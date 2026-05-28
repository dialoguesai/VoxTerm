"""Tests for ``audio.mixer.MicSystemMixer``.

Covers:
* Unit behavior (gain state, soft-clip bounds, length handling).
* Side-by-side measurements vs the old naive ``np.clip(mic + sys, -1, 1)``
  on synthetic mixtures, asserting concrete improvements:
    - peak amplitude stays in range without hard-clip square-waving
    - mic-to-system SNR improves during simultaneous loud speech
    - percentage of clipped samples drops to ~0

These metrics are also printed by the ``test_mixer_improvement_report``
test so the numbers can be lifted straight into the PR description.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from audio.mixer import MicSystemMixer, MixerConfig  # noqa: E402


SAMPLE_RATE = 16000


# ── synthetic generators ────────────────────────────────────


def _speech_like(duration_s: float, amplitude: float, seed: int = 0) -> np.ndarray:
    """Voiced-ish signal: 220 Hz fundamental + harmonics + amplitude envelope.

    Not real speech, but mimics the broadband, slowly-varying RMS profile
    well enough that RMS-driven sidechain detectors react to it the same way.
    """
    rng = np.random.RandomState(seed)
    t = np.linspace(0.0, duration_s, int(duration_s * SAMPLE_RATE), dtype=np.float32)
    base = (
        0.6 * np.sin(2 * np.pi * 220.0 * t)
        + 0.3 * np.sin(2 * np.pi * 440.0 * t)
        + 0.1 * np.sin(2 * np.pi * 880.0 * t)
    )
    # Slow envelope (8 Hz tremolo) so RMS isn't pathologically flat.
    envelope = 0.5 + 0.5 * np.abs(np.sin(2 * np.pi * 3.0 * t))
    noise = 0.05 * rng.randn(len(t)).astype(np.float32)
    sig = (base * envelope + noise).astype(np.float32)
    # Normalize then scale.
    peak = float(np.max(np.abs(sig))) or 1.0
    return (sig * (amplitude / peak)).astype(np.float32)


def _silence(duration_s: float) -> np.ndarray:
    return np.zeros(int(duration_s * SAMPLE_RATE), dtype=np.float32)


def _chunkify(audio: np.ndarray, chunk_size: int = 1024) -> list[np.ndarray]:
    return [audio[i:i + chunk_size] for i in range(0, len(audio), chunk_size)]


def _naive_mix(mic: np.ndarray, sys: np.ndarray) -> np.ndarray:
    """Reproduce the previous mixer behavior verbatim for comparison."""
    n = min(len(mic), len(sys))
    summed = mic[:n] + sys[:n]
    return np.clip(summed, -1.0, 1.0).astype(np.float32)


def _rms(x: np.ndarray) -> float:
    if len(x) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))


def _peak(x: np.ndarray) -> float:
    if len(x) == 0:
        return 0.0
    return float(np.max(np.abs(x)))


def _pct_clipped(x: np.ndarray, eps: float = 1e-4) -> float:
    """Percentage of samples sitting at ±1 (hard clip floor/ceiling)."""
    if len(x) == 0:
        return 0.0
    near_max = np.abs(np.abs(x) - 1.0) < eps
    return 100.0 * float(np.mean(near_max))


# ── unit tests ───────────────────────────────────────────


class TestSoftClip:
    def test_keeps_signal_in_unit_interval(self):
        x = np.linspace(-3.0, 3.0, 4096, dtype=np.float32)
        out = MicSystemMixer._soft_clip(x, knee=0.9)
        assert _peak(out) <= 1.0 + 1e-6
        # Linear region preserved.
        within = np.abs(x) <= 0.85
        assert np.allclose(out[within], x[within])

    def test_knee_one_is_hard_clip(self):
        x = np.array([-2.0, -0.5, 0.0, 0.5, 2.0], dtype=np.float32)
        out = MicSystemMixer._soft_clip(x, knee=1.0)
        assert out.tolist() == [-1.0, -0.5, 0.0, 0.5, 1.0]


class TestSilentMicPasses:
    def test_system_passes_through_when_mic_silent(self):
        mixer = MicSystemMixer()
        sys_sig = _speech_like(2.0, amplitude=0.4, seed=1)
        mic_sig = _silence(2.0)
        # Run a few chunks so the gain settles.
        for mc, sc in zip(_chunkify(mic_sig), _chunkify(sys_sig)):
            mixer.mix(mc, sc)
        # Sys gain should have stayed at (or recovered to) ~1.0.
        assert mixer.sys_gain > 0.99


class TestDuckingEngages:
    def test_loud_mic_ducks_system(self):
        mixer = MicSystemMixer()
        mic_sig = _speech_like(2.0, amplitude=0.6, seed=2)
        sys_sig = _speech_like(2.0, amplitude=0.5, seed=3)
        for mc, sc in zip(_chunkify(mic_sig), _chunkify(sys_sig)):
            mixer.mix(mc, sc)
        # After 2 seconds of loud mic, gain should approach the duck target
        # (10**(-12/20) ≈ 0.25). Allow generous margin for one-pole envelope.
        assert mixer.sys_gain < 0.5
        assert mixer.sys_gain > 0.2


class TestReleaseRecovery:
    def test_gain_recovers_after_mic_quiet(self):
        mixer = MicSystemMixer()
        loud_mic = _speech_like(1.0, amplitude=0.6, seed=4)
        sys_sig = _speech_like(3.0, amplitude=0.5, seed=5)
        # Phase 1: loud mic ducks the system.
        for mc, sc in zip(_chunkify(loud_mic), _chunkify(sys_sig[:SAMPLE_RATE])):
            mixer.mix(mc, sc)
        ducked = mixer.sys_gain
        assert ducked < 0.5
        # Phase 2: silent mic for 2 seconds → release.
        quiet_mic = _silence(2.0)
        for mc, sc in zip(
            _chunkify(quiet_mic), _chunkify(sys_sig[SAMPLE_RATE:]),
        ):
            mixer.mix(mc, sc)
        assert mixer.sys_gain > 0.9


class TestUnequalLengthChunks:
    def test_mic_longer_than_sys(self):
        mixer = MicSystemMixer()
        mic = np.full(1024, 0.1, dtype=np.float32)
        sys = np.full(512, 0.2, dtype=np.float32)
        out = mixer.mix(mic, sys)
        assert len(out) == 1024
        # Tail of mic passes through unchanged.
        assert np.allclose(out[512:], 0.1)

    def test_sys_longer_than_mic(self):
        mixer = MicSystemMixer()
        mic = np.full(512, 0.1, dtype=np.float32)
        sys = np.full(1024, 0.4, dtype=np.float32)
        out = mixer.mix(mic, sys)
        assert len(out) == 1024


class TestMixChunksList:
    def test_chunk_lists_aligned(self):
        mixer = MicSystemMixer()
        mics = [np.full(1024, 0.05, dtype=np.float32) for _ in range(4)]
        syss = [np.full(1024, 0.2, dtype=np.float32) for _ in range(4)]
        out = mixer.mix_chunks(mics, syss)
        assert len(out) == 4
        for chunk in out:
            assert chunk.dtype == np.float32
            assert _peak(chunk) <= 1.0

    def test_mic_tail_appended(self):
        mixer = MicSystemMixer()
        mics = [np.full(1024, 0.1, dtype=np.float32) for _ in range(3)]
        syss = [np.full(1024, 0.2, dtype=np.float32)]
        out = mixer.mix_chunks(mics, syss)
        assert len(out) == 3
        # Last two are mic-only.
        assert np.allclose(out[1], 0.1)
        assert np.allclose(out[2], 0.1)


# ── comparative / "show the improvement" tests ──────────────


class TestImprovementVsNaive:
    """Construct adversarial inputs (both sources hot) and show the
    new mixer beats the old ``clip(sum, -1, 1)`` on objective metrics."""

    def _build_simultaneous_speech(self) -> tuple[np.ndarray, np.ndarray]:
        # Both at 0.7 amplitude: naive sum hits ±1.4 and clips heavily.
        mic = _speech_like(3.0, amplitude=0.7, seed=10)
        sys = _speech_like(3.0, amplitude=0.7, seed=11)
        return mic, sys

    def test_peak_amplitude_bounded(self):
        mic, sys = self._build_simultaneous_speech()
        naive = _naive_mix(mic, sys)
        mixer = MicSystemMixer()
        out = np.concatenate(mixer.mix_chunks(_chunkify(mic), _chunkify(sys)))
        assert _peak(out) <= 1.0 + 1e-6
        assert _peak(naive) <= 1.0 + 1e-6  # naive is post-clip too
        # Naive hits the wall by definition; mixer should stay safely below.
        assert _peak(out) < 0.999

    def test_hard_clipping_reduced(self):
        mic, sys = self._build_simultaneous_speech()
        naive = _naive_mix(mic, sys)
        mixer = MicSystemMixer()
        out = np.concatenate(mixer.mix_chunks(_chunkify(mic), _chunkify(sys)))
        naive_clip_pct = _pct_clipped(naive)
        mixer_clip_pct = _pct_clipped(out)
        # Naive mixer should produce non-trivial clipping on this input.
        assert naive_clip_pct > 1.0, (
            f"baseline did not clip enough to be a meaningful test "
            f"(got {naive_clip_pct:.2f}%)"
        )
        # New mixer should essentially eliminate it.
        assert mixer_clip_pct < 0.05

    def test_mic_to_system_snr_improves(self):
        """Treat mic as 'signal' and system as 'noise'. After ducking the
        system bed, the mic's contribution to the mix should dominate more
        than it did under the naive sum.
        """
        mic, sys = self._build_simultaneous_speech()

        # SNR estimate: 20*log10(rms(mic_in_mix) / rms(sys_in_mix)).
        # We compute it by mixing the same sources twice — once treating sys=0
        # to get the "signal contribution", once treating mic=0 to get the
        # "noise contribution" — under each algorithm.
        naive_signal = _naive_mix(mic, np.zeros_like(sys))
        naive_noise = _naive_mix(np.zeros_like(mic), sys)
        naive_snr = 20.0 * math.log10(
            (_rms(naive_signal) + 1e-9) / (_rms(naive_noise) + 1e-9)
        )

        m1 = MicSystemMixer()
        m2 = MicSystemMixer()
        mixer_signal = np.concatenate(
            m1.mix_chunks(_chunkify(mic), _chunkify(np.zeros_like(sys)))
        )
        mixer_noise = np.concatenate(
            m2.mix_chunks(_chunkify(mic), _chunkify(sys))  # drive duck via mic
        )
        # Re-extract the noise component by subtracting the signal-only run
        # from the full mix.
        m3 = MicSystemMixer()
        full = np.concatenate(
            m3.mix_chunks(_chunkify(mic), _chunkify(sys))
        )
        noise_residual = full - mixer_signal[: len(full)]
        mixer_snr = 20.0 * math.log10(
            (_rms(mixer_signal) + 1e-9) / (_rms(noise_residual) + 1e-9)
        )

        # Should beat naive by at least 6 dB on this adversarial input.
        assert mixer_snr > naive_snr + 6.0, (
            f"mixer SNR {mixer_snr:.1f} dB only beats naive {naive_snr:.1f} dB "
            f"by {mixer_snr - naive_snr:.1f} dB"
        )


def test_mixer_improvement_report(capsys):
    """Prints a side-by-side comparison the PR description can quote.

    Always passes — it's a report, not a gate. Use ``pytest -s`` to view.
    """
    mic = _speech_like(3.0, amplitude=0.7, seed=42)
    sys = _speech_like(3.0, amplitude=0.7, seed=43)

    naive = _naive_mix(mic, sys)
    mixer = MicSystemMixer()
    new = np.concatenate(mixer.mix_chunks(_chunkify(mic), _chunkify(sys)))

    # SNR (mic vs sys contribution) under each.
    naive_sig = _naive_mix(mic, np.zeros_like(sys))
    naive_nse = _naive_mix(np.zeros_like(mic), sys)
    naive_snr = 20 * math.log10((_rms(naive_sig) + 1e-9) / (_rms(naive_nse) + 1e-9))

    m1 = MicSystemMixer()
    mix_sig = np.concatenate(
        m1.mix_chunks(_chunkify(mic), _chunkify(np.zeros_like(sys)))
    )
    m2 = MicSystemMixer()
    mix_full = np.concatenate(m2.mix_chunks(_chunkify(mic), _chunkify(sys)))
    mix_nse = mix_full - mix_sig[: len(mix_full)]
    mix_snr = 20 * math.log10((_rms(mix_sig) + 1e-9) / (_rms(mix_nse) + 1e-9))

    print()
    print("┌─ mic+system mixer: naive vs MicSystemMixer ───────────────────┐")
    print(f"│  metric             │  naive (clip-sum)  │  MicSystemMixer  │")
    print(f"│  peak amplitude     │  {_peak(naive):>16.4f}  │  {_peak(new):>14.4f}  │")
    print(f"│  hard-clipped (% )  │  {_pct_clipped(naive):>16.3f}  │  {_pct_clipped(new):>14.3f}  │")
    print(f"│  output RMS         │  {_rms(naive):>16.4f}  │  {_rms(new):>14.4f}  │")
    print(f"│  mic/system SNR (dB)│  {naive_snr:>16.2f}  │  {mix_snr:>14.2f}  │")
    print(f"│  final sys gain     │  {'n/a':>16}  │  {mixer.sys_gain:>14.4f}  │")
    print("└───────────────────────────────────────────────────────────────┘")
    captured = capsys.readouterr()
    # Re-emit so -s mode shows it.
    sys_out = captured.out
    print(sys_out, end="")
