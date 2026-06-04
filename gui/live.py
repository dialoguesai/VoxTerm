"""Live / near-real-time transcription of an IN-PROGRESS recording.

Tails the raw PCM of the WAV that `arecord` (or VoxTerm) is writing and transcribes
each new *speech* window with VoxTerm's own engine, printing "[mm:ss] text" as the
conversation happens. It reads the FILE, not the microphone, so it never contends with
the recorder — you can run it alongside a live capture.

    python -m gui.live ROOM.wav [--model fw-base] [--interval 10] [--max-seconds N]

Text-only by default (no diarization) to stay light + low-latency; the full
speaker-attributed transcript comes from the post-stop pipeline. fw-base is the default
for speed (≈realtime-capable on CPU). Stops after --max-seconds (0 = until interrupted).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np  # noqa: E402

import config  # noqa: E402
from gui.transcribe import _get_engines, _fmt_hms  # reuse the cached engine + time fmt  # noqa: E402
from gui.stabilize import PartialStabilizer  # noqa: E402

SR = config.SAMPLE_RATE
_WAV_HEADER = 44  # bytes; raw little-endian s16 PCM follows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Live transcription of an in-progress WAV.")
    ap.add_argument("wav")
    # fw-base is the light live default where it exists (Linux/Intel mac); Apple Silicon
    # has no fw-* keys, so fall back to the platform default (MLX).
    ap.add_argument("--model", default=("fw-base" if "fw-base" in config.AVAILABLE_MODELS else config.DEFAULT_MODEL))
    ap.add_argument("--language", default="en")
    ap.add_argument("--interval", type=float, default=10.0, help="seconds between passes")
    ap.add_argument("--max-seconds", type=float, default=0.0, help="stop after N seconds (0 = until interrupted)")
    args = ap.parse_args(argv)

    wav = Path(args.wav)
    if not wav.exists():
        print(f"error: no such file: {wav}", file=sys.stderr)
        return 2

    print(f"[live] loading {args.model} …", flush=True)
    tr, vad, _diar = _get_engines(args.model, args.language)
    print(f"[live] transcribing {wav.name} every {args.interval:.0f}s — reads the file, not the mic", flush=True)

    f = open(wav, "rb")
    f.seek(_WAV_HEADER)
    buf = np.zeros(0, dtype=np.float32)
    abs_start = 0          # absolute sample index of buf[0]
    started = time.time()
    n_lines = 0
    stab = PartialStabilizer()   # volatile preview of the still-in-progress tail
    partial_len = 0              # chars of the in-place partial line currently on screen

    def clear_partial():
        nonlocal partial_len
        if partial_len:
            sys.stdout.write("\r" + " " * partial_len + "\r")
            sys.stdout.flush()
            partial_len = 0

    try:
        while True:
            time.sleep(args.interval)
            data = f.read()                      # everything appended since last read
            if data:
                n = len(data) - (len(data) % 2)  # whole int16 samples only
                if n:
                    buf = np.concatenate([buf, np.frombuffer(data[:n], dtype="<i2").astype(np.float32) / 32768.0])
            # Finalize speech segments that are followed by trailing silence (i.e. not the
            # still-in-progress tail). Keep the unfinalized tail in buf for next pass.
            if len(buf) >= SR * 2:
                segs = vad.get_speech_segments(buf, min_speech_ms=500, min_silence_ms=300, max_speech_s=6.0)
                tail_guard = len(buf) - int(SR * 0.6)   # leave the last ~0.6s as "still talking"
                consumed = 0
                for (s, e) in segs:
                    if e > tail_guard:
                        break                     # this segment may still be growing — wait
                    out = tr.transcribe(buf[s:e])
                    txt = (out.get("text") or "").strip()
                    if txt:
                        clear_partial()            # erase the in-place partial before a final line
                        print(f"  [{_fmt_hms((abs_start + s) / SR)}] {txt}", flush=True)
                        n_lines += 1
                    consumed = e
                if consumed:
                    abs_start += consumed
                    buf = buf[consumed:]
                    stab.reset()                   # finalized → the volatile tail restarts clean
            # in-place volatile partial of the still-in-progress tail
            if len(buf) >= int(SR * 0.4):
                st = stab.push((tr.transcribe(buf).get("text") or "").strip())
                line = (st["stable"] + (" " if st["stable"] and st["volatile"] else "") + st["volatile"]).strip()
                if line:
                    s_out = f"  ~ [{_fmt_hms(abs_start / SR)}] {line}"
                    sys.stdout.write("\r" + s_out + " " * max(0, partial_len - len(s_out)))
                    sys.stdout.flush()
                    partial_len = len(s_out)
                else:
                    clear_partial()
            if args.max_seconds and (time.time() - started) >= args.max_seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        clear_partial()
        f.close()
    print(f"[live] stopped — {n_lines} live lines transcribed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
