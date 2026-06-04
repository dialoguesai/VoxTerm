#!/usr/bin/env bash
# VoxTerm Android — one-command build + install + smoke-test.
#
#   scripts/android-dev.sh [--debug|--release] [--emulator] [--mock] [--keep] [--no-build] [--deep]
#
# Plug in a phone (USB debugging on) and run it. Self-heals the two known gaps (missing rust
# targets; no AVD). Test traffic stays on loopback via `adb reverse` — never touches Wi-Fi.
# Exit codes: 0 green · 10 toolchain · 11 targets · 20 no device · 30 build · 40 install/reverse
#             50 launch · 60 smoke.  (App id / activity come from tauri.conf.json.)
set -uo pipefail

APP_ID="site.nubs.voxterm"          # keep in sync with src-tauri/tauri.conf.json `identifier`
ACTIVITY="${APP_ID}/.MainActivity"
PORT="${VOXTERM_GUI_PORT:-8740}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"

PROFILE=debug; USE_EMU=0; MOCK=0; KEEP=0; NO_BUILD=0; DEEP=0
for a in "$@"; do case "$a" in
  --debug) PROFILE=debug;; --release) PROFILE=release;;
  --emulator) USE_EMU=1;; --mock) MOCK=1;; --keep) KEEP=1;;
  --no-build) NO_BUILD=1;; --deep) DEEP=1;;
  -h|--help) sed -n '2,9p' "$0"; exit 0;;
  *) echo "unknown arg: $a"; exit 2;; esac; done

# SDK location: honor ANDROID_HOME/ANDROID_SDK_ROOT, else per-OS default (mac vs linux).
: "${ANDROID_HOME:=${ANDROID_SDK_ROOT:-}}"
if [ -z "$ANDROID_HOME" ]; then
  case "$(uname -s)" in
    Darwin) ANDROID_HOME="$HOME/Library/Android/sdk";;
    *)      ANDROID_HOME="$HOME/Android/Sdk";;
  esac
fi
export ANDROID_HOME
export NDK_HOME="${NDK_HOME:-$(ls -d "$ANDROID_HOME"/ndk/* 2>/dev/null | sort | tail -1)}"
# JDK: Android Studio's bundled JBR (per-OS path), else /usr/libexec/java_home on mac.
if [ -z "${JAVA_HOME:-}" ]; then
  case "$(uname -s)" in
    Darwin)
      if [ -x "/Applications/Android Studio.app/Contents/jbr/Contents/Home/bin/java" ]; then
        JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
      elif command -v /usr/libexec/java_home >/dev/null 2>&1; then
        JAVA_HOME="$(/usr/libexec/java_home 2>/dev/null)"
      fi;;
    *) JAVA_HOME="/opt/android-studio/jbr";;
  esac
fi
export JAVA_HOME
# python3 on mac (no bare `python`); allow override via $PYTHON.
PY="${PYTHON:-$(command -v python3 || command -v python)}"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"

