"""Coarse 'the screen isn't blank/crashed' check for the Android smoke test.

Takes a screencap PNG and asserts it looks like the live dark VoxTerm UI, not an all-white
ANR dialog, an all-black dead webview, or a solid crash screen. Deliberately NOT a pixel-diff
(brittle) — just luminance distribution. Exit 0 = looks alive, 1 = blank/suspect.

    python scripts/assert_screen.py shot.png
"""
from __future__ import annotations

import sys


def check(path: str) -> tuple[bool, str]:
    try:
        from PIL import Image
    except ImportError:
        return True, "Pillow not installed — skipping render check (treat as pass)"
    try:
        im = Image.open(path).convert("L").resize((64, 128))
    except Exception as e:
        return False, f"could not open screenshot: {e}"
    px = list(getattr(im, "get_flattened_data", im.getdata)())  # getdata deprecated in Pillow 14
    n = len(px)
    near_white = sum(1 for v in px if v > 245) / n
    near_black = sum(1 for v in px if v < 8) / n
    mean = sum(px) / n
    if near_white > 0.97:
        return False, f"screen is ~all white ({near_white:.0%}) — likely an ANR/error dialog"
    if near_black > 0.97:
        return False, f"screen is ~all black ({near_black:.0%}) — webview likely didn't render"
    # VoxTerm is a dark UI: expect a low-but-not-zero mean luminance with some variation
    if mean > 200:
        return False, f"screen too bright (mean {mean:.0f}) — not the dark VoxTerm UI"
    return True, f"looks alive (mean luminance {mean:.0f}, white {near_white:.0%}, black {near_black:.0%})"


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: assert_screen.py <screenshot.png>", file=sys.stderr)
        return 2
    try:
        import PIL  # noqa: F401
    except ImportError:
        # exit 3 = SKIP (not a pass): Pillow isn't a macOS dep, so the caller must NOT treat
        # an absent-Pillow run as a green render gate.
        print("SKIP: Pillow not installed — render check skipped (pip install Pillow to enable)")
        return 3
    ok, msg = check(args[0])
    print(("OK: " if ok else "FAIL: ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
