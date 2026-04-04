"""Bloom overlay — colour wash on party join."""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING

from textual.strip import Strip
from textual.widget import Widget
from textual.geometry import Region
from rich.segment import Segment
from rich.style import Style

if TYPE_CHECKING:
    from textual.app import App

# ── constants ──────────────────────────────────────────────────

_GRADIENT = [
    (255, 20, 60),
    (255, 0, 170),
    (170, 30, 255),
    (40, 120, 255),
    (0, 255, 220),
    (57, 255, 20),
    (230, 255, 0),
    (255, 120, 20),
    (255, 0, 180),
]

_APP_BG = (10, 14, 20)
_BG_STYLE = Style(bgcolor="#0a0e14")
_TIMESTAMP_COLOR = "#306070"

_MAX_TINT = 0.85
_DESAT = 0.15
_DURATION = 2.5
_DISSOLVE_START = 0.55
_SEED_COUNT = 14
_SPEED_LO, _SPEED_HI = 15.0, 30.0
_STAGGER_MAX = 0.4


# ── helpers ────────────────────────────────────────────────────

def _lerp_color(
    a: tuple[int, int, int], b: tuple[int, int, int], t: float,
) -> tuple[int, int, int]:
    """Linear interpolation between two RGB triples."""
    t = max(0.0, min(1.0, t))
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def _extract_rgb(
    style: Style, attr: str = "bgcolor",
) -> tuple[int, int, int]:
    """Extract RGB from a Rich Style attribute, falling back to app bg."""
    try:
        col = getattr(style, attr, None)
        if col is not None:
            tc = col.get_truecolor()
            return (tc.red, tc.green, tc.blue)
    except Exception:
        pass
    return _APP_BG


@lru_cache(maxsize=4096)
def _tint_style(
    orig_bg: tuple[int, int, int],
    orig_fg: tuple[int, int, int],
    bloom_rgb: tuple[int, int, int],
    tint: float,
    char: str,
) -> tuple[str, Style]:
    """Return (char, blended style) — cached for repeated cells."""
    # Blend bg toward bloom colour
    bg = _lerp_color(orig_bg, bloom_rgb, tint)
    # Desaturate slightly toward app bg
    bg = _lerp_color(bg, _APP_BG, _DESAT * tint)
    # Brighten fg slightly
    brighten = min(tint * 0.3, 0.25)
    fg = (
        min(255, int(orig_fg[0] + (255 - orig_fg[0]) * brighten)),
        min(255, int(orig_fg[1] + (255 - orig_fg[1]) * brighten)),
        min(255, int(orig_fg[2] + (255 - orig_fg[2]) * brighten)),
    )
    style = Style(
        color=f"#{fg[0]:02x}{fg[1]:02x}{fg[2]:02x}",
        bgcolor=f"#{bg[0]:02x}{bg[1]:02x}{bg[2]:02x}",
    )
    return char, style


def _blend(
    a: tuple[int, int, int], b: tuple[int, int, int], t: float,
) -> tuple[int, int, int]:
    """Alias for _lerp_color."""
    return _lerp_color(a, b, t)


def _noise(x: float, y: float, seed: float) -> float:
    """Cheap deterministic noise for radius warping."""
    return (
        math.sin(x * 0.7 + seed) * math.cos(y * 1.3 + seed * 0.7)
        + math.sin((x + y) * 0.4 + seed * 1.1) * 0.5
    )


# ── seed data ──────────────────────────────────────────────────

@dataclass
class _Seed:
    x: float
    y: float
    hue_offset: int       # index into _GRADIENT
    delay: float          # seconds before this seed starts
    speed: float          # radius growth per second (cells)
    noise_seed: float     # for radius warping


def _strip_to_cells(strip: Strip) -> list[tuple[str, Style]]:
    """Decompose a Strip into per-cell (char, style) pairs."""
    cells: list[tuple[str, Style]] = []
    for segment in strip:
        text = segment.text
        style = segment.style or _BG_STYLE
        for ch in text:
            cells.append((ch, style))
    return cells


