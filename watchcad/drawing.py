"""Vector drawings without a CAD or PDF dependency.

Three views are generated from the same solved spec and stack levels:

  front    dial-side plan with the case, dial furniture and hands at 10:10
  section  axial section through the case showing every layer and its height
  train    movement plan with arbors, pitch circles and the tooth table

Each view is built once as a small list of primitives (lines, polygons, circles,
text) and then rendered to SVG, PDF or DXF.  Dimensions are in millimetres at
1:1; the PDF page adds a title block and is sized to the drawing.
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field

from .modules import MODULES
from .schema import Spec

# ---------------------------------------------------------------------------
# Drawing primitives
# ---------------------------------------------------------------------------

@dataclass
class Canvas:
    width: float
    height: float
    title: str = ""
    scale: str = "1:1"
    background: str = "#ffffff"
    items: list[dict] = field(default_factory=list)

    # -- primitives -------------------------------------------------------
    def line(self, a, b, stroke="#1b1f24", w=0.18, dash=None):
        self.items.append(dict(t="line", a=tuple(a), b=tuple(b), stroke=stroke, w=w, dash=dash))
        return self

    def poly(self, pts, close=True, fill=None, stroke="#1b1f24", w=0.18, dash=None, evenodd=False):
        self.items.append(dict(t="poly", pts=[tuple(p) for p in pts], close=close, fill=fill,
                               stroke=stroke, w=w, dash=dash, evenodd=evenodd))
        return self

    def rect(self, x, y, w, h, fill=None, stroke="#1b1f24", sw=0.18, dash=None):
        return self.poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
                         fill=fill, stroke=stroke, w=sw, dash=dash)

    def circle(self, c, r, fill=None, stroke="#1b1f24", w=0.18, dash=None):
        self.items.append(dict(t="circle", c=tuple(c), r=float(r), fill=fill, stroke=stroke,
                               w=w, dash=dash))
        return self

    def text(self, p, s, size=2.4, fill="#1b1f24", anchor="start", bold=False, angle=0.0):
        self.items.append(dict(t="text", p=tuple(p), s=str(s), size=size, fill=fill,
                               anchor=anchor, bold=bold, angle=angle))
        return self

    def hatch(self, x, y, w, h, step=1.4, stroke="#7b838c", sw=0.12, angle=45):
        """Diagonal hatching clipped to a rectangle (a cut-metal convention)."""
        a = math.radians(angle)
        dx, dy = math.cos(a), math.sin(a)
        # project corners onto the direction normal to the hatch
        nx, ny = -dy, dx
        projections = [x * nx + y * ny, (x + w) * nx + y * ny,
                       x * nx + (y + h) * ny, (x + w) * nx + (y + h) * ny]
        lo, hi = min(projections), max(projections)
        t = math.ceil(lo / step) * step
        while t < hi:
            # line: p = t*n + s*d ; clip against rectangle
            pts = []
            for s in (-2000.0, 2000.0):
                px, py = t * nx + s * dx, t * ny + s * dy
                if x - 0.01 <= px <= x + w + 0.01 and y - 0.01 <= py <= y + h + 0.01:
                    pts.append((px, py))
            if len(pts) == 2:
                self.line(pts[0], pts[1], stroke=stroke, w=sw)
            t += step
        return self

    # -- annotations ------------------------------------------------------
    def dim_h(self, x1, x2, y, label, offset=0.0, size=2.1):
        y = y + offset
        self.line((x1, y), (x2, y), stroke="#7b838c", w=0.12)
        for x, direction in ((x1, -1), (x2, 1)):
            self.line((x, y), (x + direction * 0.9, y + 0.5), stroke="#7b838c", w=0.1)
            self.line((x, y), (x + direction * 0.9, y - 0.5), stroke="#7b838c", w=0.1)
        self.text(((x1 + x2) / 2, y + 0.9), label, size=size, anchor="middle", fill="#4a525c")
        return self

    def dim_v(self, x, y1, y2, label, offset=0.0, size=2.1):
        x = x + offset
        self.line((x, y1), (x, y2), stroke="#7b838c", w=0.12)
        for y, direction in ((y1, -1), (y2, 1)):
            self.line((x, y), (x + 0.5, y + direction * 0.9), stroke="#7b838c", w=0.1)
            self.line((x, y), (x - 0.5, y + direction * 0.9), stroke="#7b838c", w=0.1)
        self.text((x + 0.9, (y1 + y2) / 2), label, size=size, fill="#4a525c", angle=90)
        return self

    def leader(self, tip, elbow, label, size=2.1, anchor="start"):
        self.line(tip, elbow, stroke="#7b838c", w=0.1)
        self.circle(tip, 0.18, fill="#7b838c", stroke=None)
        dx = 1 if anchor == "start" else -1
        self.line(elbow, (elbow[0] + dx * 1.6, elbow[1]), stroke="#7b838c", w=0.1)
        self.text((elbow[0] + dx * 2.0, elbow[1] - 0.5), label, size=size, fill="#4a525c",
                  anchor=anchor)
        return self


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

CASE_INK = "#3b4450"
DIAL_INK = "#222932"
METAL = "#c9ced6"
ACCENT = "#b8523a"
BLUE = "#2f5f8f"
GOLD = "#b08a3e"


def _hex_ok(color: str | None, fallback="#c9ced6") -> str:
    if color and color.startswith("#") and len(color) == 7:
        return color
    return fallback


def _polar(cx, cy, r, deg):
    a = math.radians(deg)
    return (cx + r * math.cos(a), cy + r * math.sin(a))


def _polyline_rect(cx, cy, length, width, angle, tail=0.0):
    """Outline of a hand: a tapered bar with a rounded boss end."""
    a = math.radians(angle)
    c, s = math.cos(a), math.sin(a)
    w = width / 2
    pts = [(-tail, -w * 0.55), (0.0, -w), (length * 0.72, -w * 0.55),
           (length, -w * 0.18), (length, w * 0.18), (length * 0.72, w * 0.55),
           (0.0, w), (-tail, w * 0.55)]
    out = []
    for x, y in pts:
        out.append((cx + x * c - y * s, cy + x * s + y * c))
    return out


def _hand(can: Canvas, cx, cy, length, width, angle, fill="#e9edf2", stroke="#6c7580",
          tail=0.0):
    can.poly(_polyline_rect(cx, cy, length, width, angle, tail), fill=fill, stroke=stroke, w=0.12)
    # lume channel
    inner = _polyline_rect(cx, cy, length * 0.78, width * 0.38, angle, 0)
    can.poly(inner, fill="#e8e2c8", stroke=None, w=0)


def _dial_color(name: str) -> str:
    low = (name or "").lower()
    table = {
        "black": "#14171c", "blue": "#1c3f6e", "green": "#204436", "white": "#e9e6dd",
        "silver": "#c6c8cb", "grey": "#6a7078", "gray": "#6a7078", "salmon": "#c98d72",
        "brown": "#5b4032", "red": "#7a2126", "champagne": "#d8c49a", "panda": "#e6e2d8",
    }
    for key, color in table.items():
        if key in low:
            return color
    return "#2b3037"


def _dark_text_on(color: str) -> str:
    color = _hex_ok(color, "#333333").lstrip("#")
    r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    return "#1b1f24" if lum > 140 else "#eef1f4"


def _fmt(v, digits=1):
    return f"{v:.{digits}f}"


# ---------------------------------------------------------------------------
# Front view
# ---------------------------------------------------------------------------

def front_view(spec: Spec) -> Canvas:
    d = float(spec.get("case.diameter_mm") or 40)
    l2l = float(spec.get("case.lug_to_lug_mm") or 48)
    lug_w = float(spec.get("case.lug_width_mm") or 20)
    dial_d = float(spec.get("dial.diameter_mm") or 36)
    r = d / 2
    dr = dial_d / 2
    W = max(l2l, d) + 26
    H = l2l + 22
    can = Canvas(W, H, "Front view", "1:1")
    cx, cy = W / 2, H / 2 + 1
    case_fill = "#dfe4ea"
    bezel_fill = "#c3cad2"
    dial_fill = _dial_color(spec.get("dial.color"))
    text_ink = _dark_text_on(dial_fill)

    # lugs behind the case
    ext = max(2.0, (l2l - d) / 2 + 1.5)
    for side in (-1, 1):
        for lx in (-1, 1):
            x = cx + lx * lug_w / 4.0
            y0 = cy + side * (r - 2.2)
            y1 = cy + side * (r + ext)
            w = lug_w / 2.1
            can.poly([(x - w / 2, y0), (x + w / 2, y0),
                      (x + w * 0.36, y1), (x - w * 0.36, y1)],
                     fill=case_fill, stroke=CASE_INK, w=0.25)
    # spring bars
    for side in (-1, 1):
        y = cy + side * (l2l / 2 - 1.2)
        can.line((cx - lug_w / 2 + 0.2, y), (cx + lug_w / 2 - 0.2, y),
                 stroke="#8b939c", w=0.7)

    # crown and pushers
    crown_r = float(spec.get("case.crown_diameter_mm") or 5.5) / 2
    can.rect(cx + r - 0.2, cy - crown_r, 0.8, crown_r * 2, fill="#9aa3ac", stroke=CASE_INK, sw=0.2)
    can.rect(cx + r + 0.6, cy - crown_r, crown_r * 1.15, crown_r * 2,
             fill=case_fill, stroke=CASE_INK, sw=0.25)
    for i in range(6):
        y = cy - crown_r + 0.35 + i * (crown_r * 2 - 0.7) / 5
        can.line((cx + r + 0.65, y), (cx + r + 0.6 + crown_r * 1.1, y),
                 stroke="#8b939c", w=0.1)
    if "chronograph" in (spec.get("movement.complications") or {}):
        for py in (-5.2, 5.2):
            can.circle((cx + r + 1.0, cy + py), 1.1, fill=case_fill, stroke=CASE_INK, w=0.25)

    # case and bezel
    can.circle((cx, cy), r, fill=case_fill, stroke=CASE_INK, w=0.4)
    can.circle((cx, cy), r - 0.55, fill=bezel_fill, stroke="#8e98a3", w=0.15)
    bezel_bev = (r - 0.55 + dr + 0.2) / 2

    bezel = spec.get("case.bezel")
    if bezel == "dive":
        for i in range(60):
            a = i * 6
            p1 = _polar(cx, cy, r - 0.7, a)
            p2 = _polar(cx, cy, r - 1.35 if i % 5 else r - 1.6, a)
            can.line(p1, p2, stroke="#4d5662", w=0.14 if i % 5 else 0.3)
        for i, label in ((0, "0"), (15, "15"), (30, "30"), (45, "45")):
            p = _polar(cx, cy, bezel_bev, 90 - i)
            can.text(p, label, size=2.0, fill="#f2f4f6", anchor="middle", bold=True)
    elif bezel == "coin-edge":
        for i in range(90):
            a = i * 4
            p1 = _polar(cx, cy, r - 0.15, a)
            p2 = _polar(cx, cy, r - 0.7, a + 1.4)
            can.line(p1, p2, stroke="#9aa3ac", w=0.12)

    # dial
    can.circle((cx, cy), dr + 0.18, fill="#7f8892", stroke="#5a636d", w=0.12)
    can.circle((cx, cy), dr, fill=dial_fill, stroke="#20252b", w=0.18)

    # minute track
    track = dr * 0.955
    for i in range(60):
        a = 90 - i * 6
        long = i % 5 == 0
        p1 = _polar(cx, cy, track, a)
        p2 = _polar(cx, cy, track - (1.8 if long else 0.9), a)
        can.line(p1, p2, stroke=text_ink, w=0.2 if long else 0.1)

    # indices or numerals
    style = spec.get("dial.index_style")
    index_count = 12
    if style != "no indices":
        if style == "roman numerals":
            numerals = ["XII", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI"]
            for i, numeral in enumerate(numerals):
                p = _polar(cx, cy, dr * 0.80, 90 - i * 30)
                can.text(p, numeral, size=3.2, fill=text_ink, anchor="middle", bold=True)
        else:
            for i in range(index_count):
                a = 90 - i * 30
                length = 2.6 if i % 3 == 0 else 1.8
                width = 0.7 if i % 3 == 0 else 0.45
                p1 = _polar(cx, cy, dr - 2.2, a - math.degrees(width / dr / 2))
                p2 = _polar(cx, cy, dr - 2.2 - length, a - math.degrees(width / dr / 2))
                p3 = _polar(cx, cy, dr - 2.2 - length, a + math.degrees(width / dr / 2))
                p4 = _polar(cx, cy, dr - 2.2, a + math.degrees(width / dr / 2))
                fill = "#e9edf2" if style == "applied batons" else text_ink
                can.poly([p1, p2, p3, p4], fill=fill, stroke="#7f8892" if style == "applied batons" else None, w=0.1)
    else:
        for i in range(12):
            a = 90 - i * 30
            p = _polar(cx, cy, dr * 0.8, a)
            can.circle(p, 0.35, fill=text_ink)

    # date window at 3 o'clock
    comps = spec.get("movement.complications") or {}
    if spec.get("dial.date_window") or "date" in comps or "day-date" in comps:
        wx, wy = cx + dr * 0.60, cy
        can.rect(wx - 2.0, wy - 1.5, 4.0, 3.0, fill="#f0ede4", stroke="#8e98a3", sw=0.2)
        can.text((wx, wy), "10", size=2.6, fill="#20252b", anchor="middle", bold=True)
        if "day-date" in comps:
            can.text((wx, wy + 2.0), "TUE", size=1.5, fill="#20252b", anchor="middle")

    # complications
    if "moonphase" in comps:
        ax, ay = cx, cy - dr * 0.42
        can.circle((ax, ay), 4.2, fill="#f0ede4", stroke="#8e98a3", w=0.25)
        can.circle((ax, ay), 3.9, fill="#1d2b45", stroke=None)
        can.circle((ax - 1.3, ay + 0.3), 1.1, fill="#e6dfc4", stroke=None)
        can.circle((ax + 1.4, ay - 0.4), 1.0, fill="#e6dfc4", stroke="#1d2b45", w=0.1)
        can.text((ax, ay - 3.4), "MOON", size=1.3, fill=text_ink, anchor="middle")
    if "power-reserve" in comps:
        arc = []
        for i in range(25):
            deg = 200 - i * (140 / 24)
            arc.append(_polar(cx, cy, dr * 0.66, deg))
        can.poly(arc, close=False, stroke=text_ink, w=0.4)
        for i in range(5):
            deg = 200 - i * 35
            p1 = _polar(cx, cy, dr * 0.66, deg)
            p2 = _polar(cx, cy, dr * 0.72, deg)
            can.line(p1, p2, stroke=text_ink, w=0.2)
        can.text(_polar(cx, cy, dr * 0.55, 250), "42h", size=1.4, fill=text_ink, anchor="middle")
    if "gmt" in comps:
        _hand(can, cx, cy, dr * 0.82, 2.2, 200, fill="#b03a3a", stroke="#6e2424", tail=3.0)
        can.poly([_polar(cx, cy, dr * 0.82, 200), _polar(cx, cy, dr * 0.86, 195),
                  _polar(cx, cy, dr * 0.86, 205)], fill="#b03a3a", stroke=None)
    if "chronograph" in comps:
        for cxx, cyy in ((-dr * 0.48, dr * 0.42), (dr * 0.48, dr * 0.42)):
            can.circle((cx + cxx, cy + cyy), dr * 0.20, fill="none", stroke=text_ink, w=0.18)
            for i in range(12):
                p1 = _polar(cx + cxx, cy + cyy, dr * 0.20, i * 30)
                p2 = _polar(cx + cxx, cy + cyy, dr * 0.17, i * 30)
                can.line(p1, p2, stroke=text_ink, w=0.1)
    if "tourbillon" in comps:
        ax, ay = cx, cy - dr * 0.30
        can.circle((ax, ay), dr * 0.26, fill="#0f1318", stroke="#8e98a3", w=0.3)
        for i in range(3):
            p1 = _polar(ax, ay, dr * 0.05, i * 120)
            p2 = _polar(ax, ay, dr * 0.23, i * 120 + 15)
            can.line(p1, p2, stroke=GOLD, w=0.5)
        can.circle((ax, ay), dr * 0.26, fill="none", stroke=GOLD, w=0.2)
    if "perpetual-calendar" in comps or "annual-calendar" in comps:
        for i, label in enumerate(("JAN", "APR", "JUL", "OCT")):
            p = _polar(cx, cy, dr * 0.52, 90 - i * 90)
            can.text(p, label, size=1.2, fill=text_ink, anchor="middle")

    # hands at 10:10:37
    hour_len = float(spec.get("hands.hour_length_mm") or dr * 0.55)
    minute_len = float(spec.get("hands.minute_length_mm") or dr * 0.88)
    second_len = float(spec.get("hands.second_length_mm") or dr * 0.92)
    _hand(can, cx, cy, hour_len, 3.4, 145, tail=3.0)
    _hand(can, cx, cy, minute_len, 2.5, 30, tail=3.0)
    sec = _polyline_rect(cx, cy, second_len, 0.55, -132, tail=3.6)
    can.poly(sec, fill="#b03a3a", stroke="#6e2424", w=0.1)
    can.circle((cx, cy), 0.85, fill="#d7dde3", stroke="#6c7580", w=0.12)
    can.circle((cx, cy), 0.32, fill="#6c7580", stroke=None)

    # signature
    logo = spec.get("dial.logo_text") or spec.get("meta.name") or ""
    if logo:
        can.text((cx, cy + dr * 0.42), logo[:26], size=2.0, fill=text_ink, anchor="middle", bold=True)
    can.text((cx, cy + dr * 0.33), "AUTOMATIC" if spec.get("movement.winding") == "automatic"
             and spec.get("movement.kind") == "mechanical" else
             ("QUARTZ" if spec.get("movement.kind") == "quartz" else "MECHANICAL"),
             size=1.2, fill=text_ink, anchor="middle")

    # dimensions
    can.dim_h(cx - r, cx + r, cy - r - 4.0, f"\u00d8 {_fmt(d)} mm")
    can.dim_v(cx - r - 4.0, cy - l2l / 2, cy + l2l / 2, f"L2L {_fmt(l2l)} mm")
    can.text((W / 2, H - 3.0), "FRONT VIEW", size=3.0, anchor="middle", bold=True)
    can.text((W / 2, H - 6.2), f"{spec.get('meta.name')}", size=2.4, anchor="middle", fill="#4a525c")
    can.text((W - 2.0, 2.4), f"scale {can.scale}  \u00b7  mm", size=1.8, anchor="end", fill="#7b838c")
    return can


# ---------------------------------------------------------------------------
# Section view
# ---------------------------------------------------------------------------

def section_view(spec: Spec) -> Canvas:
    d = float(spec.get("case.diameter_mm") or 40)
    r = d / 2
    stack = spec.get("derived.stack") or {}
    thickness = float(spec.get("case.thickness_mm") or 11)
    W = d + 52
    H = thickness + 22
    can = Canvas(W, H, "Section", "1:1")
    cx = (W - 12) / 2
    base = 10.0

    def y(z):
        return base + z

    zc = stack.get("z_caseback_top", 1.2)
    zb = stack.get("z_plate_top", 2.4)
    zm = stack.get("z_movement_top", 5.8)
    zct = stack.get("z_complications_top", zm)
    zdb = stack.get("z_dial_bottom", zct)
    zdt = stack.get("z_dial_top", zdb + 0.4)
    zhb = stack.get("z_hands_bottom", zdt + 0.3)
    zht = stack.get("z_hands_top", zhb + 0.9)
    zcb = stack.get("z_crystal_bottom", thickness - 1.2)
    ztop = stack.get("z_case_top", thickness)
    dial_r = float(spec.get("dial.diameter_mm") or 36) / 2
    mv_r = float(spec.get("movement.width_mm") or 28) / 2
    wall = 1.6
    case_fill = "#e4e8ed"
    metal = "#c3cad2"

    # caseback
    can.rect(cx - r, y(0), 2 * r, zc, fill=case_fill, stroke=CASE_INK, sw=0.25)
    can.hatch(cx - r, y(0), 2 * r, zc)
    can.rect(cx - r + 0.5, y(0.15), 2 * r - 1.0, 0.35, fill="#adb5bf", stroke=None)

    # mid case walls
    left_wall = [(cx - r, y(zc)), (cx - r + wall, y(zc)), (cx - r + wall, y(ztop - 1.6)),
                 (cx - r, y(ztop - 1.6))]
    right_wall = [(cx + r - wall, y(zc)), (cx + r, y(zc)), (cx + r, y(ztop - 1.6)),
                  (cx + r - wall, y(ztop - 1.6))]
    for wallpoly in (left_wall, right_wall):
        can.poly(wallpoly, fill=case_fill, stroke=CASE_INK, w=0.25)
        xs = [p[0] for p in wallpoly]
        ys = [p[1] for p in wallpoly]
        can.hatch(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    # bezel
    bez = [(cx - r, y(ztop - 1.6)), (cx - r + wall, y(ztop - 1.6)),
           (cx - dial_r - 0.2, y(ztop)), (cx - r, y(ztop))]
    bez_r = [(cx + r - wall, y(ztop - 1.6)), (cx + r, y(ztop - 1.6)),
             (cx + r, y(ztop)), (cx + dial_r + 0.2, y(ztop))]
    for bp in (bez, bez_r):
        can.poly(bp, fill=metal, stroke=CASE_INK, w=0.25)
        xs = [p[0] for p in bp]
        ys = [p[1] for p in bp]
        can.hatch(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys), step=1.0)

    # crystal
    crystal = [(cx - dial_r - 0.2, y(zcb)), (cx + dial_r + 0.2, y(zcb)),
               (cx + dial_r + 0.2, y(ztop - 0.1)), (cx - dial_r - 0.2, y(ztop - 0.1))]
    can.poly(crystal, fill="#d7ebf3", stroke="#5f8fa5", w=0.3)
    can.circle((cx, y(ztop + 0.0)), 0.1, fill="none", stroke=None)

    # movement: plate + components
    can.rect(cx - mv_r, y(zc), 2 * mv_r, 0.35, fill="#8f7748", stroke=CASE_INK, sw=0.2)
    can.rect(cx - mv_r + 0.6, y(zc + 0.35), 2 * mv_r - 1.2, zm - zc - 0.7,
             fill="#d8c9a2", stroke="#8f7748", sw=0.2)
    can.hatch(cx - mv_r + 0.6, y(zc + 0.35), 2 * mv_r - 1.2, zm - zc - 0.7,
              step=2.0, stroke="#b7a678", sw=0.1)
    # barrel and bridge silhouettes in section
    barrel_d = float((spec.get("derived.barrel") or {}).get("inner_diameter_mm", 9) or 9)
    can.rect(cx - barrel_d / 2 - 2.0, y(zc + 0.5), barrel_d, zm - zc - 1.0,
             fill="#c9b07a", stroke="#8f7748", sw=0.2)
    can.rect(cx + mv_r * 0.15, y(zc + 0.5), mv_r * 0.55, 0.8,
             fill="#b9c0c8", stroke="#79828c", sw=0.15)
    # balance wheel section
    bal_cx = cx - mv_r * 0.38
    can.rect(bal_cx - 3.0, y(zc + 1.5), 6.0, 0.5, fill="#d4b86a", stroke="#8f7748", sw=0.15)

    # complications
    if zct > zm:
        can.rect(cx - mv_r + 0.4, y(zm), 2 * mv_r - 0.8, zct - zm,
                 fill="#a9c4b0", stroke="#5f7a68", sw=0.15)

    # dial
    can.rect(cx - dial_r, y(zdb), 2 * dial_r, zdt - zdb,
             fill=_dial_color(spec.get("dial.color")), stroke=CASE_INK, sw=0.2)

    # hands (section: two thin blocks)
    can.rect(cx - 2.2, y(zhb), 4.4, zht - zhb, fill="#e9edf2", stroke="#6c7580", sw=0.15)
    can.rect(cx + 3.0, y(zhb + 0.05), 9.0, 0.22, fill="#e9edf2", stroke="#6c7580", sw=0.1)
    can.rect(cx - 12.0, y(zhb + 0.32), 7.0, 0.2, fill="#e9edf2", stroke="#6c7580", sw=0.1)

    # crown section
    crown_r = float(spec.get("case.crown_diameter_mm") or 5.5) / 2
    can.rect(cx + r, y(thickness * 0.45 - crown_r), 2.4, crown_r * 1.2,
             fill=metal, stroke=CASE_INK, sw=0.2)
    can.rect(cx + r + 0.9, y(thickness * 0.45 - crown_r), 2.0, crown_r * 2.4,
             fill=case_fill, stroke=CASE_INK, sw=0.2)

    # dimension chains on the left
    lx = cx - r - 4.0
    can.line((lx, y(0)), (lx, y(ztop)), stroke="#7b838c", w=0.12)
    for z, label in ((0, "0"), (zc, f"caseback {zc:.1f}"), (zm, f"movement {zm:.1f}"),
                     (zdb, f"dial {zdb:.1f}"), (zht, f"hands {zht:.1f}"),
                     (ztop, f"{ztop:.1f}")):
        can.line((lx - 0.8, y(z)), (lx + 0.8, y(z)), stroke="#7b838c", w=0.1)
        can.text((lx - 1.2, y(z) - 0.5), label, size=1.6, anchor="end", fill="#4a525c")
    can.text((lx - 13.0, y(ztop / 2)), f"THICKNESS {_fmt(thickness)} mm", size=2.1,
             fill="#4a525c", angle=90)

    # leaders on the right
    can.leader((cx + dial_r * 0.55, y((zcb + ztop) / 2)), (cx + r + 7.0, y(ztop - 1.4)),
               "crystal", anchor="start")
    can.leader((cx + dial_r * 0.4, y((zdb + zdt) / 2)), (cx + r + 7.0, y(zdt)),
               "dial", anchor="start")
    can.leader((cx + 3.0, y(zht - 0.1)), (cx + r + 7.0, y(zht + 0.1)),
               "hands", anchor="start")
    can.leader((cx - mv_r * 0.3, y((zc + zm) / 2)), (cx + r + 7.0, y(zm - 0.6)),
               "movement stack", anchor="start")

    can.text((W / 2, H - 3.0), "SECTION A-A", size=3.0, anchor="middle", bold=True)
    can.text((W / 2, H - 6.2), f"{spec.get('meta.name')}  \u00b7  axial section, all layers at 1:1",
             size=2.0, anchor="middle", fill="#4a525c")
    can.text((W - 2.0, 2.4), "mm", size=1.8, anchor="end", fill="#7b838c")
    return can


# ---------------------------------------------------------------------------
# Train view
# ---------------------------------------------------------------------------

def train_view(spec: Spec) -> Canvas:
    train = spec.get("derived.train") or {}
    layout = train.get("layout") or []
    mv_w = float(spec.get("movement.width_mm") or 28)
    r = mv_w / 2
    W = mv_w + 34
    H = mv_w + 30
    can = Canvas(W, H, "Train", "1:1")
    cx, cy = W / 2 - 2, H / 2 + 1

    # plate
    can.circle((cx, cy), r, fill="#efe4c8", stroke="#8f7748", w=0.4)
    can.circle((cx, cy), r - 0.7, fill="#f6eed8", stroke="#c9b88e", w=0.15)
    # plate screws
    for deg in (45, 135, 225, 315):
        p = _polar(cx, cy, r - 1.4, deg)
        can.circle(p, 0.55, fill="#b9c0c8", stroke="#6c7580", w=0.1)
        can.line((p[0] - 0.3, p[1]), (p[0] + 0.3, p[1]), stroke="#6c7580", w=0.12)
    # centre cross lines
    can.line((cx - r, cy), (cx + r, cy), stroke="#d6c9a4", w=0.1)
    can.line((cx, cy - r), (cx, cy + r), stroke="#d6c9a4", w=0.1)

    barrel = spec.get("derived.barrel") or {}
    bd = float(barrel.get("inner_diameter_mm", 9) or 9)
    pos = {n["name"]: n for n in layout}
    if "barrel" in pos:
        b = pos["barrel"]
        can.circle((cx + b["x"], cy + b["y"]), bd / 2, fill="#e3d3a8", stroke="#8f7748", w=0.3)
        can.circle((cx + b["x"], cy + b["y"]), bd / 2 - 0.5, fill="none", stroke="#b7a678", w=0.15)
        can.circle((cx + b["x"], cy + b["y"]), bd / 6, fill="#b9c0c8", stroke="#6c7580", w=0.15)

    palette = {"barrel": "#c8a24a", "centre": "#b08a3e", "third": "#c8a24a",
               "fourth": "#b08a3e", "escape": "#d8c07a"}
    for node in layout:
        p = (cx + node["x"], cy + node["y"])
        radius = node.get("radius", 3.0)
        can.circle(p, radius, fill=None, stroke=palette.get(node["name"], "#9aa3ac"), w=0.2,
                   dash=[1.2, 0.6])
        can.circle(p, 0.35, fill="#4a525c", stroke=None)
        can.circle(p, 0.9, fill="none", stroke="#7b838c", w=0.12)
        # label
        can.text((p[0], p[1] + radius + 1.2), node["name"].upper(), size=1.7,
                 anchor="middle", fill="#4a525c")

    # meshing chain arrows
    stages = train.get("stages") or []
    for st in stages:
        a = pos.get(st["driver"])
        b = pos.get(st["driven"])
        if not a or not b:
            continue
        p1 = (cx + a["x"], cy + a["y"])
        p2 = (cx + b["x"], cy + b["y"])
        can.line(p1, p2, stroke="#4a525c", w=0.18, dash=[0.7, 0.5])
        ang = math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0]))
        tip = _polar(p2[0], p2[1], 0.9, ang + 180)
        can.poly([tip, _polar(tip[0], tip[1], 1.0, ang + 180 - 18),
                  _polar(tip[0], tip[1], 1.0, ang + 180 + 18)], fill="#4a525c", stroke=None)

    # balance wheel at a plausible free spot
    bal = (cx - r * 0.45, cy - r * 0.55)
    can.circle(bal, 3.1, fill="none", stroke=GOLD, w=0.4)
    can.circle(bal, 2.6, fill="none", stroke=GOLD, w=0.15, dash=[0.5, 0.4])
    can.circle(bal, 0.4, fill="#4a525c", stroke=None)
    can.text((bal[0], bal[1] - 4.2), "BALANCE", size=1.5, anchor="middle", fill="#4a525c")

    # tooth table
    x0 = W - 15.0
    y0 = 15.0
    can.text((x0, y0 + 11.0), "TRAIN", size=2.2, bold=True)
    can.line((x0, y0 + 10.0), (W - 2.0, y0 + 10.0), stroke="#7b838c", w=0.15)
    can.text((x0, y0 + 8.0), "mesh", size=1.6, fill="#4a525c")
    can.text((x0 + 6.5, y0 + 8.0), "teeth", size=1.6, fill="#4a525c")
    can.text((x0 + 10.6, y0 + 8.0), "c-c", size=1.6, fill="#4a525c")
    for i, st in enumerate(stages):
        yy = y0 + 6.2 - i * 1.9
        can.text((x0, yy), f"{st['driver'][:4]}->{st['driven'][:5]}", size=1.45, fill="#30363d")
        can.text((x0 + 6.5, yy), f"{st['wheel_teeth']}/{st['pinion_teeth']}", size=1.45, fill="#30363d")
        can.text((x0 + 10.6, yy), f"{st['centre_mm']:.2f}", size=1.45, fill="#30363d")
    if stages:
        can.text((x0, y0 - len(stages) * 1.9 + 3.6),
                 f"module {stages[0]['module_mm']:.3f} mm", size=1.5, fill="#4a525c")
    can.text((x0, y0 - len(stages) * 1.9 + 1.2),
             f"escape {train.get('escape_wheel_teeth', 15)} t", size=1.5, fill="#4a525c")

    can.dim_h(cx - r, cx + r, cy - r - 3.0, f"\u00d8 {_fmt(mv_w)} mm movement")
    can.text((W / 2, H - 3.0), "MOVEMENT / TRAIN", size=3.0, anchor="middle", bold=True)
    can.text((W / 2, H - 6.2),
             f"{train.get('beat_rate_bph', 0):.0f} bph  \u00b7  "
             f"{len(stages)} meshes  \u00b7  exact tooth counts", size=2.0,
             anchor="middle", fill="#4a525c")
    return can


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------

def _svg_escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def to_svg(can: Canvas) -> str:
    def X(x):
        return f"{x:.4f}"

    def Y(y):
        return f"{can.height - y:.4f}"

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{can.width}mm" '
           f'height="{can.height}mm" viewBox="0 0 {can.width} {can.height}" '
           f'font-family="Helvetica, Arial, sans-serif">']
    out.append(f'<title>{_svg_escape(can.title)}</title>')
    out.append(f'<rect x="0" y="0" width="{can.width}" height="{can.height}" fill="{can.background}"/>')
    for it in can.items:
        t = it["t"]
        if t == "line":
            dash = f' stroke-dasharray="{",".join(str(v) for v in it["dash"])}"' if it.get("dash") else ""
            out.append(f'<line x1="{X(it["a"][0])}" y1="{Y(it["a"][1])}" '
                       f'x2="{X(it["b"][0])}" y2="{Y(it["b"][1])}" '
                       f'stroke="{it["stroke"] or "none"}" stroke-width="{it["w"]}"{dash} '
                       f'stroke-linecap="round"/>')
        elif t == "poly":
            pts = " ".join(f"{X(x)},{Y(y)}" for x, y in it["pts"])
            fill = it["fill"] or "none"
            stroke = it["stroke"] or "none"
            dash = f' stroke-dasharray="{",".join(str(v) for v in it["dash"])}"' if it.get("dash") else ""
            rule = ' fill-rule="evenodd"' if it.get("evenodd") else ""
            tag = "polygon" if it["close"] else "polyline"
            out.append(f'<{tag} points="{pts}" fill="{fill}" stroke="{stroke}" '
                       f'stroke-width="{it["w"]}"{dash}{rule} stroke-linejoin="round"/>')
        elif t == "circle":
            dash = f' stroke-dasharray="{",".join(str(v) for v in it["dash"])}"' if it.get("dash") else ""
            out.append(f'<circle cx="{X(it["c"][0])}" cy="{Y(it["c"][1])}" r="{it["r"]}" '
                       f'fill="{it["fill"] or "none"}" stroke="{it["stroke"] or "none"}" '
                       f'stroke-width="{it["w"]}"{dash}/>')
        elif t == "text":
            x, y = it["p"]
            anchor = {"start": "start", "middle": "middle", "end": "end"}[it["anchor"]]
            weight = "bold" if it["bold"] else "normal"
            transform = ""
            if it.get("angle"):
                transform = f' transform="rotate({-it["angle"]} {X(x)} {Y(y)})"'
            out.append(f'<text x="{X(x)}" y="{Y(y)}" font-size="{it["size"]}" '
                       f'fill="{it["fill"]}" text-anchor="{anchor}" font-weight="{weight}"'
                       f'{transform}>{_svg_escape(it["s"])}</text>')
    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _pdf_rgb(color: str) -> tuple[float, float, float]:
    color = _hex_ok(color, "#000000").lstrip("#")
    return tuple(int(color[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _pdf_escape(s: str) -> str:
    return (str(s).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            .replace("\r", " ").replace("\n", " "))


def _text_width(s: str, size: float, bold: bool) -> float:
    # Helvetica advance widths are close enough to 0.52/0.56 em for a title block.
    return len(str(s)) * size * (0.56 if bold else 0.52)


def _pdf_content(can: Canvas) -> str:
    mm = 72.0 / 25.4
    ops: list[str] = []
    ops.append("1 J 1 j")
    for it in can.items:
        t = it["t"]
        if t == "line":
            r, g, b = _pdf_rgb(it["stroke"] or "#000000")
            ops.append(f"{r:.4f} {g:.4f} {b:.4f} RG {it['w'] * mm:.4f} w")
            if it.get("dash"):
                ops.append("[" + " ".join(f"{v * mm:.3f}" for v in it["dash"]) + "] 0 d")
            else:
                ops.append("[] 0 d")
            a, bb = it["a"], it["b"]
            ops.append(f"{a[0] * mm:.4f} {a[1] * mm:.4f} m {bb[0] * mm:.4f} {bb[1] * mm:.4f} l S")
        elif t == "poly":
            pts = it["pts"]
            if len(pts) < 2:
                continue
            ops.append("q")
            if it["fill"]:
                r, g, b = _pdf_rgb(it["fill"])
                ops.append(f"{r:.4f} {g:.4f} {b:.4f} rg")
            if it["stroke"]:
                r, g, b = _pdf_rgb(it["stroke"])
                ops.append(f"{r:.4f} {g:.4f} {b:.4f} RG {it['w'] * mm:.4f} w")
                if it.get("dash"):
                    ops.append("[" + " ".join(f"{v * mm:.3f}" for v in it["dash"]) + "] 0 d")
            x0, y0 = pts[0]
            ops.append(f"{x0 * mm:.4f} {y0 * mm:.4f} m")
            for x, y in pts[1:]:
                ops.append(f"{x * mm:.4f} {y * mm:.4f} l")
            if it["close"]:
                ops.append("h")
            if it["fill"] and it["stroke"]:
                ops.append("B")
            elif it["fill"]:
                ops.append("f" if not it.get("evenodd") else "f*")
            elif it["stroke"]:
                ops.append("S")
            else:
                ops.append("n")
            ops.append("Q")
        elif t == "circle":
            cx, cy = it["c"]
            rr = it["r"]
            k = 0.5522847498 * rr
            ops.append("q")
            if it["fill"]:
                r, g, b = _pdf_rgb(it["fill"])
                ops.append(f"{r:.4f} {g:.4f} {b:.4f} rg")
            if it["stroke"]:
                r, g, b = _pdf_rgb(it["stroke"])
                ops.append(f"{r:.4f} {g:.4f} {b:.4f} RG {it['w'] * mm:.4f} w")
                if it.get("dash"):
                    ops.append("[" + " ".join(f"{v * mm:.3f}" for v in it["dash"]) + "] 0 d")
            X, Y = cx * mm, cy * mm
            R = rr * mm
            K = k * mm
            ops.append(f"{X + R:.4f} {Y:.4f} m")
            ops.append(f"{X + R:.4f} {Y + K:.4f} {X + K:.4f} {Y + R:.4f} {X:.4f} {Y + R:.4f} c")
            ops.append(f"{X - K:.4f} {Y + R:.4f} {X - R:.4f} {Y + K:.4f} {X - R:.4f} {Y:.4f} c")
            ops.append(f"{X - R:.4f} {Y - K:.4f} {X - K:.4f} {Y - R:.4f} {X:.4f} {Y - R:.4f} c")
            ops.append(f"{X + K:.4f} {Y - R:.4f} {X + R:.4f} {Y - K:.4f} {X + R:.4f} {Y:.4f} c")
            if it["fill"] and it["stroke"]:
                ops.append("B")
            elif it["fill"]:
                ops.append("f")
            elif it["stroke"]:
                ops.append("S")
            else:
                ops.append("n")
            ops.append("Q")
        elif t == "text":
            x, y = it["p"]
            size = it["size"]
            bold = it["bold"]
            font = "F2" if bold else "F1"
            w = _text_width(it["s"], size, bold)
            dx = -w / 2 if it["anchor"] == "middle" else (-w if it["anchor"] == "end" else 0)
            r, g, b = _pdf_rgb(it["fill"])
            angle = it.get("angle", 0) or 0
            c = math.cos(math.radians(angle))
            s = math.sin(math.radians(angle))
            # translate to the text anchor, then rotate the local frame
            ops.append(f"BT {r:.4f} {g:.4f} {b:.4f} rg /{font} {size * mm:.4f} Tf")
            ops.append(f"{c:.5f} {s:.5f} {-s:.5f} {c:.5f} "
                       f"{(x + dx * c) * mm:.4f} {(y + dx * s) * mm:.4f} Tm")
            ops.append(f"({_pdf_escape(it['s'])}) Tj ET")
    return "\n".join(ops)


def write_pdf(canvases: list[Canvas], path: str, title: str = "watchcad drawing",
              meta: dict | None = None) -> str:
    """Write one page per canvas, with a small title block on every page."""
    mm = 72.0 / 25.4
    meta = meta or {}
    pages = []
    for can in canvases:
        w = can.width * mm
        h = can.height * mm
        content = _pdf_content(can)
        pages.append(content)

    n = len(pages)
    # object ids: 1 catalog, 2 pages, 3..2+2n pages+contents, fonts 3+2n, 4+2n
    font1 = 3 + 2 * n
    font2 = 4 + 2 * n
    objs: list[bytes] = []
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objs.append(f"<< /Type /Catalog /Pages 2 0 R >>".encode())
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    for i, content in enumerate(pages):
        page_id = 3 + 2 * i
        content_id = page_id + 1
        w = canvases[i].width * mm
        h = canvases[i].height * mm
        objs.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w:.4f} {h:.4f}] "
                     f"/Resources << /Font << /F1 {font1} 0 R /F2 {font2} 0 R >> >> "
                     f"/Contents {content_id} 0 R >>").encode())
        data = content.encode("latin-1", "replace")
        objs.append(f"<< /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for idx, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{idx} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    size = len(objs) + 1
    out += f"xref\n0 {size}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {size} /Root 1 0 R /Info << /Title ({_pdf_escape(title)}) "
            f"/Producer (watchcad) >> >>\nstartxref\n{xref}\n%%EOF\n").encode()
    data = bytes(out)
    if path:
        with open(path, "wb") as fh:
            fh.write(data)
    return path


# ---------------------------------------------------------------------------
# DXF (R12 ASCII)
# ---------------------------------------------------------------------------

def to_dxf(canvases: list[Canvas]) -> str:
    """ASCII DXF, one model-space drawing stacked vertically by page."""
    lines: list[str] = []

    def add(code, value):
        lines.append(str(code))
        lines.append(str(value))

    add(0, "SECTION")
    add(2, "HEADER")
    add(9, "$ACADVER")
    add(1, "AC1009")
    add(9, "$INSUNITS")
    add(70, 4)  # millimetres
    add(0, "ENDSEC")

    add(0, "SECTION")
    add(2, "TABLES")
    add(0, "TABLE")
    add(2, "LAYER")
    add(70, 4)
    for name, color in (("OUTLINE", 7), ("DIM", 8), ("TEXT", 7), ("HATCH", 9)):
        add(0, "LAYER")
        add(2, name)
        add(70, 0)
        add(62, color)
        add(6, "CONTINUOUS")
    add(0, "ENDTAB")
    add(0, "ENDSEC")

    add(0, "SECTION")
    add(2, "ENTITIES")
    offset_y = 0.0
    for can in canvases:
        for it in can.items:
            t = it["t"]
            if t == "line":
                add(0, "LINE")
                add(8, "HATCH" if it["w"] <= 0.13 else "OUTLINE")
                add(10, f"{it['a'][0]:.4f}")
                add(20, f"{it['a'][1] + offset_y:.4f}")
                add(30, 0)
                add(11, f"{it['b'][0]:.4f}")
                add(21, f"{it['b'][1] + offset_y:.4f}")
                add(31, 0)
            elif t == "poly":
                pts = it["pts"]
                count = len(pts) if it["close"] else len(pts) - 1
                for i in range(count):
                    a = pts[i]
                    b = pts[(i + 1) % len(pts)]
                    add(0, "LINE")
                    add(8, "OUTLINE")
                    add(10, f"{a[0]:.4f}")
                    add(20, f"{a[1] + offset_y:.4f}")
                    add(30, 0)
                    add(11, f"{b[0]:.4f}")
                    add(21, f"{b[1] + offset_y:.4f}")
                    add(31, 0)
            elif t == "circle":
                add(0, "CIRCLE")
                add(8, "OUTLINE")
                add(10, f"{it['c'][0]:.4f}")
                add(20, f"{it['c'][1] + offset_y:.4f}")
                add(30, 0)
                add(40, f"{it['r']:.4f}")
            elif t == "text":
                add(0, "TEXT")
                add(8, "TEXT")
                add(10, f"{it['p'][0]:.4f}")
                add(20, f"{it['p'][1] + offset_y:.4f}")
                add(30, 0)
                add(40, f"{it['size']:.3f}")
                add(1, str(it["s"]))
                add(50, f"{it.get('angle', 0):.2f}")
                add(72, {"start": 0, "middle": 1, "end": 2}[it["anchor"]])
                add(11, f"{it['p'][0]:.4f}")
                add(21, f"{it['p'][1] + offset_y:.4f}")
                add(31, 0)
        offset_y += can.height + 10.0
    add(0, "ENDSEC")
    add(0, "EOF")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Notes, tolerances and revision block
# ---------------------------------------------------------------------------

def _table(can: Canvas, x, y, header, rows, widths, size=1.7, row_h=2.5,
           header_fill="#e7eaee", stroke="#9aa3ac"):
    total = sum(widths)
    can.rect(x, y - row_h, total, row_h, fill=header_fill, stroke=stroke, sw=0.12)
    xx = x
    for i, cell in enumerate(header):
        can.text((xx + 0.6, y - row_h + 0.7), cell, size=size, bold=True, fill="#30363d")
        xx += widths[i]
    yy = y - row_h
    for row in rows:
        can.line((x, yy), (x + total, yy), stroke="#c9ced6", w=0.08)
        xx = x
        for i, cell in enumerate(row):
            can.text((xx + 0.6, yy - row_h + 0.7), cell, size=size, fill="#30363d")
            xx += widths[i]
        yy -= row_h
    can.rect(x, yy, total, y - yy, fill=None, stroke=stroke, sw=0.12)
    xx = x
    for w in widths[:-1]:
        xx += w
        can.line((xx, y), (xx, yy), stroke="#c9ced6", w=0.1)
    return yy


def notes_view(spec: Spec) -> Canvas:
    stack = spec.get("derived.stack") or {}
    d = float(spec.get("case.diameter_mm") or 40)
    dial_d = float(spec.get("dial.diameter_mm") or 36)
    W, H = 160.0, 116.0
    can = Canvas(W, H, "Notes and tolerances", "1:1")
    checksum = (spec.get("derived.checksum") or "")[:12]

    # page border
    can.rect(2, 2, W - 4, H - 4, fill=None, stroke="#9aa3ac", sw=0.25)

    can.text((6, H - 7), "GENERAL TOLERANCES", size=2.8, bold=True)
    _table(can, 6, H - 10,
           ["item", "value", "class"],
           [["linear dimensions", "\u00b10.03 mm", "general"],
            ["diameters / bores", "\u00b10.02 mm", "general"],
            ["hole fit", "H7", "ISO 286"],
            ["shaft fit", "h6", "ISO 286"],
            ["flatness / parallelism", "0.01 / 0.02 mm", "general"],
            ["surface roughness", "Ra 0.4 \u00b5m", "seats"],
            ["gear module", "\u00b10.005 mm", "tooth"],
            ["tooth thickness", "-0.010 / -0.020 mm", "backlash"],
            ["edge break", "0.05 mm max, deburr", "all"],
            ["measure at", "20 \u00b0C", "shop floor"]],
           [32, 36, 18], row_h=2.25)

    can.text((6, H - 41), "CRITICAL DIMENSIONS", size=2.8, bold=True)
    _table(can, 6, H - 44,
           ["feature", "nominal", "tol"],
           [["case bore", f"\u00d8 {d - 3.2:.2f}", "\u00b10.02"],
            ["crystal seat", f"\u00d8 {dial_d + 0.4:.2f}", "H7/h6"],
            ["caseback thread", f"\u00d8 {d - 1.5:.2f} \u00d7 0.25", "6g/6H"],
            ["caseback gasket groove", f"\u00d8 {d - 3.0:.2f} \u00d7 0.6", "+0.05/0"],
            ["lug hole", "\u00d8 1.20", "\u00b10.02"],
            ["hour hand tube", "\u00d8 1.50 / 0.25 wall", "h6"],
            ["minute hand tube", "\u00d8 0.90 / 0.20 wall", "h6"],
            ["seconds hand tube", "\u00d8 0.25", "h6"],
            ["crown tube thread", "M3.0 \u00d7 0.35", "6g/6H"]],
           [40, 30, 18], row_h=2.25)

    # right column: stack table
    x = 98.0
    can.text((x, H - 7), "STACK-UP (mm)", size=2.8, bold=True)
    rows = [
        ["caseback", f"{stack.get('caseback_mm', 0):.2f}", f"z {stack.get('z_caseback_top', 0):.2f}"],
        ["movement", f"{stack.get('movement_mm', 0):.2f}", f"z {stack.get('z_movement_top', 0):.2f}"],
        ["complications", f"{stack.get('complications_mm', 0):.2f}", f"z {stack.get('z_complications_top', 0):.2f}"],
        ["dial", f"{stack.get('dial_mm', 0):.2f}", f"z {stack.get('z_dial_top', 0):.2f}"],
        ["hands", f"{stack.get('hands_mm', 0):.2f}", f"z {stack.get('z_hands_top', 0):.2f}"],
        ["crystal", f"{stack.get('crystal_mm', 0):.2f}", f"z {stack.get('z_crystal_bottom', 0):.2f}"],
        ["clearances", f"{stack.get('clearances_mm', 0):.2f}", "min 0.10 each"],
        ["TOTAL / case", f"{stack.get('required_case_thickness_mm', 0):.2f} / "
                          f"{stack.get('available_case_thickness_mm', 0):.2f}",
         f"margin {stack.get('margin_mm', 0):+.2f}"],
    ]
    yy = _table(can, x, H - 10, ["layer", "thickness", "level"], rows, [30, 12, 14], row_h=2.35)

    can.text((x, yy - 4), "MATERIALS AND FINISH", size=2.8, bold=True)
    yy = _table(can, x, yy - 7,
                ["part", "material / finish"],
                [["case", f"{spec.get('case.material')}, {spec.get('case.finish')}"],
                 ["crystal", f"{spec.get('case.crystal')}, {spec.get('case.crystal_type')}"],
                 ["dial", f"{spec.get('dial.color')}, {spec.get('dial.finish')}"],
                 ["hands", str(spec.get("hands.material"))],
                 ["movement", f"{spec.get('movement.kind')}, {spec.get('movement.jewels')} jewels"],
                 ["gaskets", "FKM 70 Shore A"],
                 ["spring bars", "316L, 1.5 mm pin"]],
                [30, 29], row_h=2.2)

    # notes block, bottom-left
    can.text((6, 45.5), "PROCESS NOTES", size=2.8, bold=True)
    notes = [
        "1. Deburr and passivate all 316L parts after machining; no tool marks on visible faces.",
        "2. Heat-treat the mainspring to 42-46 HRC; stress-relieve after coiling.",
        "3. Plate hands and indices before lume; lume cure 24 h at 23 \u00b0C.",
        "4. Clean the movement in an ultrasonic bath; lubricate per the assembly sheet.",
        "5. Press the crystal with a flat die; leak-test every case before casing the movement.",
        "6. First article: inspect all critical dimensions and record actuals in the QC sheet.",
    ]
    for i, line in enumerate(notes):
        can.text((6, 41.5 - i * 3.0), line, size=1.8, fill="#30363d")

    # title block, bottom-right
    bx, by, bw, bh = W - 68, 8.0, 62.0, 40.0
    can.rect(bx, by, bw, bh, fill=None, stroke="#5a636d", sw=0.3)
    rows_tb = [
        ("PROJECT", str(spec.get("meta.name"))),
        ("DRAWING", "production drawing set"),
        ("SCALE", "1:1 (mm)"),
        ("DATE", _dt.date.today().isoformat()),
        ("REV", "1"),
        ("SPEC", checksum),
        ("DRAWN", "watchcad deterministic build"),
        ("CHECKED", "________________"),
    ]
    row_h = bh / len(rows_tb)
    for i, (key, value) in enumerate(rows_tb):
        y = by + bh - (i + 1) * row_h
        can.rect(bx, y, 18, row_h, fill="#eef1f4", stroke="#9aa3ac", sw=0.12)
        can.text((bx + 1.0, y + row_h / 2 - 0.8), key, size=1.7, bold=True, fill="#30363d")
        can.text((bx + 19.5, y + row_h / 2 - 0.8), value[:44], size=1.7, fill="#30363d")
        if i:
            can.line((bx, y + row_h), (bx + bw, y + row_h), stroke="#c9ced6", w=0.1)
    can.line((bx + 18, by), (bx + 18, by + bh), stroke="#9aa3ac", w=0.15)

    can.text((W / 2, H - 3.5), "NOTES, TOLERANCES AND STACK-UP", size=3.0, anchor="middle", bold=True)
    return can


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------

def drawing_set(spec: Spec) -> dict[str, Canvas]:
    return {
        "front": front_view(spec),
        "section": section_view(spec),
        "train": train_view(spec),
        "notes": notes_view(spec),
    }


def write_drawings(spec: Spec, out_dir: str) -> dict[str, str]:
    import os
    os.makedirs(os.path.join(out_dir, "drawings"), exist_ok=True)
    canvases = drawing_set(spec)
    written = {}
    for name, can in canvases.items():
        path = os.path.join(out_dir, "drawings", f"{name}.svg")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(to_svg(can))
        written[name + "_svg"] = path
    dxf_path = os.path.join(out_dir, "drawings", "watch_sheet.dxf")
    with open(dxf_path, "w", encoding="utf-8") as fh:
        fh.write(to_dxf(list(canvases.values())))
    written["sheet_dxf"] = dxf_path
    pdf_path = os.path.join(out_dir, "drawings", "watch_drawings.pdf")
    write_pdf(list(canvases.values()), pdf_path,
              title=f"{spec.get('meta.name')} - production drawing",
              meta={"date": _dt.date.today().isoformat(),
                    "checksum": spec.get("derived.checksum", "")[:8]})
    written["drawings_pdf"] = pdf_path
    return written
