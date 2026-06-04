"""Headless transcription for the GUI: a WAV (or an in-memory buffer) -> a faithful
VoxTerm ``events.jsonl`` + ``-transcript.md``, reusing VoxTerm's OWN engine
(transcriber + Silero VAD + diarizer + EventLogger). No reimplementation of the
transcription/diarization logic — this just drives the same components the TUI drives.

Importable from the GUI backend (``gui.server``) and runnable as a CLI:

    python -m gui.transcribe ROOM.wav [--out-dir DIR] [--model fw-base] [--language en]

Each ``text`` event carries an additive ``audio_offset``/``audio_end`` (seconds into
the recording) so the exporter shows true audio-relative timestamps; glass and other
consumers ignore the extra fields.
"""
from __future__ import annotations

import argparse
import sys
import threading
from datetime import datetime
from pathlib import Path

# VoxTerm package root (this file lives in <root>/gui/).
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np  # noqa: E402

import config  # noqa: E402
from audio.transcriber import get_transcriber  # noqa: E402
from audio.vad import SileroVAD  # noqa: E402
from audio.diarization.proxy import DiarizationProxy  # noqa: E402
from tui.events import EventLogger  # noqa: E402
from tui.app import VoxTerm  # noqa: E402  (reuse only the static _split_text_by_segments)

SR = config.SAMPLE_RATE  # 16000

# Loaded engines are cached so a second recording doesn't reload the model (hundreds
# of MB) from disk. Acquisition is serialized; the diarizer's per-session state is
# reset per run. (The GUI runs one transcription at a time.)
_ENGINE_LOCK = threading.Lock()
_TR_CACHE: dict = {}
_VAD = None
_DIAR = None


def _get_engines(model: str, language: str):
    global _VAD, _DIAR
    with _ENGINE_LOCK:
        key = (model, language)
        tr = _TR_CACHE.get(key)
        if tr is None:
            tr = get_transcriber(model, language=language)
            tr.load()
            _TR_CACHE[key] = tr
        if _VAD is None:
            _VAD = SileroVAD()
        if _DIAR is None:
            _DIAR = DiarizationProxy()
            _DIAR.load()
        return tr, _VAD, _DIAR


def load_wav_16k_mono(path: Path) -> np.ndarray:
    """Load any WAV as float32 mono @ 16 kHz (the live-capture format)."""
    import soundfile as sf
    data, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if getattr(data, "ndim", 1) > 1:
        data = data.mean(axis=1)
    if sr != SR:
        from scipy.signal import resample_poly
        data = resample_poly(data, SR, sr).astype(np.float32)
    return np.ascontiguousarray(data, dtype=np.float32)


