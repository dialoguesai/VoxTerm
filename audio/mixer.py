"""Mic + system-audio mixer with sidechain ducking and soft-knee limiting.

The previous mix (``np.clip(mic + sys, -1, 1)``) had two problems:

1. **Hard clipping** when both sources are loud. A talking mic on top of a
   talking video produces a summed waveform that hits ±1 frequently. The
   abrupt clip introduces broad-spectrum harmonics that look like real
   audio to the VAD and ASR, hurting transcription quality on the very
   moments that matter (you talking over content).

2. **No source priority.** The mic is the speaker we actually want to
   transcribe; the system audio is background. Summing them at equal weight
   means the loudest source wins on a per-sample basis, which is usually
   not what the user wants.

This module fixes both:

* **Sidechain ducking** — when the mic is active (RMS above a small
  threshold), the system audio is attenuated by a configurable amount
  (default ~12 dB). When the mic goes quiet, the system audio recovers
  smoothly. Attack and release time constants prevent pumping.

* **Soft-knee limiter** — after summing, samples above a knee (~0.95) are
  shaped with a tanh-style curve so the output stays inside ±1 without the
  square-wave artifacts of hard clipping.

The mixer is stateful: the current ducking gain persists across chunks
so the attack/release timing is meaningful at the 1024-sample chunk rate.
Pure numpy, no SciPy. Tunable via env vars on first construction.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

from config import SAMPLE_RATE


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class MixerConfig:
    """Mixer tuning knobs. Defaults chosen for typical meeting audio."""

    sample_rate: int = SAMPLE_RATE
    # Mic RMS above which sidechain ducking engages. ~0.02 corresponds to
    # comfortable speech; bedroom-level whispers stay below.
    duck_threshold_rms: float = 0.02
    # How much to attenuate system audio while mic is active, in dB.
    duck_ratio_db: float = 12.0
    # Attack: how fast the duck clamps down once mic crosses threshold.
    # Short enough that the first syllable isn't masked.
    attack_ms: float = 5.0
    # Release: how fast system audio recovers after mic goes quiet.
    # Long enough to avoid pumping between syllables.
    release_ms: float = 180.0
    # Soft-knee threshold for the post-sum limiter. Samples with |x| > knee
    # are tanh-shaped so the output asymptotes to ±1.
    soft_clip_knee: float = 0.92

    @classmethod
    def from_env(cls) -> "MixerConfig":
        return cls(
            duck_threshold_rms=_env_float(
                "VOXTERM_MIX_DUCK_THRESHOLD", cls.duck_threshold_rms,
            ),
            duck_ratio_db=_env_float(
                "VOXTERM_MIX_DUCK_DB", cls.duck_ratio_db,
            ),
            attack_ms=_env_float(
                "VOXTERM_MIX_ATTACK_MS", cls.attack_ms,
            ),
            release_ms=_env_float(
                "VOXTERM_MIX_RELEASE_MS", cls.release_ms,
            ),
            soft_clip_knee=_env_float(
                "VOXTERM_MIX_SOFT_KNEE", cls.soft_clip_knee,
            ),
        )


class MicSystemMixer:
    """Stateful sidechain-ducking + soft-clip mixer for mic + system audio.

    Chunk-rate state machine: current system gain is held between calls so
    attack/release time constants behave correctly at the ~15 fps audio
    timer cadence used by the TUI.

    Thread-safety: not thread-safe. The TUI only calls this from the audio
    timer (main thread), so no lock is needed.
    """

    def __init__(self, cfg: MixerConfig | None = None):
        self.cfg = cfg or MixerConfig.from_env()
        # Current ducking gain applied to system audio.
        # 1.0 = no duck; ducks toward 10 ** (-duck_ratio_db / 20) when mic active.
        self._sys_gain: float = 1.0

    # ── core mixing ─────────────────────────────────────────

    def mix(self, mic: np.ndarray, sys: np.ndarray) -> np.ndarray:
        """Mix one mic chunk with one system chunk.

        Returns a float32 array; length = max(len(mic), len(sys)). When the
        chunks differ in length, the overlapping prefix is mixed and the
        tail of the longer one is appended (system tail gets current
        ducking gain so we don't introduce a discontinuity).
        """
        cfg = self.cfg
        if len(mic) == 0 and len(sys) == 0:
            return np.zeros(0, dtype=np.float32)
        n = min(len(mic), len(sys))
        if n == 0:
            # One side is empty — pass the other through (apply sys gain
            # so a sys-only chunk during active ducking stays attenuated).
            if len(mic) > 0:
                return mic.astype(np.float32, copy=False)
            return (sys * self._sys_gain).astype(np.float32, copy=False)

        mic_a = mic[:n].astype(np.float32, copy=False)
        sys_a = sys[:n].astype(np.float32, copy=False)

        # Sidechain detector: RMS of the mic chunk drives the duck decision.
        mic_rms = float(np.sqrt(np.mean(np.square(mic_a, dtype=np.float64))))
        if mic_rms >= cfg.duck_threshold_rms:
            target_gain = 10.0 ** (-cfg.duck_ratio_db / 20.0)
            tc_ms = cfg.attack_ms
        else:
            target_gain = 1.0
            tc_ms = cfg.release_ms

        # One-pole low-pass toward target_gain. alpha = 1 - exp(-T / tau).
        chunk_ms = (n / cfg.sample_rate) * 1000.0
        if tc_ms <= 0.0:
            self._sys_gain = target_gain
        else:
            alpha = 1.0 - float(np.exp(-chunk_ms / tc_ms))
            self._sys_gain += alpha * (target_gain - self._sys_gain)

        # Apply ducking and sum.
        summed = mic_a + sys_a * self._sys_gain

        # Soft-knee limiter — keep peak inside ±1 without hard-clip harmonics.
        summed = self._soft_clip(summed, cfg.soft_clip_knee)

        # Handle tails of unequal-length inputs.
        if len(mic) > n:
            return np.concatenate(
                [summed, mic[n:].astype(np.float32, copy=False)]
            )
        if len(sys) > n:
            sys_tail = sys[n:].astype(np.float32, copy=False) * self._sys_gain
            sys_tail = self._soft_clip(sys_tail, cfg.soft_clip_knee)
            return np.concatenate([summed, sys_tail])
        return summed

    def mix_chunks(
        self,
        mic_chunks: list[np.ndarray],
        sys_chunks: list[np.ndarray],
    ) -> list[np.ndarray]:
        """List interface mirroring the old static ``_mix_chunks``.

        Walks the two chunk lists in parallel; tail chunks of whichever
        list is longer pass through with the current sidechain gain
        applied to system-only tails.
        """
        out: list[np.ndarray] = []
        n = min(len(mic_chunks), len(sys_chunks))
        for i in range(n):
            out.append(self.mix(mic_chunks[i], sys_chunks[i]))
        # Mic-only tail: just append (already a clean signal, no duck needed).
        for mc in mic_chunks[n:]:
            out.append(mc.astype(np.float32, copy=False))
        # System-only tail: apply current sys_gain + soft-clip so a tail of
        # system chunks during active ducking stays attenuated consistently.
        for sc in sys_chunks[n:]:
            scaled = sc.astype(np.float32, copy=False) * self._sys_gain
            out.append(self._soft_clip(scaled, self.cfg.soft_clip_knee))
        return out

    # ── helpers ────────────────────────────────────────────

    @staticmethod
    def _soft_clip(x: np.ndarray, knee: float) -> np.ndarray:
        """In-place soft-knee limiter. Linear below ±knee, tanh-shaped above.

        Output is guaranteed to lie in ``[-1, 1]`` for any finite input.
        """
        if knee >= 1.0:
            return np.clip(x, -1.0, 1.0)
        out = x.copy()
        abs_x = np.abs(out)
        over = abs_x > knee
        if np.any(over):
            sign = np.sign(out[over])
            # Map [knee, ∞) → [knee, 1) via knee + (1-knee)*tanh((|x|-knee)/(1-knee))
            t = (abs_x[over] - knee) / (1.0 - knee)
            out[over] = sign * (knee + (1.0 - knee) * np.tanh(t))
        return out

    # ── state ──────────────────────────────────────────────

    @property
    def sys_gain(self) -> float:
        """Current sidechain gain (1.0 = pass-through, <1.0 = ducked)."""
        return self._sys_gain

    def reset(self) -> None:
        """Reset state (e.g. on recording start)."""
        self._sys_gain = 1.0
