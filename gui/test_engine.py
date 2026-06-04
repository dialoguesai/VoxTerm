"""Tests for gui.engine.Engine — the GUI control layer.

Covers ONLY the non-recording code paths (no mic, no model load, no
transcription): option lists, the WAV writer, session listing, artifact
resolution/reading (incl. path-traversal rejection and only_dir restriction),
and the idle status() shape.

Every Engine is constructed with out_dir=<tempdir>, and config.SESSIONS_DIR /
config.LIVE_DIR are redirected to empty temp dirs, so the tests never touch
real data or the microphone.

Pytest-style; also runnable standalone (`python test_engine.py`) via the
__main__ runner at the bottom, so it works without pytest installed.
"""
import json
import sys
import tempfile
import time
import wave
from pathlib import Path

# engine.py lives in gui/; the repo root must be importable for `config` etc.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for p in (str(_ROOT), str(_HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np

import config
import engine as engine_mod
from engine import Engine, _write_wav, _wav_header, _pcm_bytes, SR


# --- isolation helpers -------------------------------------------------------

def _isolated_engine():
    """An Engine whose out_dir is a fresh tempdir, with config.SESSIONS_DIR /
    LIVE_DIR redirected to *empty* temp dirs so _session_dirs() sees only the
    dirs we control (never the user's real ~/.local/share/voxterm)."""
    out = Path(tempfile.mkdtemp(prefix="voxeng_out_"))
    sess = Path(tempfile.mkdtemp(prefix="voxeng_sess_"))
    live = Path(tempfile.mkdtemp(prefix="voxeng_live_"))
    config.SESSIONS_DIR = str(sess)
    config.LIVE_DIR = str(live)
    return Engine(out_dir=out), out, sess, live


# --- option lists ------------------------------------------------------------

def test_models_are_valid_keys():
    eng, *_ = _isolated_engine()
    models = eng.models()
    assert isinstance(models, list) and models, "models() must be a non-empty list"
    # every offered key is a real, sorted, de-duped AVAILABLE_MODELS key
    assert models == sorted(set(models)), "models() must be sorted + de-duped"
    assert all(m in config.AVAILABLE_MODELS for m in models), f"unknown key leaked: {models}"
    # the platform's faster-whisper set is included (Linux/Intel mac)
    assert set(config.FASTER_WHISPER_MODELS).issubset(set(models))
    # optional sherpa streaming keys appear iff installed (additive, never on their own)
    assert set(models) & config.SHERPA_MODELS == config.SHERPA_MODELS


def test_languages_is_nonempty_dict():
    eng, *_ = _isolated_engine()
    langs = eng.languages()
    assert isinstance(langs, dict) and langs, "languages() must be a non-empty dict"
    assert langs.get("en") == "English"
    # returns a copy, not the live config object
    assert langs is not config.AVAILABLE_LANGUAGES


# --- WAV writer --------------------------------------------------------------

