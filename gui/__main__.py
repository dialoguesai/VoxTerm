"""`python -m gui` — convenience alias for the GUI launcher (opens the browser GUI).

For the raw server use `python -m gui.server`; for the desktop app run the Tauri build.
"""
from gui.launcher import main

if __name__ == "__main__":
    raise SystemExit(main())
