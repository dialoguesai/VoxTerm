#!/usr/bin/env bash
# Fetch the native deps for the on-device ASR plugin (gitignored — large binaries):
#   - the sherpa-onnx Android AAR (statically-linked onnxruntime), version-matched to the
#     desktop engine (sherpa_onnx 1.13.2)
#   - a streaming int8 ASR model, staged into the plugin's Android assets so the APK
#     transcribes fully offline (no first-run download)
#
# Two model tiers, selected with VOXASR_MODEL (default: the lightweight one):
#   zipformer-70m  streaming zipformer2, ~68 MB assets  — fast, ALL-CAPS, no punctuation
#   nemotron-0.6b  NeMo FastConformer-RNNT, ~632 MB assets — accurate, native casing + punctuation
# The Kotlin plugin auto-detects the architecture from each model's ONNX metadata
# (modelType="") and reads its feature dim from the same metadata, so swapping tiers needs
# no code change — only a bigger APK.
#
# Run before `cargo tauri android build`, e.g.:
#   ./fetch-deps.sh                              # lightweight default
#   VOXASR_MODEL=nemotron-0.6b ./fetch-deps.sh   # high-accuracy tier (~800 MB APK)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
VER="1.13.2"
LIBS="$HERE/android/libs"
ASSETS="$HERE/android/src/main/assets/voxterm-model"
mkdir -p "$LIBS" "$ASSETS"

case "${VOXASR_MODEL:-zipformer-70m}" in
  zipformer-70m) MODEL="sherpa-onnx-streaming-zipformer-en-2023-06-26" ;;
  nemotron-0.6b) MODEL="sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25" ;;
  *) echo "unknown VOXASR_MODEL='${VOXASR_MODEL}' (want zipformer-70m | nemotron-0.6b)" >&2; exit 1 ;;
esac

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

# Stage the int8 trio under the names the Kotlin plugin loads. Glob the source names so both
# tiers work (zipformer ships `encoder-epoch-…-chunk-16-left-128.int8.onnx`, nemotron ships a
# plain `encoder.int8.onnx`); first match wins, mirroring the desktop loader's _pick().
stage() {  # stage <glob-relative-to-CACHE> <dest-name>: copy the matching int8 file into assets
  # The glob expands here on purpose — each model ships exactly one *encoder*/*decoder*/*joiner*
  # int8 file, so the first (only) match is the one to stage.
  # shellcheck disable=SC2206
  local matches=( "$CACHE"/$1 )
  [ -e "${matches[0]}" ] || { echo "missing '$1' in $CACHE" >&2; exit 1; }
  cp "${matches[0]}" "$ASSETS/$2"
}
stage "*encoder*.int8.onnx" encoder.int8.onnx
stage "*decoder*.int8.onnx" decoder.int8.onnx
stage "*joiner*.int8.onnx"  joiner.int8.onnx
cp "$CACHE/tokens.txt"      "$ASSETS/tokens.txt"
cp "$CACHE/test_wavs/0.wav" "$ASSETS/test.wav"   # debug self-test clip (offline decode check)
echo "voxasr native deps ready ($(du -sh "$ASSETS" | cut -f1) model [$MODEL], $(du -h "$AAR" | cut -f1) aar)."
