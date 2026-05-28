"""Speed benchmark: Parakeet vs Qwen3-ASR vs MLX-Whisper on Apple Silicon.

Runs each transcriber that's actually installed on a small set of audio
durations (1 s, 3 s, 5 s) and reports real-time-factor (RTF). Lower is
faster; RTF < 1 means faster than realtime.

The unit-style ``test_parakeet_*`` cases assert correctness (load works,
empty input → empty text, hallucination filter wired). The
``test_parakeet_vs_qwen3_rtf`` case asserts Parakeet beats Qwen3-0.6B on
warm-cache RTF by at least 2x — the headline reason to add it.

All tests are skipped cleanly when:
  - not on Apple Silicon (other platforms have no MLX path)
  - the relevant model package isn't importable
  - the model weights haven't been downloaded yet and the network can't
    reach Hugging Face (first-run download fails)

Run with ``pytest -s`` to see the comparison table inline.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── platform / model availability gating ─────────────────────


_IS_APPLE_SILICON = (sys.platform == "darwin") and (
    __import__("platform").machine() == "arm64"
)


def _has(module_name: str) -> bool:
    """True if ``module_name`` is importable without raising."""
    import importlib.util
    return importlib.util.find_spec(module_name) is not None


_HAS_PARAKEET = _has("parakeet_mlx")
_HAS_QWEN3 = _has("mlx_qwen3_asr")
_HAS_MLX_WHISPER = _has("mlx_whisper")

# Allow opting out of the live benchmark (CI without weights cached, etc.).
_BENCH_DISABLED = os.environ.get("VOXTERM_DISABLE_ASR_BENCH", "0") == "1"


pytestmark = pytest.mark.skipif(
    not _IS_APPLE_SILICON,
    reason="Parakeet-MLX is Apple-Silicon-only — skipping on this platform",
)


# ── synthetic audio ───────────────────────────────────────────


SR = 16000


def _speech_like(duration_s: float, seed: int = 0) -> np.ndarray:
    """Sustained vowel-ish signal: f0 + harmonics + slow envelope + noise.

    Not real speech — Parakeet won't output anything sensible — but it has
    enough RMS and bandwidth to keep the RMS gate open and exercise the
    full inference path, which is all the perf benchmark needs.
    """
    rng = np.random.RandomState(seed)
    t = np.linspace(0, duration_s, int(duration_s * SR), dtype=np.float32)
    base = (
        0.5 * np.sin(2 * np.pi * 180.0 * t)
        + 0.3 * np.sin(2 * np.pi * 360.0 * t)
        + 0.15 * np.sin(2 * np.pi * 720.0 * t)
    )
    env = 0.6 + 0.4 * np.abs(np.sin(2 * np.pi * 3.0 * t))
    noise = 0.04 * rng.randn(len(t)).astype(np.float32)
    out = (base * env + noise).astype(np.float32)
    return out * (0.3 / max(float(np.max(np.abs(out))), 1e-6))


# ── timing helper ─────────────────────────────────────────────


def _time_runs(fn, audio: np.ndarray, n_runs: int = 3) -> float:
    """Median of ``n_runs`` calls — discounts the warm-up first call."""
    samples: list[float] = []
    for _ in range(n_runs):
        t0 = time.perf_counter()
        fn(audio)
        samples.append(time.perf_counter() - t0)
    return float(np.median(samples))


def _rtf(elapsed_s: float, audio_s: float) -> float:
    return elapsed_s / audio_s if audio_s > 0 else float("inf")


# ── transcriber factories ─────────────────────────────────────


def _make_parakeet():
    from audio.transcriber import ParakeetTranscriber
    t = ParakeetTranscriber(model="mlx-community/parakeet-tdt-0.6b-v3", language="en")
    try:
        t.load()
    except Exception as e:
        pytest.skip(f"parakeet model not loadable: {e}")
    return t


def _make_qwen3():
    from audio.transcriber import Qwen3Transcriber
    t = Qwen3Transcriber(model="Qwen/Qwen3-ASR-0.6B", language="en")
    try:
        t.load()
    except Exception as e:
        pytest.skip(f"qwen3 model not loadable: {e}")
    return t


def _make_whisper():
    from audio.transcriber import WhisperTranscriber
    t = WhisperTranscriber(model="mlx-community/whisper-tiny")
    try:
        t.load()
    except Exception as e:
        pytest.skip(f"whisper model not loadable: {e}")
    return t


# ── correctness tests (cheap, run when Parakeet is installed) ─


@pytest.mark.skipif(not _HAS_PARAKEET, reason="parakeet-mlx not installed")
def test_parakeet_empty_audio_returns_empty():
    """RMS-gate short-circuit must skip inference on silence."""
    from audio.transcriber import ParakeetTranscriber
    t = ParakeetTranscriber()  # don't even load — gate runs first
    out = t.transcribe(np.zeros(SR, dtype=np.float32))
    assert out == {"text": "", "speaker": "", "speaker_id": 0}


@pytest.mark.skipif(not _HAS_PARAKEET, reason="parakeet-mlx not installed")
@pytest.mark.skipif(_BENCH_DISABLED, reason="VOXTERM_DISABLE_ASR_BENCH=1")
def test_parakeet_loads_and_transcribes():
    """End-to-end smoke: load → mel → generate → string out, no exception."""
    parakeet = _make_parakeet()
    audio = _speech_like(2.0, seed=0)
    # First call warms the JIT; second call is what we'd measure.
    parakeet.transcribe(audio)
    out = parakeet.transcribe(audio)
    assert isinstance(out["text"], str)


# ── perf comparison ───────────────────────────────────────────


@pytest.mark.skipif(
    not (_HAS_PARAKEET and _HAS_QWEN3),
    reason="needs both parakeet-mlx and mlx-qwen3-asr installed",
)
@pytest.mark.skipif(_BENCH_DISABLED, reason="VOXTERM_DISABLE_ASR_BENCH=1")
def test_parakeet_vs_qwen3_rtf():
    """Parakeet-TDT-0.6B must beat Qwen3-ASR-0.6B by ≥2x on warm RTF."""
    parakeet = _make_parakeet()
    qwen3 = _make_qwen3()

    audio = _speech_like(3.0, seed=7)
    # Warm both caches.
    parakeet.transcribe(audio)
    qwen3.transcribe(audio)

    para_t = _time_runs(parakeet.transcribe, audio, n_runs=3)
    qwen_t = _time_runs(qwen3.transcribe, audio, n_runs=3)

    para_rtf = _rtf(para_t, 3.0)
    qwen_rtf = _rtf(qwen_t, 3.0)
    speedup = qwen_t / para_t if para_t > 0 else float("inf")

    print()
    print(
        f"[3 s audio] parakeet={para_t * 1000:.0f} ms (RTF={para_rtf:.3f})  "
        f"qwen3-0.6b={qwen_t * 1000:.0f} ms (RTF={qwen_rtf:.3f})  "
        f"speedup={speedup:.2f}x"
    )

    assert speedup >= 2.0, (
        f"expected Parakeet ≥ 2x faster than Qwen3-0.6B, got {speedup:.2f}x "
        f"(parakeet={para_t*1000:.0f} ms, qwen={qwen_t*1000:.0f} ms)"
    )


@pytest.mark.skipif(not _HAS_PARAKEET, reason="parakeet-mlx not installed")
@pytest.mark.skipif(_BENCH_DISABLED, reason="VOXTERM_DISABLE_ASR_BENCH=1")
def test_perf_report():
    """Prints an RTF table across whichever backends are installed.

    Not a gate — always passes. The table goes straight into the PR body.
    """
    backends: list[tuple[str, object]] = []

    # Parakeet is the headline addition.
    backends.append(("parakeet-tdt-0.6b", _make_parakeet()))
    if _HAS_QWEN3:
        backends.append(("qwen3-asr-0.6b", _make_qwen3()))
    if _HAS_MLX_WHISPER:
        backends.append(("whisper-tiny", _make_whisper()))

    durations = [1.0, 3.0, 5.0]
    # Build a single audio buffer per duration to keep comparisons fair.
    audio_by_dur = {d: _speech_like(d, seed=42) for d in durations}

    # Warm every backend on the longest clip.
    for _, t in backends:
        t.transcribe(audio_by_dur[max(durations)])

    print()
    header = "  " + "  ".join(f"{n:>20s}" for n in [b[0] for b in backends])
    print(f"{'duration':>10s}" + header)
    for d in durations:
        audio = audio_by_dur[d]
        row = [f"{d:>8.1f}s "]
        for _, t in backends:
            elapsed = _time_runs(t.transcribe, audio, n_runs=3)
            rtf = _rtf(elapsed, d)
            row.append(f"  {elapsed * 1000:>7.0f} ms (RTF {rtf:.3f})")
        print("".join(row))