def test_write_wav_is_valid_16k_mono():
    eng, out, *_ = _isolated_engine()
    n = SR  # exactly 1 second
    audio = (np.sin(np.linspace(0, 6.28, n)) * 0.5).astype(np.float32)
    wav = out / "tone.wav"
    _write_wav(wav, audio)
    assert wav.exists()
    with wave.open(str(wav), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2          # 16-bit PCM
        assert w.getframerate() == SR == 16000
        assert w.getnframes() == n            # one frame per sample (mono)


def test_write_wav_clips_out_of_range():
    eng, out, *_ = _isolated_engine()
    audio = np.array([5.0, -5.0, 0.0], dtype=np.float32)  # beyond [-1, 1]
    wav = out / "clip.wav"
    _write_wav(wav, audio)
    with wave.open(str(wav), "rb") as w:
        assert w.getnframes() == 3
        frames = w.readframes(3)
    samples = np.frombuffer(frames, dtype="<i2")
    assert samples[0] == 32767 and samples[1] == -32767  # clipped, not wrapped


# --- streaming-WAV path (record-while-live): header + incremental write ------

def test_wav_header_is_44_bytes_and_parses():
    n = SR  # 1s of samples → 2*SR data bytes
    data = _pcm_bytes(np.zeros(n, dtype=np.float32))
    hdr = _wav_header(len(data))
    assert len(hdr) == 44
    p = Path(tempfile.mkdtemp()) / "h.wav"
    p.write_bytes(hdr + data)
    with wave.open(str(p), "rb") as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == SR
        assert w.getnframes() == n


def test_pcm_bytes_matches_write_wav_mapping():
    # _pcm_bytes must encode identically to _write_wav (same clip→int16 LE)
    audio = np.array([0.0, 0.5, -0.5, 5.0, -5.0], dtype=np.float32)
    out = Path(tempfile.mkdtemp())
    _write_wav(out / "ref.wav", audio)
    with wave.open(str(out / "ref.wav"), "rb") as w:
        ref = w.readframes(w.getnframes())
    assert _pcm_bytes(audio) == ref


def test_growing_wav_is_tailable_then_finalizes_valid():
    # Mirror start/poll/stop: placeholder header, append PCM (tailable mid-write), patch on stop.
    p = Path(tempfile.mkdtemp()) / "grow.wav"
    a = _pcm_bytes((np.ones(SR, dtype=np.float32) * 0.25))   # 1s
    b = _pcm_bytes((np.ones(SR, dtype=np.float32) * -0.25))  # 1s
    f = open(p, "wb")
    f.write(_wav_header(0)); f.flush()
    f.write(a); f.flush()
    # a tailer reading raw PCM past byte 44 sees the data already, before finalize:
    with open(p, "rb") as r:
        r.seek(44)
        assert r.read() == a
    f.write(b)
    total = len(a) + len(b)
    f.seek(0); f.write(_wav_header(total)); f.flush(); f.close()
    with wave.open(str(p), "rb") as w:
        assert w.getnframes() == 2 * SR            # both seconds present
        assert w.readframes(w.getnframes()) == a + b


# --- session history ---------------------------------------------------------

def _touch(p: Path, text: str = "x", mtime: float | None = None):
    p.write_text(text, encoding="utf-8")
    if mtime is not None:
        import os
        os.utime(p, (mtime, mtime))


def test_sessions_finds_all_artifact_kinds():
    eng, out, sess, live = _isolated_engine()
    # session A in out_dir: transcript + agent.md + agent.json
    _touch(out / "20260101-aaa-transcript.md")
    _touch(out / "20260101-aaa-agent.md")
    _touch(out / "20260101-aaa-agent.json", text="{}")
    # session B in the SESSIONS_DIR: transcript only
    _touch(sess / "20260102-bbb-transcript.md")
    items = eng.sessions()
    by_stem = {it["stem"]: it for it in items}
    assert "20260101-aaa" in by_stem and "20260102-bbb" in by_stem
    a = by_stem["20260101-aaa"]
    assert a["transcript"] == "20260101-aaa-transcript.md"
    assert a["agent_md"] == "20260101-aaa-agent.md"
    assert a["agent_json"] == "20260101-aaa-agent.json"
    b = by_stem["20260102-bbb"]
    assert b["transcript"] == "20260102-bbb-transcript.md"
    assert "agent_md" not in b and "agent_json" not in b


def test_sessions_newest_first():
    eng, out, *_ = _isolated_engine()
    _touch(out / "old-transcript.md", mtime=1_000_000.0)
    _touch(out / "new-transcript.md", mtime=2_000_000.0)
    stems = [it["stem"] for it in eng.sessions()]
    assert stems.index("new") < stems.index("old"), f"not newest-first: {stems}"


def test_sessions_empty_when_no_artifacts():
    eng, *_ = _isolated_engine()
    assert eng.sessions() == []


def test_sessions_carries_dir():
    eng, out, sess, _live = _isolated_engine()
    _touch(out / "here-transcript.md")
    _touch(sess / "there-transcript.md")
    by_stem = {it["stem"]: it for it in eng.sessions()}
    assert by_stem["here"]["dir"] == str(out)
    assert by_stem["there"]["dir"] == str(sess)


# --- artifact read / resolve -------------------------------------------------

def test_read_artifact_returns_text():
    eng, out, *_ = _isolated_engine()
    _touch(out / "s1-transcript.md", text="hello transcript")
    _touch(out / "s1-agent.md", text="# agent md")
    _touch(out / "s1-agent.json", text='{"ok": true}')
    r = eng.read_artifact("s1", "transcript")
    assert r["ok"] is True and r["text"] == "hello transcript"
    assert r["stem"] == "s1" and r["kind"] == "transcript"
    assert eng.read_artifact("s1", "agent_md")["text"] == "# agent md"
    assert json.loads(eng.read_artifact("s1", "agent_json")["text"]) == {"ok": True}


def test_read_artifact_bad_kind():
    eng, out, *_ = _isolated_engine()
    _touch(out / "s1-transcript.md")
    r = eng.read_artifact("s1", "nope")
    assert r["ok"] is False and r["error"] == "bad kind"


def test_read_artifact_not_found():
    eng, *_ = _isolated_engine()
    r = eng.read_artifact("does-not-exist", "transcript")
    assert r["ok"] is False and r["error"] == "not found"


def test_resolve_rejects_path_traversal():
    eng, out, sess, _live = _isolated_engine()
    # plant a real file we should NOT be able to reach via traversal
    _touch(out / "secret-transcript.md", text="SECRET")
    # a slash or .. in the stem must be refused outright
    assert eng._resolve("../secret", "-transcript.md") is None
    assert eng._resolve("sub/secret", "-transcript.md") is None
    assert eng._resolve("..", "-transcript.md") is None
    # and surfaced as a clean "not found" through the public API
    assert eng.read_artifact("../secret", "transcript")["ok"] is False
    assert eng.read_artifact("a/b", "transcript")["ok"] is False
    # the legitimate bare stem still resolves
    assert eng._resolve("secret", "-transcript.md") is not None


def test_resolve_honors_only_dir_restriction():
    eng, out, sess, _live = _isolated_engine()
    # same stem lives in BOTH known dirs
    _touch(out / "dup-transcript.md", text="from-out")
    _touch(sess / "dup-transcript.md", text="from-sess")
    # restricting to the SESSIONS_DIR returns that dir's copy
    p = eng._resolve("dup", "-transcript.md", only_dir=str(sess))
    assert p is not None and p.read_text() == "from-sess"
    # restricting to a dir that is NOT a known session dir -> ignored (None),
    # even though the file physically exists at that path
    bogus = Path(tempfile.mkdtemp(prefix="voxeng_bogus_"))
    _touch(bogus / "dup-transcript.md", text="from-bogus")
    assert eng._resolve("dup", "-transcript.md", only_dir=str(bogus)) is None
    # via the public API too
    r = eng.read_artifact("dup", "transcript", dir=str(bogus))
    assert r["ok"] is False and r["error"] == "not found"


def test_session_dirs_excludes_nonexistent_and_dedups():
    eng, out, sess, live = _isolated_engine()
    dirs = eng._session_dirs()
    # all returned dirs exist and are unique
    assert all(d.exists() for d in dirs)
    assert len(dirs) == len(set(dirs))
    assert out in dirs and sess in dirs and live in dirs


# --- delete_session ----------------------------------------------------------

def test_delete_session_removes_only_its_artifacts():
    eng, out, *_ = _isolated_engine()
    # every text artifact kind for the target stem
    for suf in ["-transcript.md", "-agent.md", "-agent.json", "-agent.srt", "-agent.vtt", "-events.jsonl"]:
        _touch(out / f"s1{suf}")
    # things that must SURVIVE: this session's audio, and another whole session
    _touch(out / "s1.wav", text="AUDIO")
    _touch(out / "s2-transcript.md", text="other")
    _touch(out / "s2.wav", text="OTHER AUDIO")
    r = eng.delete_session("s1")
    assert r["ok"] is True
    assert set(r["deleted"]) == {
        "s1-transcript.md", "s1-agent.md", "s1-agent.json",
        "s1-agent.srt", "s1-agent.vtt", "s1-events.jsonl",
    }, r["deleted"]
    # all six artifacts are gone
    for suf in ["-transcript.md", "-agent.md", "-agent.json", "-agent.srt", "-agent.vtt", "-events.jsonl"]:
        assert not (out / f"s1{suf}").exists()
    # audio + other session untouched
    assert (out / "s1.wav").exists() and (out / "s1.wav").read_text() == "AUDIO"
    assert (out / "s2-transcript.md").exists() and (out / "s2.wav").exists()


def test_delete_session_only_existing_files():
    eng, out, *_ = _isolated_engine()
    _touch(out / "s1-transcript.md")
    _touch(out / "s1-agent.json", text="{}")
    # the other four suffixes don't exist -> only the present two are reported/removed
    r = eng.delete_session("s1")
    assert r["ok"] is True
    assert set(r["deleted"]) == {"s1-transcript.md", "s1-agent.json"}, r["deleted"]
    assert not (out / "s1-transcript.md").exists()
    assert not (out / "s1-agent.json").exists()


def test_delete_session_missing_stem_is_ok_empty():
    eng, *_ = _isolated_engine()
    r = eng.delete_session("does-not-exist")
    assert r["ok"] is True and r["deleted"] == []


def test_delete_session_rejects_path_traversal():
    eng, out, sess, _live = _isolated_engine()
    # plant a real file we must NOT be able to reach via traversal
    _touch(out / "secret-transcript.md", text="SECRET")
    for bad in ("../secret", "a/b", "..", "sub/secret"):
        r = eng.delete_session(bad)
        assert r["ok"] is False and r["deleted"] == [], (bad, r)
    # the planted file is still there (never touched)
    assert (out / "secret-transcript.md").exists()


def test_delete_session_honors_dir_restriction():
    eng, out, sess, _live = _isolated_engine()
    # same stem lives in BOTH known dirs
    _touch(out / "dup-transcript.md", text="from-out")
    _touch(sess / "dup-transcript.md", text="from-sess")
    # restricting to SESSIONS_DIR deletes only that dir's copy
    r = eng.delete_session("dup", dir=str(sess))
    assert r["ok"] is True and r["deleted"] == ["dup-transcript.md"], r
    assert not (sess / "dup-transcript.md").exists()
    assert (out / "dup-transcript.md").exists() and (out / "dup-transcript.md").read_text() == "from-out"
    # a dir that is NOT a known session dir -> nothing resolves/deleted, even though
    # the file physically exists there
    bogus = Path(tempfile.mkdtemp(prefix="voxeng_bogus_"))
    _touch(bogus / "dup-transcript.md", text="from-bogus")
    r2 = eng.delete_session("dup", dir=str(bogus))
    assert r2["ok"] is True and r2["deleted"] == [], r2
    assert (bogus / "dup-transcript.md").exists()


def test_delete_session_never_touches_wav():
    eng, out, *_ = _isolated_engine()
    _touch(out / "rec-transcript.md")
    _touch(out / "rec.wav", text="AUDIO")
    _touch(out / "rec-agent.wav", text="NOT A TEXT ARTIFACT")  # adversarial name
    r = eng.delete_session("rec")
    assert r["deleted"] == ["rec-transcript.md"], r
    assert (out / "rec.wav").exists()
    assert (out / "rec-agent.wav").exists()  # .wav suffix is never in the artifact list


# --- status (idle) -----------------------------------------------------------

def test_status_idle_shape():
    eng, *_ = _isolated_engine()
    st = eng.status()
    assert set(st) == {"recording", "level", "elapsed", "job", "live"}
    assert st["recording"] is False
    assert st["level"] == 0.0
    assert st["elapsed"] == 0           # not recording -> zero, never time.time()
    assert st["job"] == {"state": "idle"}
    assert st["live"] == {"active": False, "wav": None, "lines": [], "partial": None}


def test_status_elapsed_zero_even_with_started_at():
    # started_at set but recording False must still report elapsed 0 (guarded)
    eng, *_ = _isolated_engine()
    eng.started_at = time.time() - 100
    assert eng.status()["elapsed"] == 0


# --- server-side export (single source of truth) -----------------------------

def test_export_session_single_source_and_renames():
    """export_session rebuilds from the events log and renders via export.py, so a download
    equals the on-disk artifact (one formatter, no client fork to drift), and renames apply."""
    import export as export_mod
    eng, out, *_ = _isolated_engine()
    stem = "20260101-zzz"
    events = [
        {"t": 0.0, "kind": "session", "phase": "start", "model": "fw-small", "language": "en"},
        {"t": 1.0, "kind": "text", "speaker": "Speaker 1", "speaker_id": 1, "color": "#5eead4",
         "text": "hello world", "confidence": "", "overlap": False, "audio_offset": 0.0, "audio_end": 1.0},
        {"t": 2.0, "kind": "text", "speaker": "Speaker 2", "speaker_id": 2, "color": "#f0566a",
         "text": "goodbye now", "confidence": "", "overlap": False, "audio_offset": 1.0, "audio_end": 2.0},
        {"t": 3.0, "kind": "session", "phase": "end"},
    ]
    ev = out / f"{stem}-events.jsonl"
    ev.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

    for kind in ("md", "json", "srt", "vtt"):           # all four formats render
        r = eng.export_session(stem, kind, renames={}, dir=None)
        assert r["ok"] and r["text"], (kind, r)

    # single source of truth: md == a direct build()+render_md of the same events
    doc = export_mod.build(export_mod.load_events(ev), session_id=stem, source_stream=ev.name)
    assert eng.export_session(stem, "md", renames={}, dir=None)["text"] == export_mod.render_md(doc)

    assert "Alice" in eng.export_session(stem, "md", renames={"1": "Alice"}, dir=None)["text"]
    assert eng.export_session(stem, "xml")["ok"] is False            # bad kind
    assert eng.export_session("does-not-exist", "md")["ok"] is False  # missing session


# --- standalone runner (no pytest needed) ------------------------------------

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
