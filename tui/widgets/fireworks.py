"""Bloom overlay — colour wash on party join.

Expanding neon glow that tints the existing UI rather than covering it.
Keeps the original characters and blends background/foreground colours
toward the bloom hue, so text stays readable through the effect.

Self-removes after the animation completes.
"""

from __future__ import annotations

import math
import random
import time
from functools import lru_cache

from textual.strip import Strip
from textual.widget import Widget
from rich.segment import Segment
from rich.style import Style


# ── colour helpers ───────────────────────────────────────────

_GRAD = [
    (0.000, (255,  20,  60)),
    (0.125, (255,   0, 170)),
    (0.250, (170,  30, 255)),
    (0.375, ( 40, 120, 255)),
    (0.500, (  0, 255, 220)),
    (0.625, ( 57, 255,  20)),
    (0.750, (230, 255,   0)),
    (0.875, (255, 120,  20)),
    (1.000, (255,   0, 180)),
]

# App background RGB for blending
_BG_R, _BG_G, _BG_B = 10, 14, 20
_BG_HEX = "#0a0e14"
_BG_STYLE = Style(bgcolor=_BG_HEX)


def _lerp_color(t: float) -> tuple[int, int, int]:
    t = t % 1.0
    for i in range(len(_GRAD) - 1):
        if t <= _GRAD[i + 1][0]:
            f = (t - _GRAD[i][0]) / (_GRAD[i + 1][0] - _GRAD[i][0])
            a, b = _GRAD[i][1], _GRAD[i + 1][1]
            return (
                int(a[0] + (b[0] - a[0]) * f),
                int(a[1] + (b[1] - a[1]) * f),
                int(a[2] + (b[2] - a[2]) * f),
            )
    return _GRAD[-1][1]


def _extract_rgb(style: Style | None, attr: str) -> tuple[int, int, int]:
    if style is None:
        return (_BG_R, _BG_G, _BG_B)
    color_obj = getattr(style, attr, None)
    if color_obj is None:
        return (_BG_R, _BG_G, _BG_B)
    try:
        triplet = color_obj.get_truecolor()
        return (triplet.red, triplet.green, triplet.blue)
    except Exception:
        return (_BG_R, _BG_G, _BG_B)


def _blend(base: int, target: int, t: float) -> int:
    return min(255, max(0, int(base + (target - base) * t)))


@lru_cache(maxsize=4096)
def _tint_style(fg_r: int, fg_g: int, fg_b: int,
                bg_r: int, bg_g: int, bg_b: int) -> Style:
    fg = f"#{fg_r:02x}{fg_g:02x}{fg_b:02x}"
    bg = f"#{bg_r:02x}{bg_g:02x}{bg_b:02x}"
    return Style(color=fg, bgcolor=bg)


# ── bloom seed ───────────────────────────────────────────────

class _Seed:
    __slots__ = ("x", "y", "hue_offset", "delay", "speed", "noise_seed")

    def __init__(self, x: float, y: float, hue_offset: float,
                 delay: float, speed: float) -> None:
        self.x = x
        self.y = y
        self.hue_offset = hue_offset
        self.delay = delay
        self.speed = speed
        self.noise_seed = random.random() * 1000


# ── helpers ──────────────────────────────────────────────────

def _strip_to_cells(strip: Strip, width: int) -> list[Segment]:
    fallback = Segment(" ", _BG_STYLE)
    cells: list[Segment] = [fallback] * width
    x = 0
    for seg in strip:
        style = seg.style
        for ch in seg.text:
            if x >= width:
                break
            cells[x] = Segment(ch, style)
            x += 1
        if x >= width:
            break
    return cells


# ── overlay widget ───────────────────────────────────────────