# ── widget ─────────────────────────────────────────────────────

class FireworkOverlay(Widget):
    """Colour-wash bloom overlay that tints the existing UI."""

    DEFAULT_CSS = """
    FireworkOverlay {
        width: 1fr;
        height: 1fr;
        layer: notifications;
        background: #0a0e14;
    }
    """

    def __init__(self, burst_count: int = 8, **kwargs) -> None:
        super().__init__(**kwargs)
        self._burst_count = max(4, min(burst_count, 30))
        self._t0: float | None = None
        self._seeds: list[_Seed] = []
        self._bg_cells: dict[tuple[int, int], tuple[str, Style]] = {}
        self._width = 0
        self._height = 0
        self._dissolve_grid: dict[tuple[int, int], float] = {}
        self._inited = False

    # ── lazy init (first render) ───────────────────────────────

    def _lazy_init(self) -> None:
        if self._inited:
            return
        self._inited = True
        self._t0 = time.monotonic()
        self._width = self.size.width
        self._height = self.size.height
        self._capture_background()
        self._generate_seeds()
        self._generate_dissolve_grid()

    def _capture_background(self) -> None:
        """Walk screen children and capture their rendered content."""
        self._bg_cells.clear()
        try:
            for widget in self.screen.walk_children():
                if widget is self:
                    continue
                region = widget.region
                crop = Region(0, 0, region.width, region.height)
                try:
                    strips = widget.render_lines(crop)
                except Exception:
                    continue
                for row_idx, strip in enumerate(strips):
                    screen_y = region.y + row_idx
                    if screen_y < 0 or screen_y >= self._height:
                        continue
                    cells = _strip_to_cells(strip)
                    for col_idx, (ch, style) in enumerate(cells):
                        screen_x = region.x + col_idx
                        if 0 <= screen_x < self._width:
                            self._bg_cells[(screen_x, screen_y)] = (ch, style)
        except Exception:
            pass

    def _generate_seeds(self) -> None:
        """Create random bloom seed points."""
        self._seeds.clear()
        w, h = self._width, self._height
        if w <= 0 or h <= 0:
            return
        count = self._burst_count or _SEED_COUNT
        for _ in range(count):
            self._seeds.append(
                _Seed(
                    x=random.uniform(0, w),
                    y=random.uniform(0, h),
                    hue_offset=random.randint(0, len(_GRADIENT) - 1),
                    delay=random.uniform(0, _STAGGER_MAX),
                    speed=random.uniform(_SPEED_LO, _SPEED_HI),
                    noise_seed=random.uniform(0, 100),
                )
            )

    def _generate_dissolve_grid(self) -> None:
        """Pre-compute dissolve order: edge cells first, core last."""
        self._dissolve_grid.clear()
        w, h = self._width, self._height
        if w <= 0 or h <= 0:
            return
        cx, cy = w / 2.0, h / 2.0
        max_dist = math.sqrt(cx * cx + cy * cy) or 1.0
        for y in range(h):
            for x in range(w):
                d = math.sqrt((x - cx) ** 2 + (y - cy) ** 2) / max_dist
                # Edge cells dissolve first (low threshold), core last (high)
                self._dissolve_grid[(x, y)] = 1.0 - d

    # ── frame building ─────────────────────────────────────────

    def _build_frame(self, elapsed: float) -> list[Strip]:
        """Build one animation frame."""
        w, h = self._width, self._height
        if w <= 0 or h <= 0:
            return [Strip.blank(w) for _ in range(h)]

        # Dissolve phase
        dissolve_progress = 0.0
        if elapsed > _DURATION * _DISSOLVE_START:
            dissolve_progress = (elapsed - _DURATION * _DISSOLVE_START) / (
                _DURATION * (1.0 - _DISSOLVE_START)
            )
            dissolve_progress = max(0.0, min(1.0, dissolve_progress))

        strips: list[Strip] = []
        for y in range(h):
            segments: list[Segment] = []
            for x in range(w):
                # Accumulate intensity from all seeds
                intensity = 0.0
                weighted_r, weighted_g, weighted_b = 0.0, 0.0, 0.0

                for seed in self._seeds:
                    t_seed = elapsed - seed.delay
                    if t_seed <= 0:
                        continue
                    radius = t_seed * seed.speed

                    # Noise-warped distance
                    dx = x - seed.x
                    dy = y - seed.y
                    dist = math.sqrt(dx * dx + dy * dy)

                    # Warp radius by noise (+-30%)
                    n = _noise(x, y, seed.noise_seed)
                    warped_radius = radius * (1.0 + 0.3 * n)

                    if dist >= warped_radius or warped_radius <= 0:
                        continue

                    # Cubic falloff
                    ratio = dist / warped_radius
                    depth = (1.0 - ratio) ** 3

                    # Hue from gradient
                    grad = _GRADIENT[seed.hue_offset % len(_GRADIENT)]
                    intensity += depth
                    weighted_r += depth * grad[0]
                    weighted_g += depth * grad[1]
                    weighted_b += depth * grad[2]

                # Clamp intensity
                intensity = min(intensity, 1.0)

                # Dissolve: reduce intensity for cells whose dissolve
                # threshold has been passed
                if dissolve_progress > 0 and intensity > 0.02:
                    threshold = self._dissolve_grid.get((x, y), 0.5)
                    if dissolve_progress > threshold:
                        fade = (dissolve_progress - threshold) / max(
                            1.0 - threshold, 0.01
                        )
                        intensity *= max(0.0, 1.0 - fade)

                # Get original cell
                orig_ch, orig_style = self._bg_cells.get(
                    (x, y), (" ", _BG_STYLE)
                )

                if intensity < 0.02:
                    # Pass through original cell
                    segments.append(Segment(orig_ch, orig_style))
                    continue

                # Compute bloom colour
                if intensity > 0:
                    bloom_rgb = (
                        min(255, int(weighted_r / intensity)),
                        min(255, int(weighted_g / intensity)),
                        min(255, int(weighted_b / intensity)),
                    )
                else:
                    bloom_rgb = _APP_BG

                # Tint amount
                tint = min(intensity * 0.9, _MAX_TINT)

                # Extract original colours
                orig_bg = _extract_rgb(orig_style, "bgcolor")
                orig_fg = _extract_rgb(orig_style, "color")

                char, style = _tint_style(
                    orig_bg, orig_fg, bloom_rgb, tint, orig_ch,
                )
                segments.append(Segment(char, style))

            strips.append(Strip(segments))
        return strips

    # ── rendering ──────────────────────────────────────────────

    def render_line(self, y: int) -> Strip:
        """Called by Textual to render each line."""
        self._lazy_init()
        if self._t0 is None:
            return Strip.blank(self.size.width)

        elapsed = time.monotonic() - self._t0

        if elapsed >= _DURATION:
            # Animation complete — remove self
            self.set_timer(0.05, self._remove_self)
            return Strip.blank(self.size.width)

        # Build full frame on first line, cache for remaining lines
        if y == 0 or not hasattr(self, "_frame_cache") or self._frame_cache_t != elapsed:
            self._frame_cache = self._build_frame(elapsed)
            self._frame_cache_t = elapsed
            # Schedule next frame
            self.set_timer(1 / 30, self._request_refresh)

        if 0 <= y < len(self._frame_cache):
            return self._frame_cache[y]
        return Strip.blank(self.size.width)

    def _request_refresh(self) -> None:
        """Request a screen refresh for animation."""
        if self._t0 is not None:
            elapsed = time.monotonic() - self._t0
            if elapsed < _DURATION:
                self.refresh()

    def _remove_self(self) -> None:
        """Remove overlay from the DOM."""
        try:
            self.remove()
        except Exception:
            pass
