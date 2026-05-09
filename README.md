# VOXTERM

Local real-time voice transcription TUI with speaker diarization and P2P collaborative transcription. Runs entirely offline — no cloud APIs, no audio stored.

![platform](https://img.shields.io/badge/platform-macOS_(Apple_Silicon)-black)
![version](https://img.shields.io/badge/version-0.1.0-blue)

## Privacy & Storage Policy

VoxTerm is **local first and private by default**. Everything runs on your machine. Nothing is ever sent to a server.

- **No audio is stored.** Microphone input is processed in real-time and discarded. Only text transcripts are saved.
- **Voice profiles are encrypted at rest.** Speaker embeddings (biometric data used to recognize voices across sessions) are encrypted with AES-256-CBC. The key lives in your macOS Keychain — zero config.
- **Transcripts are yours.** Auto-saved as markdown to `~/Documents/voxterm-transcripts/`. Never uploaded anywhere.
- **P2P stays on your LAN.** Party mode shares transcripts over your local network only. No relay servers.
- **Delete everything anytime.** Press `P` → delete to permanently wipe all voice data from disk.

## Install

One command:

```bash
curl -fsSL https://raw.githubusercontent.com/dmarzzz/VoxTerm/main/install.sh | bash
```

Then run:

```bash
voxterm
```

Requires macOS with Apple Silicon (M1+) and Python 3.9+. Models download automatically on first use.

<details>
<summary>Manual setup (for developers)</summary>

```bash
git clone https://github.com/dmarzzz/VoxTerm.git
cd voxterm
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m tui.app
```

</details>

## Controls

| Key | Action |
|-----|--------|
| `R` | Start/pause recording |
| `T` | Tag/name speakers |
| `E` | Browse saved transcripts |
| `S` | Save/export transcript |
| `M` | Switch transcription model |
| `L` | Switch language |
| `P` | Party mode — join or leave LAN sessions |
| `H` | Hivemind — pick a transcript sink |
| `O` | Speaker profiles |
| `V` | Toggle merged transcript view (party mode) |
| `C` | Clear transcript |
| `D` | Toggle debug mode |
| `?` | Help |
| `Q` | Quit |

## Party Mode (P2P)

Multiple people in the same room can share transcripts over the local network. Each laptop captures its closest speaker best — the combined result is better than any single mic.

**Press P** to join the party. Press P again to leave. No codes, no configuration.

- Auto-discovers nearby VoxTerm peers via mDNS
- Auto-joins the nearest party, or hosts one if none found
- Each party gets a unique color — all peers see the same color
- Encrypted transcript sharing (AES-256-GCM)
- Everyone sees who joins and leaves — no silent surveillance

See [docs/party-mode-design.md](docs/party-mode-design.md) for the full design.

## Hivemind Mode

Push your live transcripts to a [swf-node](https://github.com/dmarzzz/searxng-wth-frnds) running on your LAN — the Shape Rotator program's "convent box". Once configured, batches go out every ~60 seconds (or every 30 segments). Local transcript files keep saving as before; hivemind is purely additive.

**Press H** to scan the LAN. Pick a sink from the list and that's it — VoxTerm remembers it across launches.

- Auto-discovers swf-node sinks via mDNS (`_shape-rotator-hivemind._tcp.local.`)
- One-way push only — VoxTerm never reads from the network
- No client-side signing or encryption; the convent-box sink resigns the bundle
- Sink unreachable? Logs `✗ batch failed`, keeps recording locally, retries when the sink reappears
- A v4 `device_id` UUID is generated on first launch (in `~/Library/Application Support/voxterm/device_id`) and tagged onto every batch as opaque provenance

See [`SHAPE-ROTATOR-OS-SPEC.md` §4.3](https://github.com/dmarzzz/searxng-wth-frnds) for the wire protocol.

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