class FireworkOverlay(Widget):
    """Expanding colour wash that tints the existing UI."""

    DEFAULT_CSS = """
    FireworkOverlay {
        width: 1fr;
        height: 1fr;
        layer: notifications;
        background: #0a0e14;
    }
    """

    def __init__(self, burst_count: int = 12, duration: float = 2.5) -> None:
        super().__init__()
        self._burst_count = burst_count
        self._duration = duration
        self._seeds: list[_Seed] = []
        self._start = 0.0
        self._frame: list[Strip] = []
        self._timer = None
        self._dissolve: list[list[float]] = []
        self._dissolve_w = 0
        self._dissolve_h = 0
        self._initialized = False
        self._bg_cells: list[list[Segment]] = []

    def on_mount(self) -> None:
        self._start = time.monotonic()
        self._timer = self.set_interval(1.0 / 24, self._tick)

    def _lazy_init(self) -> None:
        W = max(1, self.size.width)
        H = max(1, self.size.height)
        if W <= 1 or H <= 1:
            return

        self._initialized = True
        self._capture_background(W, H)

        # Many overlapping seeds for a wash
        n = max(self._burst_count, 14)
        for _ in range(n):
            self._seeds.append(_Seed(
                x=random.uniform(1, W - 1),
                y=random.uniform(0, H),
                hue_offset=random.uniform(0, 1),
                delay=random.uniform(0, 0.4),
                speed=random.uniform(15, 30),
            ))

        self._dissolve_w = W
        self._dissolve_h = H
        self._dissolve = [
            [random.random() for _ in range(W)]
            for _ in range(H)
        ]

    def _capture_background(self, W: int, H: int) -> None:
        from textual.geometry import Region

        fallback_row = [Segment(" ", _BG_STYLE)] * W
        self._bg_cells = [list(fallback_row) for _ in range(H)]

        try:
            for widget in self.screen.walk_children():
                if widget is self:
                    continue
                try:
                    region = widget.region
                except Exception:
                    continue
                if region.width <= 0 or region.height <= 0:
                    continue
                try:
                    crop = Region(0, 0, region.width, region.height)
                    strips = widget.render_lines(crop)
                except Exception:
                    continue
                for local_y, strip in enumerate(strips):
                    screen_y = region.y + local_y
                    if screen_y < 0 or screen_y >= H:
                        continue
                    cells = _strip_to_cells(strip, region.width)
                    for local_x, cell in enumerate(cells):
                        screen_x = region.x + local_x
                        if 0 <= screen_x < W:
                            self._bg_cells[screen_y][screen_x] = cell
        except Exception:
            pass

    # ── animation ────────────────────────────────────────────

    @staticmethod
    def _noise(x: float, y: float, seed: float) -> float:
        v = math.sin(x * 0.7 + seed) * math.cos(y * 1.3 + seed * 0.7)
        v += math.sin((x + y) * 0.4 + seed * 1.1) * 0.5
        return v

    def _tick(self) -> None:
        if not self._initialized:
            self._lazy_init()

        elapsed = time.monotonic() - self._start
        if elapsed > self._duration:
            if self._timer:
                self._timer.stop()
            try:
                self.remove()
            except Exception:
                pass
            return

        self._build_frame(elapsed)
        self.refresh()

    def _build_frame(self, elapsed: float) -> None:
        W = max(1, self.size.width)
        H = max(1, self.size.height)

        if not self._seeds:
            return

        expand_end = self._duration * 0.35
        dissolve_start = self._duration * 0.55
        dissolve_end = self._duration
        hue_time = elapsed * 0.7

        # Max tint strength: ramps up, peaks at 0.85
        global_phase = elapsed / self._duration
        max_tint = min(0.85, global_phase * 2.5) if global_phase < 0.45 else 0.85

        frame: list[Strip] = []

        for y in range(H):
            segs: list[Segment] = []
            bg_row = self._bg_cells[y] if y < len(self._bg_cells) else None

            for x in range(W):
                # Accumulate blended intensity from all seeds
                total_intensity = 0.0
                weighted_hue = 0.0

                for seed in self._seeds:
                    t = elapsed - seed.delay
                    if t <= 0:
                        continue

                    # Aspect-corrected distance (terminal chars ~2:1)
                    dx = (x - seed.x) / 2.0
                    dy = y - seed.y
                    dist = math.sqrt(dx * dx + dy * dy)

                    # Radius with ease-out expansion, capped after expand_end
                    if t < expand_end:
                        expand_t = t / expand_end
                        radius = seed.speed * expand_end * (1.0 - (1.0 - expand_t) ** 2.0)
                    else:
                        radius = seed.speed * expand_end

                    if radius < 0.5:
                        continue

                    # Irregular edge: noise warps the effective radius
                    noise = self._noise(x * 0.3, y * 0.5, seed.noise_seed)
                    warped_radius = radius * (1.0 + noise * 0.3)

                    if dist > warped_radius:
                        continue

                    # Smooth cubic falloff
                    depth = max(0.0, 1.0 - (dist / warped_radius))
                    intensity = depth * depth * depth

                    # Continuous hue: seed offset + distance shift + time shift
                    hue = seed.hue_offset + dist * 0.01 + hue_time
                    total_intensity += intensity
                    weighted_hue += hue * intensity

                # Get the original background cell
                if bg_row and x < len(bg_row):
                    bg_cell = bg_row[x]
                else:
                    bg_cell = Segment(" ", _BG_STYLE)

                if total_intensity < 0.02:
                    # Not touched — pass through original
                    segs.append(bg_cell)
                    continue

                # Dissolve phase
                if elapsed > dissolve_start:
                    dissolve_t = (elapsed - dissolve_start) / (dissolve_end - dissolve_start)
                    threshold = dissolve_t ** 1.2

                    if y < self._dissolve_h and x < self._dissolve_w:
                        cell_rand = self._dissolve[y][x]
                    else:
                        cell_rand = random.random()

                    # Low-intensity cells dissolve first
                    fade_bias = 1.0 - min(1.0, total_intensity)
                    adjusted = cell_rand * (0.3 + 0.7 * fade_bias)
                    if adjusted < threshold:
                        segs.append(bg_cell)
                        continue

                # Bloom colour (lightly desaturated toward app bg)
                avg_hue = weighted_hue / total_intensity
                br, bg_, bb = _lerp_color(avg_hue)
                br = _blend(br, _BG_R, 0.15)
                bg_ = _blend(bg_, _BG_G, 0.15)
                bb = _blend(bb, _BG_B, 0.15)

                # Tint strength: intensity * max_tint
                tint = min(max_tint, total_intensity * 0.9)

                # Read original cell colours
                orig_bg = _extract_rgb(bg_cell.style, 'bgcolor')
                orig_fg = _extract_rgb(bg_cell.style, 'color')

                # Blend original bg toward bloom colour
                new_bg_r = _blend(orig_bg[0], br, tint)
                new_bg_g = _blend(orig_bg[1], bg_, tint)
                new_bg_b = _blend(orig_bg[2], bb, tint)

                # Brighten fg slightly so text pops against the tinted bg
                fg_boost = tint * 0.3
                new_fg_r = min(255, int(orig_fg[0] + (255 - orig_fg[0]) * fg_boost))
                new_fg_g = min(255, int(orig_fg[1] + (255 - orig_fg[1]) * fg_boost))
                new_fg_b = min(255, int(orig_fg[2] + (255 - orig_fg[2]) * fg_boost))

                style = _tint_style(
                    new_fg_r, new_fg_g, new_fg_b,
                    new_bg_r, new_bg_g, new_bg_b,
                )
                # Keep the original character
                segs.append(Segment(bg_cell.text, style))

            frame.append(Strip(segs))

        self._frame = frame

    def render_line(self, y: int) -> Strip:
        if y < len(self._frame):
            return self._frame[y]
        return Strip.blank(self.size.width)
