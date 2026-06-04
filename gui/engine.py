"""GUI control layer over VoxTerm's engine.

Exposes the operations the GUI drives — start/stop recording (via VoxTerm's own
``AudioCapture``), the background transcribe+export job, and session history — as a
small thread-safe object the HTTP server calls. No transcription/diarization logic
lives here; recording reuses ``audio.capture.AudioCapture`` and the heavy lifting is
``gui.transcribe`` + ``gui.export`` (the reviewed, tested pipeline).

v1 model: record -> stop -> transcribe (robust, reuses the tested pipeline). Live
word-by-word streaming is a planned fast-follow.
"""
from __future__ import annotations

import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np  # noqa: E402

import config  # noqa: E402
from gui import transcribe, export  # noqa: E402

OUT_DIR = Path.home() / "voxterm-live"
SR = config.SAMPLE_RATE


def _write_wav(path: Path, audio: np.ndarray) -> None:
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


class Engine:
    def __init__(self, out_dir: Path = OUT_DIR):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._cap = None
        self._chunks: list[np.ndarray] = []
        self._poll_thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.recording = False
        self.level = 0.0
        self.started_at = None
        self.job = {"state": "idle"}  # idle | transcribing | done | error
        # live (near-real-time) monitor of an in-progress recording (reads the file)
        self._live = {"active": False, "wav": None, "lines": []}
        self._live_stop = threading.Event()
        self._live_thread = None

    # ---- static option lists for the UI ----
    def models(self) -> list[str]:
        # faster-whisper keys only (the CPU-usable set; qwen3 default is unusable on CPU)
        return sorted(config.FASTER_WHISPER_MODELS)

    def languages(self) -> dict:
        return dict(config.AVAILABLE_LANGUAGES)

    # ---- recording ----
    def start_recording(self) -> dict:
        with self._lock:
            if self.recording:
                return {"ok": True, "already": True}
            from audio.capture import AudioCapture
            try:
                self._cap = AudioCapture()
                self._cap.start()
            except Exception as e:  # no input device / busy / permission
                self._cap = None
                self.recording = False
                return {"ok": False, "error": f"could not open the microphone: {e}"}
            self._chunks = []
            self._stop.clear()
            self.recording = True
            self.started_at = time.time()
            self.level = 0.0
            self._poll_thread = threading.Thread(target=self._poll, daemon=True, name="gui-rec-poll")
            self._poll_thread.start()
            return {"ok": True}

    def _poll(self):
        while not self._stop.is_set():
            try:
                chunks = self._cap.drain()
            except Exception:
                chunks = []
            fresh = [np.asarray(c, dtype=np.float32) for c in chunks if c is not None and len(c)]
            if fresh:
                with self._lock:                       # serialize with stop's concat/clear
                    self._chunks.extend(fresh)
                last = fresh[-1]
                if len(last):
                    self.level = float(np.sqrt(np.mean(np.square(last))))
            time.sleep(0.066)  # ~15 Hz

    def stop_recording(self, model: str = "fw-small", language: str = "en") -> dict:
        if not self.recording:
            return {"ok": False, "error": "not recording"}
        # Signal + join the poll thread WITHOUT holding self._lock (the poll thread takes
        # the lock to append, so holding it here would deadlock). Once joined, no more
        # appends can race the final drain/concat/clear.
        self._stop.set()
        if self._poll_thread:
            self._poll_thread.join(timeout=5)
        with self._lock:
            self.recording = False
            try:
                for c in self._cap.drain():
                    if c is not None and len(c):
                        self._chunks.append(np.asarray(c, dtype=np.float32))
                self._cap.stop()
            except Exception:
                pass
            audio = np.concatenate(self._chunks).astype(np.float32) if self._chunks else np.zeros(0, dtype=np.float32)
            self._chunks = []
        if len(audio) < SR // 2:  # < 0.5s
            self.job = {"state": "error", "error": "recording too short"}
            return {"ok": False, "error": "recording too short"}
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        wav = self.out_dir / f"{ts}-gui.wav"
        _write_wav(wav, audio)
        self.job = {"state": "transcribing", "frac": 0.0, "msg": "starting", "wav": str(wav)}
        threading.Thread(target=self._do_transcribe, args=(audio, model, language, str(wav)),
                         daemon=True, name="gui-transcribe").start()
        return {"ok": True, "wav": str(wav), "seconds": round(len(audio) / SR, 1)}

    def _do_transcribe(self, audio, model, language, wav):
        try:
            def prog(frac, msg):
                self.job = {"state": "transcribing", "frac": round(frac, 3), "msg": msg, "wav": wav}
            r = transcribe.transcribe_audio(audio, self.out_dir, model=model, language=language, progress=prog)
            md_path, json_path, srt_path, vtt_path = export.export(Path(r["events_path"]), self.out_dir)
            self.job = {"state": "done", "wav": wav, **r,
                        "agent_md": str(md_path), "agent_json": str(json_path),
                        "agent_srt": str(srt_path), "agent_vtt": str(vtt_path),
                        "stem": Path(r["transcript_path"]).stem.replace("-transcript", "")}
        except Exception as e:
            self.job = {"state": "error", "error": f"{type(e).__name__}: {e}"}

    def transcribe_existing(self, wav_path: str, model: str = "fw-small", language: str = "en") -> dict:
        """Transcribe an already-recorded WAV (e.g. a prior capture) in the background."""
        p = Path(wav_path)
        if not p.exists():
            return {"ok": False, "error": "no such file"}
        self.job = {"state": "transcribing", "frac": 0.0, "msg": "starting", "wav": str(p)}
        threading.Thread(target=lambda: self._do_transcribe(transcribe.load_wav_16k_mono(p), model, language, str(p)),
                         daemon=True, name="gui-transcribe").start()
        return {"ok": True}

    def status(self) -> dict:
        return {
            "recording": self.recording,
            "level": round(self.level, 4),
            "elapsed": round(time.time() - self.started_at, 1) if (self.recording and self.started_at) else 0,
            "job": self.job,
            "live": {"active": self._live["active"], "wav": self._live["wav"],
                     "lines": self._live["lines"][-120:]},
        }

    # ---- live (near-real-time) monitor: tail an in-progress recording's file ----
    def _newest_wav(self):
        wavs = sorted(self.out_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
        return wavs[0] if wavs else None

    def live_start(self, wav: str | None = None) -> dict:
        with self._lock:
            if self._live["active"]:
                return {"ok": True, "already": True, "wav": self._live["wav"]}
            target = Path(wav) if wav else self._newest_wav()
            if not target or not target.exists():
                return {"ok": False, "error": "no recording to monitor"}
            self._live = {"active": True, "wav": str(target), "lines": []}
            self._live_stop.clear()
            self._live_thread = threading.Thread(target=self._live_loop, args=(target,), daemon=True, name="gui-live")
            self._live_thread.start()
            return {"ok": True, "wav": str(target)}

    def live_stop(self) -> dict:
        self._live_stop.set()
        if self._live_thread:
            self._live_thread.join(timeout=3)
        self._live["active"] = False
        return {"ok": True}

    def _live_loop(self, wav: Path):
        # tail raw PCM of the (still-growing) WAV, transcribe finalized speech windows
        from gui.transcribe import _get_engines, _fmt_hms
        try:
            tr, vad, _d = _get_engines("fw-base", "en")
        except Exception as e:
            self._live["lines"].append({"t": "", "text": f"(live engine error: {e})"})
            self._live["active"] = False
            return
        f = open(wav, "rb")
        f.seek(0, 2)  # tail from the CURRENT end — only NEW speech (true live, no slow backlog replay)
        abs_start = max(0, (f.tell() - 44) // 2)  # samples already recorded before we started (for timestamps)
        buf = np.zeros(0, dtype=np.float32)
        try:
            while not self._live_stop.is_set():
                self._live_stop.wait(8.0)
                data = f.read()
                if data:
                    n = len(data) - (len(data) % 2)
                    if n:
                        buf = np.concatenate([buf, np.frombuffer(data[:n], dtype="<i2").astype(np.float32) / 32768.0])
                if len(buf) >= SR * 2:
                    segs = vad.get_speech_segments(buf, min_speech_ms=500, min_silence_ms=300, max_speech_s=6.0)
                    guard = len(buf) - int(SR * 0.6)
                    consumed = 0
                    for (s, e) in segs:
                        if e > guard:
                            break
                        txt = (tr.transcribe(buf[s:e]).get("text") or "").strip()
                        if txt:
                            self._live["lines"].append({"t": _fmt_hms((abs_start + s) / SR), "text": txt})
                            self._live["lines"] = self._live["lines"][-200:]
                        consumed = e
                    if consumed:
                        abs_start += consumed
                        buf = buf[consumed:]
        finally:
            f.close()
            self._live["active"] = False

    # ---- session history ----
    def _session_dirs(self) -> list[Path]:
        dirs = [self.out_dir]
        try:
            dirs.append(Path(config.SESSIONS_DIR))
            dirs.append(Path(config.LIVE_DIR))
        except Exception:
            pass
        seen, uniq = set(), []
        for d in dirs:
            if d and d not in seen and d.exists():
                seen.add(d)
                uniq.append(d)
        return uniq

    def sessions(self) -> list[dict]:
        """All sessions across the known dirs, newest first, with which artifacts exist."""
        out = {}
        for d in self._session_dirs():
            for f in d.glob("*-transcript.md"):
                stem = f.stem[: -len("-transcript")]
                out.setdefault((d, stem), {"stem": stem, "dir": str(d), "mtime": f.stat().st_mtime})
                out[(d, stem)]["transcript"] = f.name
            for f in d.glob("*-agent.md"):
                stem = f.stem[: -len("-agent")]
                e = out.setdefault((d, stem), {"stem": stem, "dir": str(d), "mtime": f.stat().st_mtime})
                e["agent_md"] = f.name
                e["mtime"] = max(e.get("mtime", 0), f.stat().st_mtime)
            for f in d.glob("*-agent.json"):
                stem = f.stem[: -len("-agent")]
                e = out.setdefault((d, stem), {"stem": stem, "dir": str(d), "mtime": f.stat().st_mtime})
                e["agent_json"] = f.name
        items = sorted(out.values(), key=lambda x: x.get("mtime", 0), reverse=True)
        return items

    def _resolve(self, stem: str, suffix: str, only_dir: str | None = None) -> Path | None:
        # prevent traversal: stem must be a bare name
        if "/" in stem or ".." in stem:
            return None
        dirs = self._session_dirs()
        if only_dir:  # restrict to that dir IFF it's a known session dir (no traversal)
            od = Path(only_dir)
            dirs = [d for d in dirs if d == od]
        for d in dirs:
            p = d / f"{stem}{suffix}"
            if p.exists():
                return p
        return None

    # text artifacts a session owns (audio .wav is managed separately and never touched)
    _ARTIFACT_SUFFIXES = ["-transcript.md", "-agent.md", "-agent.json",
                          "-agent.srt", "-agent.vtt", "-events.jsonl"]

    def delete_session(self, stem: str, dir: str | None = None) -> dict:
        """Remove ONLY this session's text artifacts for ``stem``.

        Reuses _resolve's traversal guard (reject '/' or '..' in the stem) and resolves
        strictly within _session_dirs() (honoring the optional ``dir`` like _resolve's
        only_dir). Deletes only files that exist; never touches .wav audio or anything
        outside a known session dir. Returns the list of deleted filenames.
        """
        # SAME guard as _resolve: stem must be a bare name (no traversal)
        if "/" in stem or ".." in stem:
            return {"ok": False, "error": "bad stem", "deleted": []}
        deleted: list[str] = []
        for suffix in self._ARTIFACT_SUFFIXES:
            p = self._resolve(stem, suffix, only_dir=dir)  # resolves within known dirs only
            if p and p.is_file():
                try:
                    p.unlink()
                    deleted.append(p.name)
                except OSError:
                    pass
        return {"ok": True, "deleted": deleted}

    def read_artifact(self, stem: str, kind: str, dir: str | None = None) -> dict:
        suffix = {"transcript": "-transcript.md", "agent_md": "-agent.md", "agent_json": "-agent.json",
                  "srt": "-agent.srt", "vtt": "-agent.vtt"}.get(kind)
        if not suffix:
            return {"ok": False, "error": "bad kind"}
        p = self._resolve(stem, suffix, only_dir=dir)
        if not p:
            return {"ok": False, "error": "not found"}
        return {"ok": True, "stem": stem, "kind": kind, "path": str(p), "text": p.read_text(encoding="utf-8")}
