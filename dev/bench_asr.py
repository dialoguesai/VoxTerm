#!/usr/bin/env python3
"""ASR model benchmark for VoxTerm (Apple Silicon / MLX).

Drives the *actual* VoxTerm transcriber classes (so it exercises the real
integration path, padding + filters included) and reports word error rate
(WER) and speed (latency, real-time factor) across the configured models.

Ground-truth audio is synthesised on the fly with macOS `say` across a few
voices for light acoustic variety, then resampled to 16 kHz mono — the same
format the live pipeline feeds the models. TTS audio is clean, so absolute
WER is optimistic; treat the numbers as a *relative* ranking between models
on identical input, plus honest speed measurements.

Usage:
    .venv/bin/python -m dev.bench_asr                 # default model set
    .venv/bin/python -m dev.bench_asr --models nemotron-streaming parakeet-1.1b qwen3-0.6b
    .venv/bin/python -m dev.bench_asr --json out.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

# Make repo root importable when run as a file.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import AVAILABLE_MODELS, FASTER_WHISPER_MODELS, PARAKEET_MODELS, QWEN3_MODELS  # noqa: E402

# (reference_text, say_voice)
SENTENCES = [
    ("The quick brown fox jumps over the lazy dog near the riverbank.", "Daniel"),
    ("She sells seashells by the seashore on a bright summer morning.", "Samantha"),
    ("Artificial intelligence is transforming how we build modern software.", "Alex"),
    ("Please remember to back up your files before installing the update.", "Karen"),
    ("The conference call is scheduled for next Tuesday afternoon.", "Daniel"),
    ("Our quarterly revenue exceeded expectations across every region.", "Samantha"),
    ("He carefully measured the ingredients before mixing the batter.", "Alex"),
    ("Climate scientists are studying ocean currents and rising temperatures.", "Karen"),
    ("The new transcription engine runs entirely offline on your laptop.", "Daniel"),
    ("Could you forward me the meeting notes from yesterday's review?", "Samantha"),
    ("Voice recognition accuracy depends heavily on background noise levels.", "Alex"),
    ("The library opens at nine and closes at six on weekdays.", "Karen"),
]

SR = 16000
_WORD_RE = re.compile(r"[a-z0-9']+")


def normalize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def wer(ref: str, hyp: str) -> tuple[float, int, int]:
    """Word error rate via Levenshtein distance over normalized tokens.

    Returns (wer, edit_distance, ref_word_count).
    """
    r = normalize(ref)
    h = normalize(hyp)
    n, m = len(r), len(h)
    if n == 0:
        return (0.0 if m == 0 else 1.0, m, 0)
    # DP edit distance.
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if r[i - 1] == h[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[m] / n, prev[m], n


def synth_clips(cache: Path) -> list[dict]:
    cache.mkdir(parents=True, exist_ok=True)
    clips = []
    for i, (text, voice) in enumerate(SENTENCES):
        wav = cache / f"clip_{i:02d}.wav"
        if not wav.exists():
            aiff = cache / f"clip_{i:02d}.aiff"
            subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
            # 16 kHz mono PCM16 — the live capture format.
            subprocess.run(
                ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                 str(aiff), str(wav)],
                check=True,
            )
            aiff.unlink(missing_ok=True)
        audio, sr = sf.read(wav, dtype="float32")
        assert sr == SR, sr
        clips.append({"text": text, "audio": audio, "dur": len(audio) / SR})
    return clips


def make_transcriber(model_key: str, repo: str, language: str):
    from audio.transcriber import (
        FasterWhisperTranscriber,
        ParakeetTranscriber,
        Qwen3Transcriber,
        WhisperTranscriber,
    )
    if model_key in QWEN3_MODELS:
        return Qwen3Transcriber(model=repo, language=language)
    if model_key in PARAKEET_MODELS:
        return ParakeetTranscriber(model=repo, language=language)
    if model_key in FASTER_WHISPER_MODELS:
        return FasterWhisperTranscriber(model=repo, language=language)
    return WhisperTranscriber(model=repo)


def bench_model(model_key: str, clips: list[dict], language: str) -> dict:
    repo = AVAILABLE_MODELS[model_key]
    print(f"\n=== {model_key}  ({repo}) ===", flush=True)
    t = make_transcriber(model_key, repo, language)

    t0 = time.perf_counter()
    t.load()
    load_s = time.perf_counter() - t0
    print(f"  loaded in {load_s:.1f}s", flush=True)

    # Warm-up (first call pays graph-compile / lazy-eval costs).
    t.transcribe(clips[0]["audio"])
    if hasattr(t, "_init_dedup"):
        t._init_dedup()  # reset dedup so warm-up doesn't suppress clip 0

    total_edits = total_words = 0
    total_audio = total_proc = 0.0
    per_clip = []
    for c in clips:
        s = time.perf_counter()
        out = t.transcribe(c["audio"])
        proc = time.perf_counter() - s
        hyp = out.get("text", "")
        w, edits, words = wer(c["text"], hyp)
        total_edits += edits
        total_words += words
        total_audio += c["dur"]
        total_proc += proc
        per_clip.append({"ref": c["text"], "hyp": hyp, "wer": w, "proc_s": proc})
        print(f"  [{w*100:5.1f}% WER {proc:5.2f}s] {hyp[:70]!r}", flush=True)

    agg_wer = total_edits / total_words if total_words else 0.0
    rtf = total_proc / total_audio if total_audio else 0.0
    return {
        "model": model_key,
        "repo": repo,
        "load_s": round(load_s, 2),
        "wer": round(agg_wer, 4),
        "rtf": round(rtf, 4),
        "audio_s": round(total_audio, 2),
        "proc_s": round(total_proc, 2),
        "avg_latency_s": round(total_proc / len(clips), 3),
        "clips": per_clip,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+",
                    default=["parakeet-0.6b", "parakeet-1.1b",
                             "qwen3-0.6b", "qwen3-1.7b", "small"])
    ap.add_argument("--language", default="en")
    ap.add_argument("--json", type=str, default=None)
    ap.add_argument("--cache", default="/tmp/voxterm_bench")
    args = ap.parse_args()

    from audio.transcriber import configure_mlx_memory
    configure_mlx_memory()

    clips = synth_clips(Path(args.cache))
    total_audio = sum(c["dur"] for c in clips)
    print(f"{len(clips)} clips, {total_audio:.1f}s total audio", flush=True)

    results = []
    for mk in args.models:
        if mk not in AVAILABLE_MODELS:
            print(f"  skip unknown model {mk}", flush=True)
            continue
        try:
            results.append(bench_model(mk, clips, args.language))
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  FAILED {mk}: {e}", flush=True)

    # Summary table.
    print("\n" + "=" * 78)
    print(f"{'model':22s} {'WER%':>7s} {'RTF':>7s} {'avg lat':>9s} {'load s':>8s}")
    print("-" * 78)
    for r in sorted(results, key=lambda r: r["wer"]):
        print(f"{r['model']:22s} {r['wer']*100:7.2f} {r['rtf']:7.3f} "
              f"{r['avg_latency_s']:8.2f}s {r['load_s']:7.1f}s")
    print("=" * 78)
    print("WER on clean macOS-`say` TTS (optimistic; use as relative ranking). "
          "RTF = proc_time / audio_time (lower = faster).")

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
