# Fork guide — Dialogues AI (`dialoguesai/VoxTerm`)

This tree is a **fork of [dmarzzz/VoxTerm](https://github.com/dmarzzz/VoxTerm)** with Dialogues Grant Access + Topos `app_ingest` integration. Use this document when publishing to `github.com/dialoguesai/VoxTerm`.

## Upstream relationship

- **Base:** upstream `main` @ `64521b6` (or later merges as needed).
- **Fork additions:** `dialogues/` package, Dialogues TUI screens, config keys, tests, `justfile`, `FORK.md`, `docs/dialogues-integration.md`.
- **License:** MIT — retain upstream copyright; Dialogues modifications may add a copyright line (see `LICENSE`).

Keep a remote to upstream for selective merges:

```bash
git remote add upstream https://github.com/dmarzzz/VoxTerm.git
git fetch upstream
```

## Publish checklist

### 1. Create the GitHub repo

```bash
# After committing all Dialogues work locally:
git remote set-url origin https://github.com/dialoguesai/VoxTerm.git
git push -u origin main
```

### 2. URLs already pointed at `dialoguesai/VoxTerm`

| File | What changed |
|------|----------------|
| `pyproject.toml` | Homepage / Repository |
| `install.sh` | `REPO` default via `VOXTERM_GITHUB_REPO` |
| `README.md` | Clone / install / dev commands |

Override install repo without editing files:

```bash
VOXTERM_GITHUB_REPO=dialoguesai/VoxTerm curl -fsSL .../install.sh | bash
```

### 3. First release assets

Before `install.sh` works for end users, publish a GitHub **release** with:

- Source tarball (automatic on release)
- **`install.sh`** attached or served from `releases/latest/download/install.sh` (match upstream release workflow or add one)

**ONNX speaker model:** copy the `onnx-models` release asset from upstream or re-upload:

```bash
# Until dialoguesai hosts it, users can keep upstream default:
export VOXTERM_ONNX_GITHUB_REPO=dmarzzz/VoxTerm
```

After mirroring `eres2net_large.onnx` to `dialoguesai/VoxTerm` releases tag `onnx-models`, set default in `audio/diarization/onnx_embedder.py` or document the env var.

### 4. Control Plane registration

| Item | Value |
|------|--------|
| App id | `voxterm` (or set `DIALOGUES_APP_ID`) |
| Auth | `public_pkce` |
| Scopes | `messages:write` |
| Redirect | `http://127.0.0.1:8741/oauth/callback` |
| Source id | `voxterm_transcripts` |
| Parser | `voxterm.transcript.v1` |

Allowlist User-Agent: `dialoguesai/voxterm-grant/0.3.0` on the CP Cloudflare zone.

### 5. CI

Add or enable `.github/workflows/dialogues-tests.yml` (pytest for Dialogues tests). Extend to full suite when ready.

### 6. Privacy messaging

README now states: local-first by default; Topos push is **opt-in** via **D** menu. Update marketing if needed.

## Code review summary (Dialogues integration)

### Strengths

- Clean separation: `dialogues/` mirrors hivemind client pattern (batch, flush, credentials).
- OAuth uses PKCE + loopback; credentials stored with `0600`, atomic write.
- Attach ≠ push; persisted in `ConfigStore`.
- HTTP User-Agent on all CP calls (Cloudflare 1010 fix).
- Unit tests for PKCE, HTTP, OAuth callback, screen lifecycle, ToposClient batching.

### Fixes applied in this fork

| Issue | Fix |
|-------|-----|
| Per-segment engine pipeline spam | Deferred flush after recording stop + transcription idle |
| `Q` quit not working | Priority bindings for `q`/`Q`; transcript panel non-focusable |
| Re-attach cleared push preference | Restore `enable_push()` from config after attach |
| `record_id` collision within same minute | Include seconds in timestamp |
| Missing footer **D** | Added violet Dialogues shortcut in footer + telemetry |

### Remaining follow-ups (non-blocking)

- Web GUI / dictation / mobile: no Dialogues path yet (TUI only).
- No CI in upstream; `just test` is dev-only until workflow is enabled on fork.
- OAuth state mismatch logged but exchange continues (acceptable for loopback dev).
- Ingest has no retry queue; failed batches are dropped with log warning.
- `voxterm -D` (dictation launcher) vs **D** (Dialogues) — different entry points; document only.

## Syncing from upstream

```bash
git fetch upstream
git merge upstream/main   # or rebase; resolve conflicts in tui/app.py, config.py
just test
```

Conflict hotspots: `tui/app.py`, `config.py`, `pyproject.toml`, `README.md`.
