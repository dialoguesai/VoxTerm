#!/usr/bin/env bash
# VoxTerm iOS thin-client — build/run on a Mac (mirrors android-dev.sh).
#
#   scripts/ios-dev.sh [--dev|--build]
#
# REQUIRES macOS + Xcode (xcrun/xcodebuild) + CocoaPods — iOS cannot be built off a Mac.
# Same thin-client design as Android: the app is a WebView that pairs to the VoxTerm
# backend on your desktop over the LAN. No mic permission on the phone.
set -uo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
  echo "ios-dev.sh: iOS builds require macOS + Xcode — nothing to do on $(uname -s)." >&2
  exit 0
fi

MODE="${1:---dev}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"

die() { printf '  \033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }
command -v xcrun      >/dev/null || die "xcrun not found — run: xcode-select --install"
command -v xcodebuild >/dev/null || die "xcodebuild not found (install Xcode)"
cargo tauri --version >/dev/null 2>&1 || die "cargo-tauri missing (cargo install tauri-cli)"
command -v pod        >/dev/null || echo "  ! CocoaPods (pod) not found — 'cargo tauri ios init' needs it (sudo gem install cocoapods)"

# iOS rust targets (device + simulator)
for t in aarch64-apple-ios aarch64-apple-ios-sim x86_64-apple-ios; do
  rustup target list --installed 2>/dev/null | grep -qx "$t" || rustup target add "$t" || die "rustup target add $t failed"
done

[ -d src-tauri/gen/apple ] || cargo tauri ios init || die "cargo tauri ios init failed"

case "$MODE" in
  --dev)   cargo tauri ios dev ;;     # simulator shares the mac's localhost → pair to 127.0.0.1:8740
  --build) cargo tauri ios build ;;   # device build (needs signing: Xcode automatic, or APPLE_DEVELOPMENT_TEAM)
  *) echo "usage: ios-dev.sh [--dev|--build]"; exit 2 ;;
esac
