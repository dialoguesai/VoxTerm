# Dialogues / Topos integration

VoxTerm can optionally push transcript segments to a user's Topos dataset via the Dialogues Control Plane **Grant Access** flow. Transcription and diarization remain local; only text segments you explicitly enable are sent.

## Quick start

1. Run the TUI: `just run` (or `python -m tui.app`).
2. Press **D** → **Attach Dialogues account** (opens browser, loopback OAuth on `127.0.0.1:8741`).
3. Press **D** again → enable **Send transcripts to Topos**.
4. Press **R** to record. Segments batch to the Control Plane (`app_ingest`) and your connected Topos engine ingests them.

Attach and push are separate: attaching stores credentials; push must be toggled on.

## Architecture

```
VoxTerm  ──POST /v1/ingestion/app_ingest──▶  Control Plane
                                                  │
                                                  └── WebSocket app_ingest ──▶  Topos engine
```

VoxTerm never talks to the Topos engine directly. The Control Plane validates the attach token and routes records to the engine connected for your `resource_id`.

## Batching

ToposClient flushes when any of:

- **30 segments** accumulated
- **60 seconds** since batch start
- Recording stops and transcription finishes
- App quit (`close()`)

One flush = one `app_ingest` POST with multiple records.

## Control Plane prerequisites

Before attach works in production:

1. **Grant Access app** registered (`public_pkce`, `messages:write`, redirect `http://127.0.0.1:8741/oauth/callback`).
2. **Source** `voxterm_transcripts` published and installed on the user's engine (`ui_stream`, parser `voxterm.transcript.v1`).
3. **Engine** connected to the same Control Plane as VoxTerm posts to.
4. **Cloudflare / WAF** allows the VoxTerm User-Agent on the CP zone.

See the monorepo skill `.cursor/skills/dialogues-create-app` for app registration.

## Development

```bash
just setup    # venv + editable install
just test     # includes dialogues unit tests
just run      # start TUI
```

Tests: `tests/test_dialogues_*.py`, `tests/test_topos_client.py`.
