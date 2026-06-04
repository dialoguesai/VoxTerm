# VoxTerm GUI

A small web control app over VoxTerm's own engine. Hit a button to record, stop,
transcribe, and diarize; review the result; and export an AI-ready transcript — all
from a browser tab (including your phone, on your own network).

It is a thin control surface, not a reimplementation. Recording uses VoxTerm's
`audio.capture.AudioCapture`; transcription drives the same transcriber + Silero VAD +
diarizer + `EventLogger` the TUI uses; the AI export is a pure function of the same
`events.jsonl` stream that the TUI emits. Nothing about the speech pipeline is
duplicated here.

## What it does (v1)

A single linear flow:

1. **Record** — pick a model + language, hit the button, talk.
2. **Stop** — captured audio is written to a WAV.
3. **Transcribe + diarize** — runs in the background; a progress bar tracks it.
4. **Export** — automatically produces an AI-ready `-agent.md` + `-agent.json`, plus
   `.srt` / `.vtt` subtitles.
5. **History** — every past session is listed in the sidebar; click to reopen.
6. **Rename** — relabel a diarized speaker; the rename flows into your copy/download.

Review extras: a speaker legend, per-turn timestamps and uncertainty markers, and
client-side exports built from the loaded session (so your renames are included) —
**Copy for AI**, **Summarize for AI** (transcript prefixed with a ready-to-paste LLM
summarization task), and `.md` / `.json` / `.srt` / `.vtt` downloads. The subtitle
output is byte-identical to the server-written files.

It's also a **PWA** — install it to your phone/desktop home screen for an app-like,
offline-capable shell. Your model + language picks are remembered (localStorage), and
keyboard shortcuts work (**Space** or **R** to record, **Esc** to close the sidebar),
with focus rings and aria-live status for accessibility.

## How to run

```bash
python -m gui.server
# -> http://127.0.0.1:8740   (loopback only)
```

By default it binds `127.0.0.1` — reachable only from this machine.

Optional env:

| Var | Default | Effect |
|-----|---------|--------|
| `VOXTERM_GUI_PORT` | `8740` | listen port |
| `VOXTERM_GUI_LAN` | unset | `=1` binds `0.0.0.0` and requires a token (see below) |
| `VOXTERM_GUI_TOKEN` | auto | set your own LAN token; otherwise one is generated |

### Phone / LAN access

The app records a real room, so exposing it to the network is gated behind a token
that must be present on **every** `/api/*` call.

```bash
VOXTERM_GUI_LAN=1 python -m gui.server
```

On start it prints the exact URL to open from your phone:

```
http://<this-host>:8740/?token=<TOKEN>
```

Open that URL on a device on the same network. The page reads `?token=…` from the URL
and attaches it to every API request and the status stream automatically. Without a
valid token, every `/api/*` call returns `401`.

## Privacy and security model

- **Loopback by default.** No token, no network exposure — only this machine can reach it.
- **Token-gated LAN.** With `VOXTERM_GUI_LAN=1`, every `/api/*` request must carry the
  token (header `X-VoxTerm-Token`, `Authorization: Bearer …`, or `?token=…`), checked with
  a constant-time compare. This guards both starting a recording of the room and reading
  past transcripts.
- **Transcription is fully local.** Models run on this machine via VoxTerm's engine.
  Nothing audio-related leaves the host.
- **No audio in any network payload.** The API moves JSON status, option lists, and text
  artifacts only — never audio. WAVs stay on disk under `~/voxterm-live/`.
- **Strict CSP.** Same-origin only; no external scripts, fonts, images, or connections.
  (`style-src` allows `'unsafe-inline'` for a few computed styles — the level ring, the
  progress bar, speaker color dots — all from local, escaped data.) Plus
  `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.
- **No path traversal.** Static files resolve within `static/` only; session lookups
  reject non-bare stems and restrict any `dir` to a known session directory.

## Files

| File | Role |
|------|------|
| `server.py` | stdlib `http.server` — serves the UI, a tiny JSON API, and an SSE status stream; handles the loopback/LAN + token gate and CSP. No transcription logic. |
| `engine.py` | Control layer over VoxTerm's engine: start/stop recording (via `AudioCapture`), the background transcribe+export job, live level/status, and session-history listing/reads. |
| `transcribe.py` | Headless transcription: a WAV (or in-memory buffer) → a faithful `events.jsonl` + `-transcript.md`, reusing VoxTerm's transcriber, Silero VAD, diarizer, and `EventLogger`. Also a CLI: `python -m gui.transcribe ROOM.wav`. |
| `export.py` | Pure, replayable export of an `events.jsonl` → `-agent.md` / `.json` / `.srt` / `.vtt`. No audio, no live state. CLI: `python -m gui.export [events.jsonl] [--format md\|json\|srt\|vtt\|all]`. |
| `static/index.html`, `static/app.js`, `static/style.css`, `static/sw.js`, `static/manifest.webmanifest`, `static/icon*` | The self-hosted single-page UI + the PWA service worker, manifest, and icons. |

### Outputs

Recordings and their artifacts land in `~/voxterm-live/`. The history sidebar also reads
VoxTerm's own session and live dirs. Per session:

| Artifact | What it is |
|----------|------------|
| `<ts>-gui.wav` | the captured audio (local only) |
| `<ts>-events.jsonl` | the canonical VoxTerm event stream (the same one the TUI emits / glass tails) |
| `<ts>-transcript.md` | human-readable transcript with timestamps + speaker labels |
| `<ts>-agent.md` | AI-ready transcript: YAML front-matter, marker legend, one speaker-attributed, timestamped turn per line |
| `<ts>-agent.json` | typed, lossless companion the `-agent.md` is rendered from (each turn carries `t_offset`/`t_offset_end`) |
| `<ts>-agent.srt` / `.vtt` | subtitles (SubRip / WebVTT) rendered from the per-turn timestamps |

`events.jsonl` is the source of truth: each line is one JSON object
(`{"t", "kind", …}`). The exporter is a pure reduction of that stream — `text` events
carry an `audio_offset`/`audio_end` so timestamps are true offsets into the recording.

### API surface

`GET /api/options` · `GET /api/status` · `GET /api/sessions` · `GET /api/session` ·
`GET /api/events` (SSE) · `POST /api/record/start` · `POST /api/record/stop` ·
`POST /api/transcribe` (transcribe an existing WAV).

## Models and languages

Models offered are VoxTerm's faster-whisper keys (`fw-tiny`, `fw-base`, `fw-small`,
`fw-medium`, `fw-large-v3`, `fw-distil-large-v3`); `fw-small` is the default. Languages
come from VoxTerm's `AVAILABLE_LANGUAGES` (default `en`). On CPU, the smaller `fw-*`
models are the practical choices.

## Scope: what this is not (yet)

v1 is deliberately the linear flow above (record → stop → transcribe → export →
history → rename). Planned fast-follows, not built here:

- **Live word-by-word streaming** during recording (v1 transcribes after stop).
- **Party / P2P** multi-device sessions (the export already understands `peer` turns).
- **Hivemind** shared/aggregated sessions.
- **Merged view** across multiple sessions.
- **Speaker profiles** (persistent cross-session identities; v1 renames are per-view).
- **Tauri native desktop + iOS/Android** app (the PWA already covers home-screen install;
  Tauri is the native / app-store step, wrapping this same web UI).
