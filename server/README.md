# VoxTerm collector

A small FastAPI service that receives transcripts (and optional audio) from
VoxTerm clients and indexes them in SQLite. Used by the Shape Rotator program
to aggregate transcripts across deployed devices.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r server/requirements.txt
python -m server.app                       # default: 127.0.0.1:8000
python -m server.app --port 9000           # custom port, still loopback
```

`VOXTERM_SERVER_DATA_DIR` controls where blobs and the SQLite index live
(default: `./server-data/`).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | Liveness check |
| POST | `/v1/transcripts` | Upload (multipart: `metadata`, `transcript`, optional `audio`); idempotent on `session_id` |
| GET | `/v1/transcripts?limit=50&offset=0` | List recent uploads |
| GET | `/v1/transcripts/{session_id}` | Fetch one record |
| GET | `/v1/transcripts/{session_id}/audio` | Stream the WAV |

## Storage layout

```
$VOXTERM_SERVER_DATA_DIR/
├── transcripts.db                  # SQLite index
└── uploads/
    └── 2026-05-03_120000/
        ├── transcript.md
        ├── audio.wav               # optional
        └── metadata.json           # sidecar so the index is reconstructible
```

## Tests

```bash
pytest server/tests/
```

## v1 hard limits — DO NOT host this publicly as-is

This server starts with **no auth, no TLS, no rate limiting, no audit logging**.
It's wired for the Shape Rotator team to run on a single machine and shovel
transcripts in from a few trusted devices on the same network.

It refuses to bind to a non-loopback address unless you set
`VOXTERM_ALLOW_PUBLIC=1`. Don't set that flag until you've worked through this
checklist:

- [ ] **Auth.** Add a bearer-token header check (per-device tokens, issued by an
      admin endpoint or out-of-band). The client already has plumbing for
      `remote_upload_token` (currently absent because v1 has no auth).
- [ ] **TLS.** Terminate TLS at a reverse proxy (Caddy / nginx). Don't rely on
      uvicorn's TLS for production.
- [ ] **Rate limiting.** Per-device or per-IP, e.g. via a reverse proxy or
      `slowapi`.
- [ ] **Storage backend.** The filesystem layout is fine for a few hundred
      sessions; swap for object storage (S3 / R2) once volumes grow or when you
      need offsite durability.
- [ ] **Audit logging.** Log every upload (who, when, sizes) to a separate
      append-only log; today's request log is uvicorn-default and not durable.
- [ ] **Backups.** Schedule a backup of `transcripts.db` and a sync of `uploads/`.
- [ ] **Retention policy.** Decide how long to keep audio (it's expensive) and
      automate deletion.

Anything past that is hosting-specific (which PaaS, which DNS, which TLS cert).

## Why same-repo?

The client lives at `tui/remote_upload.py`. Keeping the server next to it means
the protocol is owned by one project and changes ship as one PR cycle. If the
collector grows past Shape Rotator scope, split it out then.
