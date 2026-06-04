"""Export a VoxTerm event log to an LLM-agent-optimized transcript.

A pure, replayable function of the ``*-events.jsonl`` stream (the same stream glass
tails) — no audio, no live state. Produces two files alongside the session:

  <stem>-agent.md    human + LLM readable: YAML front-matter, an orientation line,
                     then one speaker-attributed, timestamped turn per paragraph.
  <stem>-agent.json  the typed, lossless companion the .md is rendered from.

Run:
  python -m glass.export [events.jsonl] [--out-dir DIR]
  # with no path, exports the newest *-events.jsonl in VoxTerm's live dir.

Design notes:
- ``confidence`` in the event stream is a TIER STRING ("", "high", "medium", "new"),
  never a float — we keep it verbatim and derive a single ``confidence_uncertain``
  boolean, avoiding the "+confidence -> NaN" trap a numeric coercion would hit.
- Timestamps prefer a turn's ``audio_offset`` (true seconds into the recording, set
  by the headless file transcriber) and fall back to wall-clock ``t - session_start``
  for live TUI sessions.
- Speaker labels from diarization are voice CLUSTERS, not verified identities; that
  caveat is stated once in the front-matter ``notes`` rather than marked on every
  turn. Per-turn markers flag only genuinely-more-uncertain turns.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

EXPORT_VERSION = 1
DOC_KIND = "voxterm-transcript"

# Marker render order (bare tokens; rendered "[token]" in Markdown).
_MARKER_ORDER = ["~", "new-voice", "overlap", "peer"]
_MARKER_LEGEND = {
    "[~]": "low/medium-confidence or unattributed speaker — treat the label as uncertain",
    "[new-voice]": "first appearance of an unrecognized speaker",
    "[overlap]": "overlapping speech in this segment",
    "[peer]": "turn arrived from a remote P2P peer",
}
_CONFIDENCE_LEGEND = {"recognized": "high", "suggested": "medium", "new_voice": "new", "asserted": ""}


def load_events(path: Path) -> list[dict]:
    """Read a JSONL event log; skip blank/garbled lines rather than failing."""
    events = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "kind" in obj:
                events.append(obj)
    return events


def _num(v, default=0.0):
    """Tolerant numeric parse from an untrusted event log: returns a FINITE float
    (or ``default``) — never raises, never returns NaN/Infinity. Garbled or
    non-numeric ``t``/``audio_offset`` values degrade to the default instead of
    crashing the whole export."""
    if isinstance(v, bool) or v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _coerce_sid(raw) -> int:
    """Speaker ids are ints in real VoxTerm but strings ("S0") in the synth harness;
    normalize either to an int without crashing."""
    if isinstance(raw, bool) or raw is None:
        return 0
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            m = re.search(r"\d+", raw)
            return int(m.group()) if m else 0
    return 0


def _fmt_hms(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def _fmt_ts(seconds: float, sep: str) -> str:
    """Subtitle timestamp "HH:MM:SS<sep>mmm" (sep="," for SRT, "." for WebVTT).
    Clamps a non-finite/negative value to 0 so a garbled offset can never produce
    an invalid cue."""
    s = _num(seconds, 0.0)
    if s < 0:
        s = 0.0
    ms_total = int(round(s * 1000))
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    sec, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d}{sep}{ms:03d}"


def _iso_local(unix_ts: float) -> str:
    """Local, timezone-aware ISO 8601 (e.g. 2026-06-04T09:14:07-07:00)."""
    return datetime.fromtimestamp(unix_ts).astimezone().isoformat(timespec="seconds")


def build(events: list[dict], *, session_id: str, source_stream: str) -> dict:
    """Reduce the event stream to the typed export object (the JSON sidecar shape)."""
    starts = [e for e in events if e.get("kind") == "session" and e.get("phase") == "start"]
    ends = [e for e in events if e.get("kind") == "session" and e.get("phase") == "end"]
    text_evs = [e for e in events if e.get("kind") in ("text", "peer_text")]

    has_anchor = bool(starts or text_evs)
    t0 = _num(starts[0].get("t")) if starts else (_num(text_evs[0].get("t")) if text_evs else 0.0)
    incomplete = not ends
    t_end = _num(ends[-1].get("t"), t0) if ends else (_num(events[-1].get("t"), t0) if events else t0)
    model = starts[0].get("model", "") if starts else ""
    language = (starts[0].get("language", "") if starts else "") or "auto"
    party = any(e.get("kind") == "party" and e.get("on") for e in events)

    turns = []
    has_audio_time = any("audio_offset" in e for e in text_evs)
    audio_end_max = 0.0
    raw_audio_ends: list[float | None] = []
    for idx, e in enumerate(text_evs):
        is_peer = e.get("kind") == "peer_text"
        if "audio_offset" in e:
            t_off = _num(e.get("audio_offset"))
            audio_end_max = max(audio_end_max, _num(e.get("audio_end"), t_off))
        else:
            t_off = max(0.0, _num(e.get("t"), t0) - t0)
        # raw per-turn audio_end (None if absent) feeds the t_offset_end post-pass below.
        raw_audio_ends.append(_num(e.get("audio_end"), None) if "audio_end" in e else None)
        conf = "" if is_peer else e.get("confidence", "")
        if isinstance(conf, bool) or conf is None:
            conf = ""

        # confidence is normally a TIER STRING ("", high, medium, new). Some producers
        # (the synth harness, older logs) emit a float; handle both honestly. A
        # non-finite float (NaN/Inf) is sanitized to "" so it can never reach the JSON
        # sidecar as an invalid literal — and is treated as uncertain, not certain.
        numeric = isinstance(conf, (int, float))
        non_finite = numeric and not math.isfinite(conf)
        if non_finite:
            conf, numeric = "", False
        tier_uncertain = (conf < 0.5) if numeric else (conf in ("medium", "new") or non_finite)

        sid = 0 if is_peer else _coerce_sid(e.get("speaker_id", 0))
        overlap = bool(e.get("overlap", False)) and not is_peer
        peer_name = e.get("peer") if is_peer else None
        speaker = e.get("speaker", "") or ("(unattributed)" if (not is_peer and sid == 0) else "")

        markers = []
        if conf == "new":
            markers.append("new-voice")
        if tier_uncertain:
            markers.append("~")
        if not is_peer and sid == 0:
            markers.append("~")
        if overlap:
            markers.append("overlap")
        if is_peer:
            markers.append("peer")
        markers = [m for m in _MARKER_ORDER if m in markers]  # dedup + canonical order
        uncertain = tier_uncertain or (sid == 0 and not is_peer)

        turns.append({
            "index": idx,
            "t_offset": round(t_off, 2),
            "t_offset_hms": _fmt_hms(t_off),
            "t_unix": _num(e.get("t"), None),
            "speaker_id": sid,
            "speaker": speaker,
            "text": (e.get("text", "") or "").strip(),
            "confidence": conf,
            "confidence_uncertain": uncertain,
            "overlap": overlap,
            "peer": is_peer,
            "peer_name": peer_name,
            "markers": markers,
        })

    # SHARED CONTRACT: every turn gains a finite t_offset_end (end seconds). Source
    # priority: this turn's audio_end -> the NEXT turn's t_offset -> t_offset + 2.0.
    for i, t in enumerate(turns):
        start = t["t_offset"]
        ae = raw_audio_ends[i]
        if ae is not None and math.isfinite(ae):
            end = ae
        elif i + 1 < len(turns):
            end = turns[i + 1]["t_offset"]
        else:
            end = start + 2.0
        if not math.isfinite(end):
            end = start + 2.0
        t["t_offset_end"] = round(end, 2)

    if has_audio_time:
        # cover every turn, including any wall-clock-fallback turn, so a turn's
        # offset can never exceed the reported duration.
        duration_seconds = round(max([audio_end_max] + [t["t_offset"] for t in turns], default=0.0))
    else:
        duration_seconds = round(max(0.0, t_end - t0))

    # Distinct speakers (local by id; peers by name+label) with turn counts.
    local_labels: dict[int, Counter] = {}
    local_counts: Counter = Counter()
    peer_counts: Counter = Counter()
    for t in turns:
        if t["peer"]:
            peer_counts[(t["peer_name"], t["speaker"])] += 1
        else:
            local_counts[t["speaker_id"]] += 1
            local_labels.setdefault(t["speaker_id"], Counter())[t["speaker"]] += 1
    speakers = []
    for sid in sorted(local_counts):
        label = local_labels[sid].most_common(1)[0][0] if local_labels[sid] else ""
        speakers.append({"id": sid, "label": label or "(unattributed)",
                         "peer": False, "peer_name": None, "turns": local_counts[sid]})
    for (pname, plabel), n in sorted(peer_counts.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        speakers.append({"id": 0, "label": plabel, "peer": True, "peer_name": pname, "turns": n})

    # notes are computed once here (the single source) and stored on the doc; render_md
    # consumes doc["_notes"] and render_json strips it. Ordering: caveat first.
    notes = ["Speaker labels are diarization voice-clusters (e.g. \"Speaker 1\"), not verified "
             "identities; treat attribution as approximate unless a turn is otherwise marked."]
    if not has_anchor:
        notes.append("No parsable session or transcript events were found — this log may be "
                     "empty or corrupt; timestamps are unavailable.")
    if incomplete:
        notes.append("Session end event missing — duration/ended_at are approximate.")
    if has_audio_time:
        notes.append("Timestamps are true offsets into the recorded audio.")

    return {
        "voxterm_export_version": EXPORT_VERSION,
        "kind": DOC_KIND,
        "session": {
            "id": session_id,
            "started_at": (_iso_local(t0) if has_anchor else None),
            "started_at_unix": (t0 if has_anchor else None),
            "ended_at": (_iso_local(t_end) if (has_anchor and not incomplete) else None),
            "duration_seconds": duration_seconds,
            "duration_hms": _fmt_hms(duration_seconds),
            "source": "VoxTerm",
            "source_stream": source_stream,
            "model": model,
            "language": language,
            "party": party,
            "incomplete": incomplete,
            "audio_relative_time": has_audio_time,
        },
        "speakers": speakers,
        "turns": turns,
        "_notes": notes,
    }


def _yaml_scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return str(v)
    return json.dumps(str(v), ensure_ascii=False)  # JSON string == valid YAML double-quoted


def render_md(doc: dict) -> str:
    s = doc["session"]
    out = ["---"]
    out.append(f"voxterm_export_version: {EXPORT_VERSION}")
    out.append(f"kind: {DOC_KIND}")
    out.append(f"session_id: {_yaml_scalar(s['id'])}")
    out.append(f"date: {s['started_at'][:10] if s['started_at'] else 'null'}")
    out.append(f"started_at: {_yaml_scalar(s['started_at'])}")
    out.append(f"ended_at: {_yaml_scalar(s['ended_at'])}")
    out.append(f"duration: {_yaml_scalar(s['duration_hms'])}")
    out.append(f"duration_seconds: {s['duration_seconds']}")
    out.append("source: VoxTerm")
    out.append(f"source_stream: {_yaml_scalar(s['source_stream'])}")
    out.append(f"model: {_yaml_scalar(s['model'])}")
    out.append(f"language: {_yaml_scalar(s['language'])}")
    out.append(f"party: {_yaml_scalar(s['party'])}")
    out.append(f"audio_relative_time: {_yaml_scalar(s['audio_relative_time'])}")
    out.append("speakers:")
    for sp in doc["speakers"]:
        bits = f"id: {sp['id']}, label: {_yaml_scalar(sp['label'])}, turns: {sp['turns']}, peer: {_yaml_scalar(sp['peer'])}"
        if sp["peer"]:
            bits += f", peer_name: {_yaml_scalar(sp['peer_name'])}"
        out.append(f"  - {{ {bits} }}")
    out.append(f"turns: {len(doc['turns'])}")
    out.append("confidence_legend: { " + ", ".join(f"{k}: {_yaml_scalar(v)}" for k, v in _CONFIDENCE_LEGEND.items()) + " }")
    out.append("markers:")
    for k, v in _MARKER_LEGEND.items():
        out.append(f"  {_yaml_scalar(k)}: {_yaml_scalar(v)}")
    out.append("notes:")
    for n in doc.get("_notes", []):
        out.append(f"  - {_yaml_scalar(n)}")
    out.append("---")
    out.append("")

    n_sp = len(doc["speakers"])
    out.append(f"> VoxTerm session — {n_sp} speaker(s), {len(doc['turns'])} turns, "
               f"{s['duration_hms']}. Timestamps are [mm:ss] "
               f"{'into the recorded audio' if s['audio_relative_time'] else 'from session start'}. "
               f"Markers: [~] uncertain attribution, [overlap] overlapping speech, "
               f"[new-voice] first appearance, [peer] remote peer. "
               f"Speaker labels are voice clusters, not verified identities.")
    out.append("")
    out.append("## Transcript")
    out.append("")
    for t in doc["turns"]:
        if t["peer"]:
            who = f"**{t['speaker']}** (peer: {t['peer_name']})"
        elif t["speaker_id"]:
            who = f"**{t['speaker']}** (#{t['speaker_id']})"
        else:
            who = "**(unattributed)**"
        line = f"[{t['t_offset_hms']}] {who}: {t['text']}"
        if t["markers"]:
            line += "  " + " ".join(f"[{m}]" for m in t["markers"])
        out.append(line)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_json(doc: dict) -> str:
    d = {k: v for k, v in doc.items() if k != "_notes"}
    # allow_nan=False: refuse to emit NaN/Infinity literals (invalid JSON for strict
    # parsers). build() already sanitizes them, so this is a fail-loud backstop.
    return json.dumps(d, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def _cue_label(t: dict) -> str:
    """Cue speaker label: peers render "name (peer)"; locals use the speaker label."""
    name = t.get("speaker") or "(unattributed)"
    if t.get("peer"):
        return f"{name} (peer)"
    return name


def _cue_times(t: dict) -> tuple[float, float]:
    """(start, end) seconds for a cue, guaranteeing end > start (min 0.5s span)."""
    start = _num(t.get("t_offset"), 0.0)
    end = _num(t.get("t_offset_end"), start)
    if end <= start:
        end = start + 0.5
    return start, end


def to_srt(doc: dict) -> str:
    """Render doc turns as SubRip (SRT): 1-indexed cues, "HH:MM:SS,mmm" times.
    Empty-text turns are skipped; cue label = speaker, cue text = turn text."""
    blocks = []
    idx = 0
    for t in doc.get("turns", []):
        text = (t.get("text") or "").strip()
        if not text:
            continue
        idx += 1
        start, end = _cue_times(t)
        blocks.append(
            f"{idx}\n"
            f"{_fmt_ts(start, ',')} --> {_fmt_ts(end, ',')}\n"
            f"{_cue_label(t)}: {text}\n"
        )
    return "\n".join(blocks)


def to_vtt(doc: dict) -> str:
    """Render doc turns as WebVTT: "WEBVTT" header, "HH:MM:SS.mmm" times.
    Empty-text turns are skipped; cue label = speaker, cue text = turn text."""
    blocks = ["WEBVTT\n"]
    for t in doc.get("turns", []):
        text = (t.get("text") or "").strip()
        if not text:
            continue
        start, end = _cue_times(t)
        blocks.append(
            f"{_fmt_ts(start, '.')} --> {_fmt_ts(end, '.')}\n"
            f"{_cue_label(t)}: {text}\n"
        )
    return "\n".join(blocks)


def export(events_path: Path, out_dir: Path | None = None) -> tuple[Path, Path, Path, Path]:
    events = load_events(events_path)
    stem = events_path.stem
    if stem.endswith("-events"):
        stem = stem[: -len("-events")]
    doc = build(events, session_id=stem, source_stream=events_path.name)  # notes set on doc
    if not doc["turns"]:
        print(f"warning: no transcript turns found in {events_path.name} "
              f"({len(events)} events parsed) — writing an empty export", file=sys.stderr)

    out_dir = out_dir or events_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{stem}-agent.md"
    json_path = out_dir / f"{stem}-agent.json"
    srt_path = out_dir / f"{stem}-agent.srt"
    vtt_path = out_dir / f"{stem}-agent.vtt"
    md_path.write_text(render_md(doc), encoding="utf-8")
    json_path.write_text(render_json(doc), encoding="utf-8")
    srt_path.write_text(to_srt(doc), encoding="utf-8")
    vtt_path.write_text(to_vtt(doc), encoding="utf-8")
    return md_path, json_path, srt_path, vtt_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Export a VoxTerm event log to an LLM-agent transcript.")
    ap.add_argument("events", nargs="?", help="path to a *-events.jsonl (default: newest in VoxTerm live dir)")
    ap.add_argument("--out-dir", default=None, help="output dir (default: alongside the events file)")
    ap.add_argument("--format", choices=["md", "json", "srt", "vtt", "all"], default="all",
                    help="which artifact(s) to write/print (default: all = md+json+srt+vtt)")
    args = ap.parse_args(argv)

    if args.events:
        events_path = Path(args.events)
    else:
        # Default: newest *-events.jsonl in VoxTerm's live dir (self-contained — no glass dep).
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from config import LIVE_DIR
            live = Path(LIVE_DIR)
        except Exception:
            live = Path.home() / ".local" / "share" / "voxterm" / ".live"
        cands = sorted(live.glob("*-events.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if live.exists() else []
        if not cands:
            print("error: no events file given and none found in the live dir", file=sys.stderr)
            return 2
        events_path = cands[0]
    if not events_path.exists():
        print(f"error: no such file: {events_path}", file=sys.stderr)
        return 2

    md_path, json_path, srt_path, vtt_path = export(events_path, Path(args.out_dir) if args.out_dir else None)
    # export() always writes md+json+srt+vtt (the default behavior); --format only
    # controls which of those written paths are printed.
    want = {"md", "json", "srt", "vtt"} if args.format == "all" else {args.format}
    if "md" in want:
        print(f"agent transcript: {md_path}")
    if "json" in want:
        print(f"json sidecar:     {json_path}")
    if "srt" in want:
        print(f"srt subtitles:    {srt_path}")
    if "vtt" in want:
        print(f"vtt subtitles:    {vtt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
