# VOXTERM

Local real-time voice transcription TUI with speaker diarization and P2P collaborative transcription. Runs entirely offline — no cloud APIs, no audio stored.

![platform](https://img.shields.io/badge/platform-macOS_(Apple_Silicon)-black)
![version](https://img.shields.io/badge/version-0.0.0-blue)

## Privacy & Voice Data

VoxTerm is local and private first. All processing happens on your machine — nothing ever leaves.

Voice tagging stores 3D-Speaker ERes2Net embeddings locally at `~/Library/Application Support/voxterm/.speakers.db`. These embeddings are biometric data — they can identify a person across recordings but cannot be used to reconstruct audio. **No audio is ever stored.**

**What we do:**
- All processing is local and offline — no data ever leaves your machine
- Embedding BLOBs are **encrypted at rest** with AES-256-CBC + HMAC-SHA256
- Encryption key is auto-generated and stored in your **macOS Keychain** — zero config, no passwords
- Existing unencrypted databases are automatically migrated on first open
- Database file permissions are set to owner-only (`0600`)
- Daily backups with 7-day retention
- `Ctrl+X` in the profiles screen (`P`) permanently deletes all voice data with a `VACUUM` to scrub bytes from disk
- `.gitignore` excludes all speaker databases and transcripts from version control
- Transcripts auto-saved to `~/Documents/voxterm/` as markdown

**Known limitations:**
- **Metadata is not encrypted.** Speaker names, timestamps, and session history are stored in plaintext SQL columns. Only the biometric embedding BLOBs are encrypted. Someone with file access can see *who* spoke and *when*, but not derive a voiceprint.
- **Keychain access.** The encryption key lives in your macOS login Keychain. Any process running as your user *could* request it. FileVault provides the strongest defense for the at-rest case.

**What we explicitly avoid:**
- No subprocess calls for key management — the Security framework is called directly via `ctypes`, so the key never appears in `argv` or the process list
- No additional dependencies — CommonCrypto and Security.framework are built into macOS

## Setup

```bash
git clone https://github.com/dmarzzz/VoxTerm.git
cd voxterm
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Speaker recognition models download automatically on first run (~633KB).

## Run

```bash
python3 -m tui.app
```

Or use the launcher:
```bash
./voxterm
```

## Controls

| Key | Action |
|-----|--------|
| `R` | Start/pause recording |
| `N` | Party mode — join or leave P2P sessions |
| `T` | Tag/name speakers |
| `P` | Speaker profiles |
| `M` | Switch transcription model |
| `L` | Switch language |
| `S` | Save/export transcript |
| `C` | Clear transcript |
| `D` | Toggle debug mode |
| `?` | Help |
| `Q` | Quit |

## Party Mode (P2P)

Multiple people in the same room can share transcripts over the local network. Each laptop captures its closest speaker best — the combined result is better than any single mic.

**Press N** to join the party. Press N again to leave. No codes, no configuration.

- Auto-discovers nearby VoxTerm peers via mDNS
- Auto-joins the nearest party, or hosts one if none found
- Each party gets a unique color — all peers see the same color
- Encrypted transcript sharing (AES-256-GCM)
- Everyone sees who joins and leaves — no silent surveillance

See [docs/party-mode-design.md](docs/party-mode-design.md) for the full design.

## Voice Tagging

VoxTerm learns and remembers speaker voices across sessions:

1. Record a conversation — speakers are detected as "Speaker 1", "Speaker 2", etc.
2. Press `T` to name them — type a name, press Enter
3. Next session, VoxTerm auto-recognizes returning speakers
4. The more you tag, the less you need to — the system learns over time

Press `P` to manage your speaker profile library (rename, delete, wipe all data).

## Models

- **qwen3-0.6b** (default) — fast, good for most use
- **qwen3-1.7b** — more accurate, larger
- Whisper variants (tiny through large-v3) available via `M` menu

Models download automatically on first use.

## Project Structure

```
audio/              Capture, VAD, transcription, diarization, speaker profiles
network/            P2P: discovery, sessions, party mode
tui/                App, widgets, theme
tests/              Test suite
docs/               Design docs and specs
config.py           Constants, paths, settings
```

## Requirements

- macOS with Apple Silicon (M1+)
- Python 3.9+
- Microphone access
