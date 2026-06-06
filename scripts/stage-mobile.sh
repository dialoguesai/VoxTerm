#!/usr/bin/env bash
# Stage the desktop GUI (gui/static) into the mobile bundle so the phone runs the SAME app offline.
# The Tauri Android bundle's frontendDist is mobile-pair/; this drops the GUI under mobile-pair/app/
# with the on-device LocalBackend wired in place of the HTTP RemoteBackend. Regenerated on every
# build (mobile-pair/app/ is gitignored) so gui/static stays the single source of truth.
# Run automatically by tauri's beforeDevCommand / beforeBuildCommand.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/gui/static"
DST="$ROOT/mobile-pair/app"

rm -rf "$DST"; mkdir -p "$DST"
# Copy the GUI, minus the PWA shell (no service worker / manifest on-device) and the HTTP backend
# (the on-device copy uses backend-local.js, already present in gui/static).
for f in "$SRC"/*; do
  case "$(basename "$f")" in
    sw.js | manifest.webmanifest | backend-remote.js | index.html) continue ;;
    *) cp "$f" "$DST/" ;;
  esac
done
# Rewrite the entry for the /app/ subpath + the on-device backend:
#   /static/X         -> ./X                 (assets sit flat beside index.html)
#   backend-remote.js -> backend-local.js    (sets window.VOX_BACKEND + the on-device flag)
#   drop the manifest <link>                 (no PWA shell on-device)
sed -e 's#/static/#./#g' \
    -e 's#backend-remote\.js#backend-local.js#' \
    -e '/rel="manifest"/d' \
    "$SRC/index.html" > "$DST/index.html"
echo "staged mobile GUI -> ${DST#"$ROOT"/} ($(find "$DST" -type f | wc -l | tr -d ' ') files)"
