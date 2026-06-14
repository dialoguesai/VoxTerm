# VoxTerm iOS thin-client

The iOS app is the same Tauri v2 thin-client as Android: a WebView that pairs to the
VoxTerm backend running on your **desktop** over your LAN. The phone does **no transcription
and requests no microphone** — your computer owns the mic and the models.

## Privacy posture
- No `RECORD_AUDIO` / camera / location. The only network egress is token-gated HTTP to the
  desktop you pair with, on your own subnet. No cloud.
- ATS is relaxed **only** for local networking (`NSAllowsLocalNetworking`, *not*
  `NSAllowsArbitraryLoads`) so the app can reach a plain-HTTP LAN desktop — see
  `src-tauri/Info.ios.plist`. iOS 14+ shows a one-time Local Network permission prompt.

## Build (requires a Mac + Xcode — cannot be built off a Mac)
```bash
xcode-select --install              # Xcode command-line tools
sudo gem install cocoapods          # cargo tauri ios init needs CocoaPods
scripts/ios-dev.sh --dev            # simulator (shares the mac's localhost → pair to 127.0.0.1:8740)
scripts/ios-dev.sh --build          # device build
```
`scripts/ios-dev.sh` adds the iOS rust targets, runs `cargo tauri ios init` (XcodeGen +
CocoaPods → `src-tauri/gen/apple/`) on first run, then `cargo tauri ios dev|build`. On a
non-Mac it exits cleanly (no-op).

## Signing
- **Simulator / personal device:** Xcode automatic signing with a free Apple ID (7-day
  provisioning) is enough to run on your own iPhone.
- **CLI / TestFlight:** set `APPLE_DEVELOPMENT_TEAM` (paid Apple Developer Program, $99/yr).
  `developmentTeam` is intentionally **not** committed to `tauri.conf.json`.

## Using it
On a real iPhone: same Wi-Fi as the desktop → run `VOXTERM_GUI_LAN=1 python -m gui.server`
on the desktop, enter its LAN IP + the printed token in the pairing screen, tap **Allow** on
the Local Network prompt. (The simulator can use `127.0.0.1:8740` directly.)
