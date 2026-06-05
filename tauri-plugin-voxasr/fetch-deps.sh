#!/usr/bin/env bash
# Fetch the native deps for the on-device ASR plugin (gitignored — large binaries):
#   - the sherpa-onnx Android AAR (statically-linked onnxruntime), version-matched to the
#     desktop engine (sherpa_onnx 1.13.2)
#   - the streaming zipformer-20M int8 model, staged into the plugin's Android assets so the
#     APK transcribes fully offline (no first-run download)
# Run this once before `cargo tauri android build`.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
VER="1.13.2"
MODEL="sherpa-onnx-streaming-zipformer-en-20M-2023-02-17"
LIBS="$HERE/android/libs"
ASSETS="$HERE/android/src/main/assets/voxterm-model"
mkdir -p "$LIBS" "$ASSETS"

AAR="$LIBS/sherpa-onnx-$VER.aar"
if [ ! -f "$AAR" ]; then
  echo "fetching sherpa-onnx $VER Android AAR…"
  curl -fSL -o "$AAR" \
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/v$VER/sherpa-onnx-static-link-onnxruntime-$VER.aar"
fi

# Reuse the desktop model cache if present; otherwise download the model release.
CACHE="$HOME/.cache/voxterm/sherpa/$MODEL"
if [ ! -f "$CACHE/tokens.txt" ]; then
  echo "fetching $MODEL…"
  mkdir -p "$(dirname "$CACHE")"
  curl -fSL -o "/tmp/$MODEL.tar.bz2" \
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$MODEL.tar.bz2"
  tar xjf "/tmp/$MODEL.tar.bz2" -C "$(dirname "$CACHE")"
fi

# Stage the int8 trio under the names the Kotlin plugin loads.
cp "$CACHE/encoder-epoch-99-avg-1.int8.onnx" "$ASSETS/encoder.int8.onnx"
cp "$CACHE/decoder-epoch-99-avg-1.int8.onnx" "$ASSETS/decoder.int8.onnx"
cp "$CACHE/joiner-epoch-99-avg-1.int8.onnx"  "$ASSETS/joiner.int8.onnx"
cp "$CACHE/tokens.txt"                        "$ASSETS/tokens.txt"
cp "$CACHE/test_wavs/0.wav"                   "$ASSETS/test.wav"   # debug self-test clip (offline decode check)
echo "voxasr native deps ready ($(du -sh "$ASSETS" | cut -f1) model, $(du -h "$AAR" | cut -f1) aar)."
