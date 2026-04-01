#!/bin/bash
set -euo pipefail

# VoxTerm installer — downloads the latest release and sets up a ready-to-run installation.
# Usage: curl -fsSL https://dmarzzz.github.io/VoxTerm/install.sh | bash

REPO="dmarzzz/VoxTerm"
INSTALL_DIR="${VOXTERM_INSTALL_DIR:-$HOME/.local/share/voxterm}"
BIN_DIR="${VOXTERM_BIN_DIR:-$HOME/.local/bin}"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
error() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

# --- Detect platform ---
OS="$(uname -s)"
ARCH="$(uname -m)"

case "${OS}" in
  Darwin) PLATFORM="macos" ;;
  Linux)  PLATFORM="linux" ;;
  *)      error "Unsupported OS: ${OS}" ;;
esac

case "${ARCH}" in
  arm64|aarch64) ARCH_NAME="arm64" ;;
  x86_64)        ARCH_NAME="x86_64" ;;
  *)             error "Unsupported architecture: ${ARCH}" ;;
esac

ASSET_PATTERN="voxterm-*-${PLATFORM}-${ARCH_NAME}.tar.gz"

# --- Check dependencies ---
command -v python3 >/dev/null 2>&1 || error "python3 is required but not found"
command -v curl >/dev/null 2>&1    || error "curl is required but not found"

PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PYTHON_MAJOR="${PYTHON_VERSION%%.*}"
PYTHON_MINOR="${PYTHON_VERSION#*.}"
if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 11 ]; }; then
  error "Python 3.11+ required, found ${PYTHON_VERSION}"
fi

if [ "${PLATFORM}" = "macos" ]; then
  command -v brew >/dev/null 2>&1 || info "Homebrew not found — you may need to install portaudio and ffmpeg manually"
  if command -v brew >/dev/null 2>&1; then
    for dep in portaudio ffmpeg; do
      if ! brew list "${dep}" &>/dev/null; then
        info "Installing ${dep} via Homebrew..."
        brew install "${dep}"
      fi
    done
  fi
elif [ "${PLATFORM}" = "linux" ]; then
  for cmd in ffmpeg; do
    command -v "${cmd}" >/dev/null 2>&1 || info "Warning: ${cmd} not found — install it with your package manager"
  done
fi

# --- Fetch latest release ---
info "Fetching latest release from ${REPO}..."
RELEASE_JSON="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest")"
TAG="$(echo "${RELEASE_JSON}" | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])")"
VERSION="${TAG#v}"

info "Latest version: ${VERSION}"

# Find the matching asset URL
DOWNLOAD_URL="$(echo "${RELEASE_JSON}" | python3 -c "
import sys, json, fnmatch
assets = json.load(sys.stdin)['assets']
pattern = '${ASSET_PATTERN}'
for a in assets:
    if fnmatch.fnmatch(a['name'], pattern):
        print(a['browser_download_url'])
        break
else:
    sys.exit(1)
")" || error "No release asset found matching ${ASSET_PATTERN}. Check https://github.com/${REPO}/releases"

ASSET_NAME="$(basename "${DOWNLOAD_URL}")"

# --- Download and extract ---
info "Downloading ${ASSET_NAME}..."
TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

curl -fsSL -o "${TMPDIR}/${ASSET_NAME}" "${DOWNLOAD_URL}"

info "Installing to ${INSTALL_DIR}..."
rm -rf "${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"
tar xzf "${TMPDIR}/${ASSET_NAME}" -C "${INSTALL_DIR}" --strip-components=1

# --- Rebuild venv for this system if needed ---
if [ ! -d "${INSTALL_DIR}/.venv" ] || ! "${INSTALL_DIR}/.venv/bin/python3" -c "import sys" 2>/dev/null; then
  info "Setting up Python virtual environment..."
  python3 -m venv "${INSTALL_DIR}/.venv"
  "${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip -q
  "${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt" -q
fi

# --- Symlink binary ---
mkdir -p "${BIN_DIR}"
ln -sf "${INSTALL_DIR}/voxterm" "${BIN_DIR}/voxterm"

# --- Verify ---
if [ -x "${BIN_DIR}/voxterm" ]; then
  info "VoxTerm ${VERSION} installed successfully!"
  echo ""
  echo "  Run:  voxterm"
  echo ""
  if ! echo "${PATH}" | tr ':' '\n' | grep -qx "${BIN_DIR}"; then
    echo "  Note: Add ${BIN_DIR} to your PATH:"
    echo "    export PATH=\"${BIN_DIR}:\$PATH\""
    echo ""
  fi
else
  error "Installation failed — ${BIN_DIR}/voxterm not found"
fi
