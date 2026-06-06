# tauri-plugin-voxasr

On-device, fully offline speech-to-text for VoxTerm's mobile app. The phone records the mic and, at
stop, transcribes the whole clip locally with [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)
offline **Whisper** (the same model family the desktop's faster-whisper uses) — full context,
native punctuation + casing, **no pairing, no relay, no network**.

This is what lets the Android app run the **same GUI as the desktop**: the web UI (`gui/static`) is
staged into the mobile bundle (`mobile-pair/app/`) and a `LocalBackend` (`gui/static/backend-local.js`)
drives this plugin instead of the desktop's Python HTTP engine. Same app, different engine underneath.

Android-only. Desktop and iOS get an honest "unsupported" error (desktop uses the full Python engine;
iOS is a future native port).

## How it works

`AudioRecord` (16 kHz mono PCM16) accumulates into a buffer while recording. At stop, the buffer is
split into ≤30 s windows (Whisper's decode cap — cut at a quiet point so words aren't sliced) and
each is decoded by a sherpa-onnx `OfflineRecognizer`, the texts joined. The webview **polls** for
state (no plugin events — polling needs no extra listener permission):

| Command | Args | Returns |
|---|---|---|
| `start_transcribe` | — | resolves once recording starts (prompts for the mic permission on first use) |
| `stop_transcribe` | — | resolves immediately; transcription runs async and is reported via poll |
| `poll_transcript` | — | `{ phase, elapsed, level, durationSec, segments: [{text,start}], error? }` |

`phase` is `idle \| recording \| transcribing \| done \| error`. The `LocalBackend` maps these to the
GUI's record → transcribe → done state machine and builds the transcript from `segments`.

## Permissions & privacy

The only Android permission is `RECORD_AUDIO`. The app manifest **strips `INTERNET`**
(`tools:node="remove"`), so the APK genuinely cannot reach the network — on-device transcription is
provably offline. `RECORD_AUDIO` is a runtime permission, so the plugin requests it on the first
`start_transcribe` (via the `microphone` alias) and resumes once granted.

## Bundled model (fetch-deps.sh)

The native AAR and the int8 model are large, so they're **gitignored and fetched on demand**. Run
`./fetch-deps.sh` once before building; it downloads the version-matched sherpa-onnx AAR (1.13.2,
statically-linked onnxruntime) and stages an offline Whisper int8 model into the plugin's Android
assets, so the APK transcribes offline with no first-run download.

Model size, selected with `VOXASR_MODEL` (default `whisper-base.en`):

| `VOXASR_MODEL` | Assets | Quality / speed (measured on a mid-range phone) |
|---|---|---|
| `whisper-tiny.en` | ~75 MB | fastest, roughest |
| `whisper-base.en` *(default)* | ~155 MB | desktop `fw-base` parity; ~5× real-time (xRT ~0.2) — recommended |
| `whisper-small.en` | ~360 MB | most accurate; ~real-time on a mid-range phone |

```sh
./fetch-deps.sh                                # base.en default
VOXASR_MODEL=whisper-small.en ./fetch-deps.sh
```

`fetch-deps.sh` is the single source of truth for which model ships.

## Building

From the repo root, the one-command script handles toolchain/deps/build/install:

```sh
scripts/android-dev.sh --debug          # build + install + smoke-test a debug APK on a connected device
VOXASR_MODEL=whisper-small.en scripts/android-dev.sh --debug
```

It runs `fetch-deps.sh` automatically when the AAR/model are missing. To build by hand:
`./tauri-plugin-voxasr/fetch-deps.sh && cargo tauri android build --debug --apk --target aarch64`
(Tauri's `beforeBuildCommand` also runs `scripts/stage-mobile.sh` to refresh `mobile-pair/app/`).

The plugin is registered on Android in `src-tauri/src/lib.rs` (`#[cfg(mobile)]`) and granted in
`src-tauri/capabilities/mobile.json` (`voxasr:default`).
