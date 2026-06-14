"""Tests for glass.export — the LLM-agent transcript exporter.

Pytest-style; also runnable standalone (`python tests/test_export.py`) via the
__main__ runner at the bottom, so it works without pytest installed.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import re

from export import (build, export, render_md, render_json, to_srt, to_vtt,
                    _coerce_sid, _fmt_ts)
from _timefmt import fmt_hms


def _evs():
    """A realistic REAL-schema event stream (int speaker_ids, tier-string confidence)."""
    t0 = 1_780_000_000.0
    return [
        {"t": t0, "kind": "session", "phase": "start", "model": "fw-base", "language": "en"},
        {"t": t0, "kind": "recording", "on": True},
        {"t": t0 + 1, "kind": "speaker", "speaker_id": 1, "label": "Speaker 1", "color": "#00ffcc"},
        {"t": t0 + 1, "kind": "text", "speaker": "Speaker 1", "speaker_id": 1, "color": "#00ffcc",
         "text": "Let's ship the exporter.", "confidence": "high", "overlap": False, "audio_offset": 1.5, "audio_end": 4.0},
        {"t": t0 + 2, "kind": "text", "speaker": "Speaker 2", "speaker_id": 2, "color": "#ff8c42",
         "text": "Is the new voice handled?", "confidence": "new", "overlap": False, "audio_offset": 5.0, "audio_end": 7.0},
        {"t": t0 + 3, "kind": "text", "speaker": "Speaker 1", "speaker_id": 1, "color": "#00ffcc",
         "text": "Yes, and overlaps too.", "confidence": "", "overlap": True, "audio_offset": 7.5, "audio_end": 9.0},
        {"t": t0 + 4, "kind": "text", "speaker": "", "speaker_id": 0, "color": "",
         "text": "mumble in the back", "confidence": "", "overlap": False, "audio_offset": 9.5, "audio_end": 10.0},
        {"t": t0 + 5, "kind": "peer_text", "peer": "laptop-2", "speaker": "Sam", "text": "Joining from the other room."},
        {"t": t0 + 6, "kind": "session", "phase": "end"},
    ]


def _doc():
    return build(_evs(), session_id="2026-06-04_120000", source_stream="x-events.jsonl")


def test_turn_count_and_kinds():
    d = _doc()
    assert len(d["turns"]) == 5  # 4 text + 1 peer_text (non-content events excluded)
    assert d["voxterm_export_version"] == 1
    assert d["kind"] == "voxterm-transcript"


def test_high_confidence_unmarked():
    t = _doc()["turns"][0]
    assert t["confidence"] == "high" and t["markers"] == [] and t["confidence_uncertain"] is False


def test_new_voice_marked_uncertain():
    t = _doc()["turns"][1]
    assert "new-voice" in t["markers"] and "~" in t["markers"] and t["confidence_uncertain"] is True


def test_overlap_marked():
    t = _doc()["turns"][2]
    assert "overlap" in t["markers"]
    # confidence "" with a real speaker id is NOT per-turn marked uncertain
    assert t["confidence_uncertain"] is False


def test_unattributed_marked():
    t = _doc()["turns"][3]
    assert t["speaker_id"] == 0 and t["speaker"] == "(unattributed)"
    assert "~" in t["markers"] and t["confidence_uncertain"] is True


def test_peer_turn():
    t = _doc()["turns"][4]
    assert t["peer"] is True and t["peer_name"] == "laptop-2" and t["speaker"] == "Sam"
    assert t["speaker_id"] == 0 and t["markers"] == ["peer"]


def test_audio_offset_preferred_over_wallclock():
    d = _doc()
    assert d["session"]["audio_relative_time"] is True
    assert d["turns"][0]["t_offset"] == 1.5 and d["turns"][0]["t_offset_hms"] == "00:02"
    # duration from max audio_end, not wall-clock session span
    assert d["session"]["duration_seconds"] == 10


def test_speaker_grouping():
    d = _doc()
    locals_ = {s["id"]: s for s in d["speakers"] if not s["peer"]}
    assert locals_[1]["turns"] == 2 and locals_[1]["label"] == "Speaker 1"
    assert locals_[2]["turns"] == 1
    assert any(s["peer"] and s["peer_name"] == "laptop-2" for s in d["speakers"])


def test_numeric_confidence_robust():
    # synth/older logs emit a float confidence; <0.5 must be uncertain, not crash
    evs = [{"t": 1.0, "kind": "session", "phase": "start", "model": "m", "language": "en"},
           {"t": 2.0, "kind": "text", "speaker": "a", "speaker_id": 1, "text": "hi", "confidence": 0.40, "overlap": False},
           {"t": 3.0, "kind": "session", "phase": "end"}]
    t = build(evs, session_id="s", source_stream="x")["turns"][0]
    assert t["confidence_uncertain"] is True and "~" in t["markers"]


def test_coerce_sid_handles_strings():
    assert _coerce_sid(3) == 3 and _coerce_sid("S2") == 2 and _coerce_sid("7") == 7
    assert _coerce_sid("nope") == 0 and _coerce_sid(None) == 0 and _coerce_sid(True) == 0


def test_incomplete_session():
    evs = [{"t": 1.0, "kind": "session", "phase": "start", "model": "m", "language": "en"},
           {"t": 2.0, "kind": "text", "speaker": "a", "speaker_id": 1, "text": "hi", "confidence": "", "overlap": False}]
    d = build(evs, session_id="s", source_stream="x")
    assert d["session"]["incomplete"] is True and d["session"]["ended_at"] is None


def test_render_md_structure():
    d = _doc()
    d["_notes"] = ["note"]
    md = render_md(d)
    assert md.startswith("---\n") and "## Transcript" in md
    assert "[peer]" in md and "(unattributed)" in md and "(#1)" in md
    # exactly one turn per non-empty paragraph in the transcript body
    body = md.split("## Transcript", 1)[1]
    turn_lines = [ln for ln in body.splitlines() if ln.startswith("[")]
    assert len(turn_lines) == len(d["turns"])


def test_json_sidecar_valid():
    d = _doc()
    parsed = json.loads(render_json(d))
    assert parsed["voxterm_export_version"] == 1
    assert len(parsed["turns"]) == 5
    assert "_notes" not in parsed  # internal field must not leak into JSON


def test_export_round_trip_files():
    tmp = Path(tempfile.mkdtemp())
    ev = tmp / "2026-06-04_120000-events.jsonl"
    ev.write_text("\n".join(json.dumps(e) for e in _evs()) + "\n", encoding="utf-8")
    md_path, json_path, srt_path, vtt_path = export(ev, tmp)
    assert md_path.name == "2026-06-04_120000-agent.md"
    assert json_path.name == "2026-06-04_120000-agent.json"
    assert srt_path.name == "2026-06-04_120000-agent.srt"
    assert vtt_path.name == "2026-06-04_120000-agent.vtt"
    assert md_path.exists() and json_path.exists() and srt_path.exists() and vtt_path.exists()
    json.loads(json_path.read_text())  # valid
    assert vtt_path.read_text().startswith("WEBVTT")


def test_malformed_lines_skipped():
    tmp = Path(tempfile.mkdtemp())
    ev = tmp / "s-events.jsonl"
    ev.write_text('{"t":1,"kind":"session","phase":"start","model":"m","language":"en"}\n'
                  'not json at all\n'
                  '\n'
                  '{"t":2,"kind":"text","speaker":"a","speaker_id":1,"text":"hi","confidence":"","overlap":false}\n',
                  encoding="utf-8")
    md_path, _json, _srt, _vtt = export(ev, tmp)
    assert "hi" in md_path.read_text()


def test_fmt_hms():
    assert fmt_hms(65) == "01:05" and fmt_hms(3661) == "1:01:01" and fmt_hms(0) == "00:00"


# --- regression tests for the adversarial-review findings ---

def test_missing_t_does_not_crash():
    # garbled-but-valid-JSON lines with no 't' must not crash build() (load_events contract)
    evs = [{"kind": "session", "phase": "start", "model": "m", "language": "en"},
           {"kind": "text", "speaker": "a", "speaker_id": 1, "text": "hi", "confidence": "", "overlap": False}]
    d = build(evs, session_id="s", source_stream="x")
    assert len(d["turns"]) == 1


def test_garbled_timestamps_do_not_crash():
    evs = [{"t": "not-a-number", "kind": "session", "phase": "start", "model": "m", "language": "en"},
           {"t": "", "kind": "text", "speaker": "a", "speaker_id": 1, "text": "hi",
            "confidence": "", "overlap": False, "audio_offset": "NaN", "audio_end": None}]
    d = build(evs, session_id="s", source_stream="x")
    assert d["turns"][0]["t_offset"] == 0.0  # degraded, not crashed


def test_nonfinite_confidence_safe_json():
    nan = float("nan")
    evs = [{"t": 1.0, "kind": "session", "phase": "start", "model": "m", "language": "en"},
           {"t": 2.0, "kind": "text", "speaker": "a", "speaker_id": 1, "text": "hi", "confidence": nan, "overlap": False},
           {"t": 3.0, "kind": "session", "phase": "end"}]
    d = build(evs, session_id="s", source_stream="x")
    assert d["turns"][0]["confidence"] == ""           # NaN sanitized away
    assert d["turns"][0]["confidence_uncertain"] is True
    out = render_json(d)
    assert "NaN" not in out and "Infinity" not in out
    # strict parse: raise if any NaN/Infinity constant is present
    json.loads(out, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))


def test_empty_log_is_honest_not_1970():
    d = build([], session_id="s", source_stream="x")
    assert d["session"]["started_at"] is None          # not the Unix epoch
    assert len(d["turns"]) == 0
    assert any("empty or corrupt" in n for n in d["_notes"])
    # renders without crashing despite null started_at
    md = render_md(d)
    assert "date: null" in md


def test_build_populates_notes():
    # notes must come from build() itself (no manual _notes injection needed)
    d = build(_evs(), session_id="s", source_stream="x")
    assert d.get("_notes") and any("diarization voice-clusters" in n for n in d["_notes"])
    md = render_md(d)
    assert "notes:" in md and "diarization voice-clusters" in md


def test_duration_covers_all_turns():
    # an audio-timed session with a wall-clock-fallback turn: duration must cover it
    t0 = 1000.0
    evs = [{"t": t0, "kind": "session", "phase": "start", "model": "m", "language": "en"},
           {"t": t0 + 1, "kind": "text", "speaker": "a", "speaker_id": 1, "text": "x",
            "confidence": "", "overlap": False, "audio_offset": 5.0, "audio_end": 6.0},
           {"t": t0 + 30, "kind": "text", "speaker": "a", "speaker_id": 1, "text": "y",
            "confidence": "", "overlap": False},  # no audio_offset -> wall-clock 30s
           {"t": t0 + 31, "kind": "session", "phase": "end"}]
    d = build(evs, session_id="s", source_stream="x")
    assert d["session"]["duration_seconds"] >= max(t["t_offset"] for t in d["turns"])


def test_yaml_frontmatter_parses_with_injection_attempt():
    try:
        import yaml
    except ImportError:
        return  # no YAML parser available; skip
    evs = [{"t": 1.0, "kind": "session", "phase": "start", "model": 'weird: "model"\ninjected: x', "language": "en"},
           {"t": 2.0, "kind": "peer_text", "peer": "host: evil\nkey: 1", "speaker": 'Sam "the man"', "text": "hi"},
           {"t": 3.0, "kind": "session", "phase": "end"}]
    d = build(evs, session_id="s", source_stream="x")
    md = render_md(d)
    front = md.split("---", 2)[1]
    parsed = yaml.safe_load(front)
    assert parsed["model"] == 'weird: "model"\ninjected: x'   # value preserved, not split
    assert "injected" not in parsed                            # no key injected
    # peer name with a colon/newline round-trips inside the speakers list
    peer = next(s for s in parsed["speakers"] if s.get("peer"))
    assert peer["peer_name"] == "host: evil\nkey: 1"


# --- subtitle export (SHARED CONTRACT: t_offset_end + SRT/WebVTT) ---

def test_t_offset_end_present_and_after_start():
    d = _doc()
    for t in d["turns"]:
        assert "t_offset_end" in t
        assert isinstance(t["t_offset_end"], float)
        assert t["t_offset_end"] > t["t_offset"]


def test_t_offset_end_sources_per_contract():
    d = _doc()
    turns = d["turns"]
    # turn 0 has audio_end 4.0 -> end from audio_end
    assert turns[0]["t_offset_end"] == 4.0
    # turn 1 audio_end 7.0
    assert turns[1]["t_offset_end"] == 7.0
    # last turn (peer, no audio_end, no next) -> t_offset + 2.0
    last = turns[-1]
    assert last["t_offset_end"] == round(last["t_offset"] + 2.0, 2)


def test_t_offset_end_falls_back_to_next_start():
    # no audio_end anywhere -> each turn's end = next turn's start; last = +2.0
    t0 = 1000.0
    evs = [{"t": t0, "kind": "session", "phase": "start", "model": "m", "language": "en"},
           {"t": t0 + 1, "kind": "text", "speaker": "a", "speaker_id": 1, "text": "one",
            "confidence": "", "overlap": False},
           {"t": t0 + 5, "kind": "text", "speaker": "a", "speaker_id": 1, "text": "two",
            "confidence": "", "overlap": False},
           {"t": t0 + 9, "kind": "session", "phase": "end"}]
    d = build(evs, session_id="s", source_stream="x")
    turns = d["turns"]
    assert turns[0]["t_offset_end"] == turns[1]["t_offset"]
    assert turns[1]["t_offset_end"] == round(turns[1]["t_offset"] + 2.0, 2)
    for t in turns:
        assert t["t_offset_end"] > t["t_offset"]


def test_t_offset_end_monotonic_ish():
    # for the audio-timed turns (which carry true audio_end), starts and ends both
    # advance monotonically; each end strictly follows its own start.
    d = _doc()
    audio_turns = [t for t in d["turns"] if not t["peer"]]  # the 4 audio-timed turns
    starts = [t["t_offset"] for t in audio_turns]
    ends = [t["t_offset_end"] for t in audio_turns]
    assert starts == sorted(starts)
    assert ends == sorted(ends)
    for t in audio_turns:
        assert t["t_offset_end"] > t["t_offset"]


def test_t_offset_end_finite_on_garbled():
    evs = [{"t": "x", "kind": "session", "phase": "start", "model": "m", "language": "en"},
           {"t": "", "kind": "text", "speaker": "a", "speaker_id": 1, "text": "hi",
            "confidence": "", "overlap": False, "audio_offset": "NaN", "audio_end": "Inf"}]
    d = build(evs, session_id="s", source_stream="x")
    e = d["turns"][0]["t_offset_end"]
    import math as _m
    assert _m.isfinite(e) and e > d["turns"][0]["t_offset"]


def test_to_srt_parses():
    d = _doc()
    srt = to_srt(d)
    blocks = [b for b in srt.strip().split("\n\n") if b.strip()]
    text_turns = [t for t in d["turns"] if t["text"].strip()]
    assert len(blocks) == len(text_turns)
    ts_re = re.compile(r"^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$")
    for i, block in enumerate(blocks, start=1):
        lines = block.splitlines()
        assert int(lines[0]) == i                # sequential integer indices, 1-based
        assert ts_re.match(lines[1]), lines[1]   # valid SRT timestamp line
        assert lines[2].strip()                  # cue text present


def test_to_srt_skips_empty_text():
    evs = [{"t": 1.0, "kind": "session", "phase": "start", "model": "m", "language": "en"},
           {"t": 2.0, "kind": "text", "speaker": "a", "speaker_id": 1, "text": "   ",
            "confidence": "", "overlap": False, "audio_offset": 1.0, "audio_end": 2.0},
           {"t": 3.0, "kind": "text", "speaker": "a", "speaker_id": 1, "text": "real",
            "confidence": "", "overlap": False, "audio_offset": 2.0, "audio_end": 3.0},
           {"t": 4.0, "kind": "session", "phase": "end"}]
    d = build(evs, session_id="s", source_stream="x")
    srt = to_srt(d)
    blocks = [b for b in srt.strip().split("\n\n") if b.strip()]
    assert len(blocks) == 1
    assert blocks[0].splitlines()[0] == "1"
    assert "real" in blocks[0]


def test_to_srt_peer_label():
    d = _doc()
    srt = to_srt(d)
    assert "Sam (peer):" in srt


def test_to_vtt_header_and_separator():
    d = _doc()
    vtt = to_vtt(d)
    assert vtt.startswith("WEBVTT")
    # uses "." ms separator and never the SRT comma separator in cue times
    cue_lines = [ln for ln in vtt.splitlines() if "-->" in ln]
    assert cue_lines
    ts_re = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}$")
    for ln in cue_lines:
        assert ts_re.match(ln), ln
        assert "," not in ln


def test_subtitle_end_gt_start_min_span():
    # a turn whose end <= start must be bumped to start+0.5 in the cue
    t0 = 1000.0
    evs = [{"t": t0, "kind": "session", "phase": "start", "model": "m", "language": "en"},
           {"t": t0 + 1, "kind": "text", "speaker": "a", "speaker_id": 1, "text": "zero-span",
            "confidence": "", "overlap": False, "audio_offset": 5.0, "audio_end": 5.0}]
    d = build(evs, session_id="s", source_stream="x")
    srt = to_srt(d)
    line = [ln for ln in srt.splitlines() if "-->" in ln][0]
    start, end = line.split(" --> ")
    assert start == "00:00:05,000" and end == "00:00:05,500"


def test_to_srt_sanitizes_cue_text_no_injection():
    # a turn whose text carries a blank line + a fake "-->" timing line must NOT inject
    # extra cues or break the index sequence — it collapses to one single-line cue.
    nasty = "line one\n\nfake\n00:00:99,000 --> 00:00:99,500\ninjected"
    d = {"session": {"id": "x"}, "speakers": [], "turns": [
        {"t_offset": 1.0, "t_offset_end": 2.0, "speaker": "A", "peer": False, "peer_name": None, "text": "first"},
        {"t_offset": 2.0, "t_offset_end": 3.0, "speaker": "A", "peer": False, "peer_name": None, "text": nasty},
    ]}
    srt = to_srt(d)
    idx_lines = [ln for ln in srt.splitlines() if ln.strip().isdigit()]
    assert idx_lines == ["1", "2"]                                  # exactly two cues, sequential
    assert sum(1 for ln in srt.splitlines() if " --> " in ln) == 2  # only the 2 REAL cue timings
    assert "00:00:99,000 --> 00:00:99,500" not in srt               # injected timing line neutralized (--> -> ->)
    assert "00:00:99,000 -> 00:00:99,500" in srt                    # ...it survives as harmless cue text


def test_t_offset_end_after_start_when_offsets_out_of_order():
    # next-turn-start fallback could yield end < start on out-of-order offsets; build()
    # must still keep t_offset_end > t_offset in the JSON (matches the rendered cue).
    t0 = 1000.0
    evs = [{"t": t0, "kind": "session", "phase": "start", "model": "m", "language": "en"},
           {"t": t0 + 1, "kind": "text", "speaker": "a", "speaker_id": 1, "text": "later-offset",
            "confidence": "", "overlap": False, "audio_offset": 5.0},   # no audio_end -> uses next start
           {"t": t0 + 2, "kind": "text", "speaker": "b", "speaker_id": 2, "text": "earlier-offset",
            "confidence": "", "overlap": False, "audio_offset": 3.0, "audio_end": 4.0}]
    d = build(evs, session_id="s", source_stream="x")
    for t in d["turns"]:
        assert t["t_offset_end"] > t["t_offset"], t


def test_fmt_ts_edges():
    assert _fmt_ts(0, ",") == "00:00:00,000"
    assert _fmt_ts(0, ".") == "00:00:00.000"
    assert _fmt_ts(3661.5, ",") == "01:01:01,500"
    assert _fmt_ts(3661.5, ".") == "01:01:01.500"
    # negative/non-finite clamp to zero
    assert _fmt_ts(-5, ",") == "00:00:00,000"
    assert _fmt_ts(float("nan"), ".") == "00:00:00.000"


def test_json_sidecar_includes_t_offset_end():
    d = _doc()
    parsed = json.loads(render_json(d))
    for t in parsed["turns"]:
        assert "t_offset_end" in t


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
