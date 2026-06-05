# tauri-plugin-voxasr

On-device, fully offline streaming speech-to-text for VoxTerm's mobile shell. The phone records
and transcribes locally with the [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) Android AAR —
**no pairing, no relay, no network**. It's the native alternative to the LAN-pairing fallback (a
phone connecting to a VoxTerm desktop engine).

Android-only. Desktop and iOS get an honest "unsupported" error (desktop uses the full Python
engine; iOS is a future native port).

## How it works

`AudioRecord` (16 kHz mono PCM16) → a sherpa-onnx `OnlineRecognizer` (transducer) with endpoint
detection. The running decode is exposed as a `partial` line; an endpoint finalizes it into
`finals`. The webview drives the plugin through three commands and **polls** for the transcript —
there are no plugin events (polling needs no extra listener permission and is simpler to reason
about):

| Command | Args | Returns |
|---|---|---|
| `start_transcribe` | — | resolves once recording starts (prompts for the mic permission on first use) |
| `stop_transcribe` | — | resolves once the worker has stopped |
| `poll_transcript` | — | `{ partial: string, finals: string[] }` — `finals` are drained on each poll |

The webview consumer lives in [`mobile-pair/`](../mobile-pair/) (`startOnDevice` / `pollOnce` /
`stopOnDevice`, polling every 500 ms while recording).

## Permissions

The only Android permission is `RECORD_AUDIO`. **No `INTERNET` permission** — the on-device path
makes no network calls. `RECORD_AUDIO` is a runtime permission, so the plugin requests it on the
first `start_transcribe` (via the `microphone` alias) and resumes once granted.

## Bundled model (fetch-deps.sh)

The native AAR and the int8 model are large, so they're **gitignored and fetched on demand** rather
than committed. Run `./fetch-deps.sh` once before building; it downloads the version-matched
sherpa-onnx AAR (1.13.2, statically-linked onnxruntime) and stages a streaming int8 model into the
plugin's Android assets, so the APK transcribes offline with no first-run download.

Two model tiers, selected with `VOXASR_MODEL`:

| `VOXASR_MODEL` | Model | Assets | APK | Quality |
|---|---|---|---|---|
| `zipformer-70m` *(default)* | streaming zipformer2 | ~68 MB | ~232 MB | fast (xRT ~0.1 on a mid-range phone); ALL-CAPS, no punctuation |
| `nemotron-0.6b` | NeMo FastConformer-RNNT | ~632 MB | ~621 MB | accurate; native casing + punctuation; xRT ~0.3 on the same phone |

```sh
./fetch-deps.sh                              # lightweight default
VOXASR_MODEL=nemotron-0.6b ./fetch-deps.sh   # high-accuracy tier (~800 MB APK)
```

The Kotlin plugin auto-detects the model architecture **and** feature dimension from the model's own
ONNX metadata (`modelType=""`), so swapping tiers needs no code change — `fetch-deps.sh` is the
single source of truth for which model ships.

## Building

From the repo root, the one-command script handles toolchain/deps/build/install:

```sh
scripts/android-dev.sh --debug          # build + install + smoke-test a debug APK on a connected device
VOXASR_MODEL=nemotron-0.6b scripts/android-dev.sh --debug   # with the high-accuracy tier
```

It runs `fetch-deps.sh` automatically when the AAR/model are missing. To build by hand:
`./tauri-plugin-voxasr/fetch-deps.sh && cargo tauri android build --debug --apk --target aarch64`.

The plugin is registered on Android in `src-tauri/src/lib.rs` (`#[cfg(mobile)]`) and granted in
`src-tauri/capabilities/mobile.json` (`voxasr:default`).