def _fmt_hms(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def transcribe_audio(audio: np.ndarray, out_dir: Path, *, model: str = "fw-base",
                     language: str = "en", progress=None) -> dict:
    """Transcribe a float32/16k mono buffer. Returns
    {events_path, transcript_path, n_turns, n_speakers}. ``progress(frac, msg)`` is
    called 0..1 as windows complete (optional, for a live UI)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if progress:
        progress(0.02, "loading engine")
    tr, vad, diar = _get_engines(model, language)  # cached across calls
    diar.reset_session()

    session_start = datetime.now()
    base = session_start.strftime("%Y-%m-%d_%H%M%S")
    ts, n = base, 1
    while (out_dir / f"{ts}-events.jsonl").exists() or (out_dir / f"{ts}-transcript.md").exists():
        n += 1
        ts = f"{base}-{n}"
    events_path = out_dir / f"{ts}-events.jsonl"
    md_path = out_dir / f"{ts}-transcript.md"

    ev = EventLogger(events_path)
    ev.open()
    md = md_path.open("x", encoding="utf-8")
    last_sid, n_turns = 0, 0
    speakers: dict[int, str] = {}
    try:
        md.write("# VoxTerm Transcript\n\n")
        md.write(f"- **Date:** {session_start.strftime('%A, %B %d, %Y')}\n")
        md.write(f"- **Started:** {session_start.strftime('%I:%M %p')}\n")
        md.write(f"- **Model:** {model}\n")
        md.write(f"- **Language:** {config.AVAILABLE_LANGUAGES.get(language, language)}\n")
        md.write("\n---\n\n")
        ev.emit("session", phase="start", model=model, language=language)
        ev.emit("recording", on=True)

        windows = vad.get_speech_segments(audio, min_speech_ms=500, min_silence_ms=300, max_speech_s=6.0)
        total = max(1, len(windows))
        for wi, (s, e) in enumerate(windows):
            if progress:
                progress(0.05 + 0.92 * wi / total, f"segment {wi + 1}/{total}")
            clip = audio[s:e]
            out = tr.transcribe(clip)
            text = (out.get("text") or "").strip()
            if not text:
                continue
            ev.emit("vad", on=True)
            if len(clip) >= 48000:
                segments = diar.identify_segments(clip.copy())
            else:
                lbl, sid = diar.identify(clip.copy())
                segments = [(lbl, sid, 0, len(clip))]
            if not segments:
                segments = [("", 0, 0, len(clip))]
            if len(segments) > 1:
                parts = VoxTerm._split_text_by_segments(text, segments)
            else:
                parts = [(text, segments[0][0], segments[0][1])]
            for (seg_text, label, sid), (_l, _s, seg_start, seg_end) in zip(parts, segments):
                seg_text = seg_text.strip()
                if not seg_text:
                    continue
                color = diar.get_speaker_color(sid) if sid else ""
                if sid and sid not in speakers:
                    speakers[sid] = label or f"Speaker {sid}"
                audio_offset = round((s + seg_start) / SR, 2)
                audio_end = round((s + seg_end) / SR, 2)
                if sid != last_sid:
                    ev.emit("speaker", speaker_id=sid, label=label, color=color)
                    last_sid = sid
                ev.emit("text", speaker=label, speaker_id=sid, color=color, text=seg_text,
                        confidence="", overlap=False, audio_offset=audio_offset, audio_end=audio_end)
                stamp = _fmt_hms(audio_offset)
                md.write(f"**[{stamp}]** **{label}:** {seg_text}\n\n" if label else f"**[{stamp}]** {seg_text}\n\n")
                n_turns += 1
            ev.emit("vad", on=False)
    finally:
        for _c in (lambda: ev.emit("recording", on=False), lambda: ev.emit("session", phase="end"), ev.close, md.close):
            try:
                _c()
            except Exception:
                pass
    if progress:
        progress(1.0, "done")
    return {"events_path": str(events_path), "transcript_path": str(md_path),
            "n_turns": n_turns, "n_speakers": len(speakers)}


def transcribe_wav(wav_path, out_dir, *, model="fw-base", language="en", progress=None) -> dict:
    return transcribe_audio(load_wav_16k_mono(Path(wav_path)), Path(out_dir),
                            model=model, language=language, progress=progress)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Headless VoxTerm transcription of a WAV file.")
    ap.add_argument("wav")
    ap.add_argument("--out-dir", default=str(Path.home() / "voxterm-live"))
    ap.add_argument("--model", default="fw-base")
    ap.add_argument("--language", default="en")
    args = ap.parse_args(argv)
    if not Path(args.wav).exists():
        print(f"error: no such file: {args.wav}", file=sys.stderr)
        return 2

    def prog(f, m):
        print(f"  [{int(f*100):3d}%] {m}", flush=True)
    r = transcribe_wav(args.wav, args.out_dir, model=args.model, language=args.language, progress=prog)
    print(f"done: {r['n_turns']} turns, {r['n_speakers']} speaker(s)")
    print(f"EVENTS={r['events_path']}")
    print(f"TRANSCRIPT={r['transcript_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
