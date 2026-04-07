"""Centralized, platform-aware path resolution for VoxTerm.

On macOS (darwin): uses ~/Documents/voxterm and ~/Library/Application Support/voxterm.
On Linux: uses XDG Base Directory paths ($XDG_DATA_HOME, $XDG_CONFIG_HOME).
On Windows: uses %LOCALAPPDATA%/voxterm for app data and ~/Documents/voxterm for sessions.
"""

import os
import sys
from pathlib import Path

_home = Path.home()

if sys.platform == "darwin":
    # macOS paths — unchanged from original layout
    SESSIONS_DIR = _home / "Documents" / "voxterm"
    DATA_DIR = _home / "Library" / "Application Support" / "voxterm"
    LIVE_DIR = SESSIONS_DIR / ".live"
    BIN_DIR = SESSIONS_DIR / ".bin"
    CRASH_DIR = SESSIONS_DIR / ".crashes"
    STATE_FILE = SESSIONS_DIR / ".state.json"
elif sys.platform.startswith("linux"):
    # Linux — XDG-compliant paths
    _xdg_data = Path(os.environ.get("XDG_DATA_HOME", _home / ".local" / "share"))
    _xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", _home / ".config"))
    DATA_DIR = _xdg_data / "voxterm"
    CONFIG_DIR = _xdg_config / "voxterm"
    SESSIONS_DIR = DATA_DIR
    LIVE_DIR = DATA_DIR / ".live"
    BIN_DIR = DATA_DIR / ".bin"
    CRASH_DIR = DATA_DIR / ".crashes"
    STATE_FILE = CONFIG_DIR / "state.json"
elif sys.platform == "win32":
    # Windows — %LOCALAPPDATA% for app data (private to user by default ACLs),
    # user Documents for human-visible session files.
    _localappdata = Path(os.environ.get("LOCALAPPDATA", _home / "AppData" / "Local"))
    DATA_DIR = _localappdata / "voxterm"
    SESSIONS_DIR = _home / "Documents" / "voxterm"
    LIVE_DIR = SESSIONS_DIR / ".live"
    BIN_DIR = DATA_DIR / "bin"
    CRASH_DIR = DATA_DIR / "crashes"
    STATE_FILE = SESSIONS_DIR / ".state.json"
else:
    raise RuntimeError(f"Unsupported platform: {sys.platform}")

# Speaker database
DB_DIR = DATA_DIR
DB_PATH = DB_DIR / ".speakers.db"
BACKUP_DIR = DB_DIR / ".backups"