say(){ printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
ok(){  printf '  \033[32m✓\033[0m %s\n' "$*"; }
die(){ printf '  \033[31m✗ %s\033[0m\n' "$2" >&2; exit "$1"; }

BACKEND_PID=""; EMU_PID=""; LOG=/tmp/voxterm-backend.log
cleanup(){
  [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null
  adb reverse --remove tcp:$PORT 2>/dev/null
  adb shell am force-stop "$APP_ID" 2>/dev/null
  [ "$KEEP" = 0 ] && [ -n "$EMU_PID" ] && kill "$EMU_PID" 2>/dev/null
}
trap cleanup EXIT

# ── A — toolchain ──
say "A toolchain"
command -v adb   >/dev/null || die 10 "adb missing"
command -v cargo >/dev/null || die 10 "cargo missing"
cargo tauri --version >/dev/null 2>&1 || die 10 "cargo-tauri missing (cargo install tauri-cli)"
[ -d "$NDK_HOME" ]         || die 10 "NDK not found ($NDK_HOME)"
[ -x "$JAVA_HOME/bin/java" ] || die 10 "JAVA_HOME invalid ($JAVA_HOME)"
[ -n "$PY" ]               || die 10 "python3 not found (set \$PYTHON)"
ok "adb · cargo · cargo-tauri · NDK · JAVA_HOME · python3"
have="$(rustup target list --installed 2>/dev/null)"
for t in aarch64-linux-android armv7-linux-androideabi i686-linux-android x86_64-linux-android; do
  echo "$have" | grep -qx "$t" || rustup target add "$t" || die 11 "rustup target add $t failed"
done
ok "rust android targets"

# ── B — device ──
say "B device"
adb start-server >/dev/null 2>&1
DEV="$(adb devices | awk 'NR>1 && $2=="device"{print $1; exit}')"
if [ -z "$DEV" ] || [ "$USE_EMU" = 1 ]; then
  [ "$USE_EMU" = 0 ] && echo "  no physical device — using emulator"
  AVD=voxterm-ci
  # match the emulator ABI to the host: arm64 image on Apple Silicon, x86_64 elsewhere.
  ABI=x86_64; GPU=swiftshader_indirect
  if [ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ]; then ABI=arm64-v8a; GPU=host; fi
  if ! emulator -list-avds 2>/dev/null | grep -qx "$AVD"; then
    IMG="${VOXTERM_AVD_IMAGE:-system-images;android-34;google_apis;$ABI}"
    echo "no" | "$ANDROID_HOME"/cmdline-tools/latest/bin/avdmanager create avd -n "$AVD" -k "$IMG" --device pixel_6 2>/dev/null \
      || die 20 "AVD create failed — install the image first:  sdkmanager \"$IMG\""
  fi
  emulator -avd "$AVD" -no-window -no-audio -no-snapshot -gpu "$GPU" >/tmp/voxterm-emu.log 2>&1 &
  EMU_PID=$!
  adb wait-for-device
  for _ in $(seq 1 90); do [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ] && break; sleep 2; done
  [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ] || die 20 "emulator boot timed out"
  DEV="$(adb devices | awk 'NR>1 && $2=="device"{print $1; exit}')"
fi
[ -n "$DEV" ] || die 20 "no device"
ok "device: $DEV"

# ── C — build ──
if [ "$NO_BUILD" = 0 ]; then
  say "C build ($PROFILE)"
  [ -d src-tauri/gen/android ] || cargo tauri android init || die 30 "android init failed"
  if [ "$PROFILE" = release ]; then cargo tauri android build --apk || die 30 "build failed"
  else cargo tauri android build --debug --apk || die 30 "build failed"; fi
fi
APK="$(find src-tauri/gen/android -path "*outputs/apk/*" -name "*${PROFILE}*.apk" 2>/dev/null | head -1)"
[ -n "$APK" ] || APK="$(find src-tauri/gen/android -name '*.apk' 2>/dev/null | head -1)"
[ -n "$APK" ] || die 30 "no APK found"
ok "APK: ${APK#$ROOT/}"

# ── D — install + launch ──
say "D install + launch"
adb install -r "$APK" >/dev/null 2>&1 || die 40 "adb install failed"; ok "installed"
adb reverse tcp:$PORT tcp:$PORT >/dev/null || die 40 "adb reverse failed"; ok "adb reverse tcp:$PORT (loopback)"
: > "$LOG"
if [ "$MOCK" = 1 ]; then
  VOXTERM_GUI_LOG=1 "$PY" "$ROOT/scripts/mock_backend.py" --port "$PORT" >"$LOG" 2>&1 & BACKEND_PID=$!
else
  VOXTERM_GUI_LOG=1 VOXTERM_GUI_PORT="$PORT" "$PY" -m gui.server >"$LOG" 2>&1 & BACKEND_PID=$!
fi
up=0; for _ in $(seq 1 40); do curl -sf "http://127.0.0.1:$PORT/api/options" >/dev/null 2>&1 && { up=1; break; }; sleep 0.5; done
[ "$up" = 1 ] && ok "backend up on 127.0.0.1:$PORT" || echo "  ! backend silent — round-trip asserts go soft"
adb logcat -c 2>/dev/null
adb shell am start -W -n "$ACTIVITY" >/tmp/voxterm-amstart.log 2>&1 || die 50 "am start failed"
grep -q "Status: ok" /tmp/voxterm-amstart.log || die 50 "activity did not report Status: ok"
ok "launched $ACTIVITY"; sleep 4   # let the webview load + the loopback auto-probe fire

# ── E — smoke ──
say "E smoke"
FAILS=0
adb shell dumpsys activity activities 2>/dev/null | grep -q "$APP_ID" \
  && ok "E1 activity foregrounded" || { echo "  ✗ E1 activity not found"; FAILS=$((FAILS+1)); }
adb exec-out screencap -p > /tmp/voxterm-shot.png 2>/dev/null
"$PY" "$ROOT/scripts/assert_screen.py" /tmp/voxterm-shot.png \
  && ok "E3 render sane" || { echo "  ✗ E3 render check failed"; FAILS=$((FAILS+1)); }
if [ "$up" = 1 ] && grep -q "GET /api/options" "$LOG"; then
  ok "E2 WebView reached the engine (GET /api/options)"
  grep -q "GET /api/events" "$LOG" && ok "E2 SSE stream opened"
else
  echo "  ~ E2 round-trip not observed (soft, v1) — shell verified; connect flow needs the loopback/paired backend"
fi
if [ "$DEEP" = 1 ] && [ "$up" = 1 ]; then
  adb shell input tap 210 470 2>/dev/null; sleep 2
  grep -q "POST /api/record/start" "$LOG" && ok "E4 record round-trip" || echo "  ~ E4 not observed (tap-by-coord is fragile)"
fi

say "RESULT"
[ "$FAILS" = 0 ] && { ok "ALL GREEN — builds, installs, launches, renders"; exit 0; } || die 60 "$FAILS hard assertion(s) failed"
