"""Watch gearing: ratio solving and tooth profiles.

Two jobs:
  1. Find integer tooth counts that give an exact rational speed ratio while
     keeping centre distances inside the movement.  This is the part of watch
     design that is genuinely exact and is therefore reported as `calculated`.
  2. Generate a tooth outline: epicycloidal addendum, radial dedendum, with an
     interference clamp so the outline is always a simple closed polygon.

The profile is engineering-approximate (presentation and area/mass use).  The
tooth-count arithmetic is exact, which is what the kinematics check relies on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
import math

# Reasonable watch ranges.  Module (tooth size) 0.08-0.25 mm is the normal band.
PINIONS = range(6, 17)
WHEELS = range(24, 121)
HOUR_WHEELS = range(24, 81)
MOTION_WHEELS = range(24, 61)


@dataclass
class Stage:
    driver: str
    driven: str
    wheel_teeth: int
    pinion_teeth: int
    module_mm: float
    centre_mm: float
    ratio: float
    note: str = ""

    def to_dict(self) -> dict:
        return dict(driver=self.driver, driven=self.driven, wheel_teeth=self.wheel_teeth,
                    pinion_teeth=self.pinion_teeth, module_mm=round(self.module_mm, 4),
                    centre_mm=round(self.centre_mm, 4), ratio=round(self.ratio, 6),
                    note=self.note)


@dataclass
class TrainSolution:
    stages: list[Stage] = field(default_factory=list)
    target_ratio: float = 1.0
    actual_ratio: float = 1.0
    error_ppm: float = 0.0
    score: float = 0.0

    def to_dict(self) -> dict:
        return dict(stages=[s.to_dict() for s in self.stages],
                    target_ratio=round(self.target_ratio, 8),
                    actual_ratio=round(self.actual_ratio, 8),
                    error_ppm=round(self.error_ppm, 3), score=round(self.score, 4))


def _table(pinions, wheels) -> dict[Fraction, tuple[int, int]]:
    table: dict[Fraction, tuple[int, int]] = {}
    for p in pinions:
        for w in wheels:
            r = Fraction(w, p)
            # keep meshes sensible: a wheel never drives a wheel this small
            if 1.6 <= float(r) <= 20.0:
                table.setdefault(r, (w, p))
    return table


def _score(combo, module, max_centre):
    """Prefer compact, evenly spaced meshes with healthy tooth counts."""
    centres = [module * (w + p) / 2 for w, p in combo]
    spread = max(centres) - min(centres)
    penalty = spread * 0.35
    penalty += sum(max(0.0, c - max_centre) * 8 for c in centres)
    penalty += sum(3.0 for w, p in combo if p < 7)
    penalty += sum(max(0.0, 40 - w) * 0.05 for w, p in combo)
    return penalty


def solve_ratio(target, stages: int = 2, module: float = 0.12,
                max_centre: float = 12.0, limit: int = 40,
                pinions=PINIONS, wheels=WHEELS) -> list[TrainSolution]:
    """Exact tooth-count solutions for `target` over `stages` meshes.

    target may be a Fraction, int, float or string.  Results are exact rational
    products; error_ppm is reported for display only and is zero by construction.
    """
    target = Fraction(target).limit_denominator(100000)
    if target <= 0:
        return []
    table = _table(pinions, wheels)
    results: list[TrainSolution] = []

    def rec(rem: Fraction, left: int, acc: list[tuple[int, int]]):
        if left == 1:
            hit = table.get(rem)
            if hit:
                combo = acc + [hit]
                score = _score(combo, module, max_centre)
                results.append((score, combo))
            return
        for ratio, pair in table.items():
            if rem % ratio == 0:
                nxt = rem / ratio
                if nxt < Fraction(3, 2):
                    continue
                rec(nxt, left - 1, acc + [pair])

    rec(target, stages, [])
    results.sort(key=lambda item: item[0])
    out = []
    for score, combo in results[:limit]:
        if combo:
            total = 1.0
            st = []
            for w, p in combo:
                total *= w / p
                st.append(Stage("", "", w, p, module, module * (w + p) / 2, w / p))
            out.append(TrainSolution(st, float(target), total,
                                     abs(total - float(target)) / float(target) * 1e6, score))
    return out


def best_ratio(target, stages=2, module=0.12, max_centre=12.0, pinions=PINIONS,
               wheels=WHEELS) -> TrainSolution | None:
    sols = solve_ratio(target, stages, module, max_centre, limit=1,
                       pinions=pinions, wheels=wheels)
    return sols[0] if sols else None


def approximate(target: float, module: float, max_centre: float,
                pinions=PINIONS, wheels=WHEELS) -> tuple[int, int, float]:
    """One-mesh approximation used for barrel and motion works.

    Returns (wheel_teeth, pinion_teeth, error_percent).
    """
    best = None
    for p in pinions:
        for w in wheels:
            centre = module * (w + p) / 2
            if centre > max_centre:
                continue
            err = abs(w / p - target) / target * 100
            quality = err * 10 + max(0.0, 40 - w) * 0.02 + (3 if p < 7 else 0)
            if best is None or quality < best[0]:
                best = (quality, w, p, err)
    if best is None:
        return 60, 10, 100.0
    return best[1], best[2], best[3]


# ---------------------------------------------------------------------------
# Tooth profile
# ---------------------------------------------------------------------------

def _polar(r: float, phi: float) -> tuple[float, float]:
    return (r * math.cos(phi), r * math.sin(phi))


def _epicycloid_flank(r_pitch: float, r_tip: float, rolling: float,
                      phi_start: float, points: int = 7) -> list[tuple[float, float]]:
    """Addendum flank from the pitch point to the tip, as (r, phi) pairs."""
    out = []
    theta = 0.0
    step = 0.02
    for i in range(points + 1):
        # march theta until the radius reaches the tip
        while True:
            x = (r_pitch + rolling) * math.cos(theta) - rolling * math.cos(
                (r_pitch + rolling) / rolling * theta) - r_pitch
            y = (r_pitch + rolling) * math.sin(theta) - rolling * math.sin(
                (r_pitch + rolling) / rolling * theta)
            r = math.hypot(r_pitch + x, y)
            if r >= r_tip or theta > 2.0:
                break
            theta += step
        phi = math.atan2(y, r_pitch + x) + phi_start
        out.append((min(r, r_tip), phi))
        if r >= r_tip:
            # fill the remaining samples with the tip radius
            for _ in range(points - i):
                out.append((r_tip, phi))
            break
    return out


def _smoothstep(u: float) -> float:
    return u * u * (3 - 2 * u)


def gear_profile(teeth: int, module: float, points: int = 5,
                 pressure_style: str = "radial-cycloidal") -> dict:
    """Closed 2-D outline of a watch wheel or pinion, counter-clockwise.

    Addendum and dedendum are smooth radial flanks (a presentation-grade
    cycloidal approximation), with a narrow tip and a wide root.  The result is
    guaranteed to be a simple closed polygon: the polar angle increases
    monotonically with a fixed tooth ordering.
    """
    teeth = max(6, int(teeth))
    r_pitch = module * teeth / 2.0
    r_tip = r_pitch + 0.85 * module
    r_root = max(0.15, r_pitch - 1.45 * module)
    pitch_angle = 2 * math.pi / teeth
    half_pitch = pitch_angle / 2

    # Tooth thickness at the pitch circle is half the circular pitch for both
    # wheels and pinions.  A thicker pinion tooth (previously 0.32) closes the
    # mating gap and makes the two outlines interfere even at the correct phase.
    tooth_half = 0.24 * pitch_angle
    root_half = min(half_pitch * 0.96, tooth_half + (half_pitch - tooth_half) * 0.78)
    tip_half = tooth_half * 0.34

    def flank(r0, r1, a0, a1, n):
        out = []
        for i in range(n + 1):
            u = i / n
            s = _smoothstep(u)
            out.append((r0 + (r1 - r0) * s, a0 + (a1 - a0) * s))
        return out

    dedendum = flank(r_root, r_pitch, root_half, tooth_half, 3)
    addendum = flank(r_pitch, r_tip, tooth_half, tip_half, points)

    poly: list[tuple[float, float]] = []
    for i in range(teeth):
        base = i * pitch_angle
        # minus side, root -> pitch -> tip (angles rise toward zero)
        for r, phi in dedendum:
            poly.append(_polar(r, base - phi))
        for r, phi in addendum[1:]:
            poly.append(_polar(r, base - phi))
        poly.append(_polar(r_tip, base + tip_half))
        # plus side, tip -> pitch -> root
        for r, phi in list(reversed(addendum))[1:]:
            poly.append(_polar(r, base + phi))
        for r, phi in list(reversed(dedendum))[1:]:
            poly.append(_polar(r, base + phi))
        # root arc across the gap to the next tooth
        for j in range(1, 3):
            poly.append(_polar(r_root, base + root_half
                               + (pitch_angle - 2 * root_half) * j / 3.0))

    return dict(points=poly, teeth=teeth, module=module, r_pitch=r_pitch,
                r_tip=r_tip, r_root=r_root, profile=pressure_style,
                tooth_thickness_mm=2 * tooth_half * r_pitch,
                pitch_mm=2 * math.pi * r_pitch)


def mesh_rotation(wheel_teeth: int, pinion_teeth: int,
                  line_angle: float) -> tuple[float, float]:
    """Angles that put a wheel tooth and a pinion gap on the centre line.

    The profiles start with a tooth centred on local angle zero.  A pinion gap
    is half a pitch away from its tooth centre.  Rotating the pinion so that gap
    faces the wheel gives a valid, backlash-friendly mesh without trial and
    error.
    """
    pinion_pitch = 2.0 * math.pi / max(6, int(pinion_teeth))
    return line_angle, line_angle + math.pi - pinion_pitch / 2.0


def profile_polygon(teeth: int, module: float, rotation: float = 0.0,
                    center: tuple[float, float] = (0.0, 0.0),
                    points: int | None = None) -> tuple[list[tuple[float, float]], dict]:
    """A profile placed in world coordinates (rotation in radians)."""
    prof = gear_profile(teeth, module,
                        points=points if points is not None
                        else (3 if teeth > 40 else 4))
    ca, sa = math.cos(rotation), math.sin(rotation)
    cx, cy = center
    placed = [(x * ca - y * sa + cx, x * sa + y * ca + cy)
              for x, y in prof["points"]]
    return placed, prof


def _segments_intersect(a, b, c, d) -> bool:
    def cross(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])

    d1 = cross(c, d, a)
    d2 = cross(c, d, b)
    d3 = cross(a, b, c)
    d4 = cross(a, b, d)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _point_in_polygon(point, poly) -> bool:
    x, y = point
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and \
                x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _segments(poly, near=None, window: float = 0.0):
    out = []
    for i in range(len(poly)):
        a = poly[i]
        b = poly[(i + 1) % len(poly)]
        if near is not None:
            x0, x1 = min(a[0], b[0]), max(a[0], b[0])
            y0, y1 = min(a[1], b[1]), max(a[1], b[1])
            if x1 < near[0] - window or x0 > near[0] + window:
                continue
            if y1 < near[1] - window or y0 > near[1] + window:
                continue
        out.append((a, b))
    return out


def polygons_intersect(a, b, near=None, window: float = 0.0) -> bool:
    """True when two closed polygons overlap.

    Passing ``near`` and ``window`` restricts the test to segments close to a
    contact point, which keeps the meshing check cheap.  Without a window this
    is an exact segment/containment test for the sampled outlines.
    """
    seg_a = _segments(a, near, window)
    seg_b = _segments(b, near, window)
    for p0, p1 in seg_a:
        for q0, q1 in seg_b:
            if _segments_intersect(p0, p1, q0, q1):
                return True
    if near is not None:
        return False
    return _point_in_polygon(a[0], b) or _point_in_polygon(b[0], a)


def meshing_outlines(wheel_teeth: int, pinion_teeth: int, module: float,
                     wheel_pos: tuple[float, float],
                     pinion_pos: tuple[float, float],
                     points: int | None = None):
    """Wheel and pinion outlines placed and phased for one mesh."""
    theta = math.atan2(pinion_pos[1] - wheel_pos[1],
                       pinion_pos[0] - wheel_pos[0])
    wheel_rot, pinion_rot = mesh_rotation(wheel_teeth, pinion_teeth, theta)
    wheel, wp = profile_polygon(wheel_teeth, module, wheel_rot, wheel_pos, points)
    pinion, pp = profile_polygon(pinion_teeth, module, pinion_rot, pinion_pos, points)
    contact = (wheel_pos[0] + wp["r_pitch"] * math.cos(theta),
               wheel_pos[1] + wp["r_pitch"] * math.sin(theta))
    return wheel, pinion, contact


def escape_wheel_ratio(beat_rate_bph: float, escape_teeth: int = 15) -> float:
    """Revolutions of the escape wheel per revolution of the fourth wheel."""
    escape_revs_per_hour = beat_rate_bph / (2.0 * escape_teeth)
    return escape_revs_per_hour / 60.0  # fourth wheel turns once per minute


def train_power_reserve(turns: float, barrel_to_centre: float) -> float:
    """Hours of running from usable barrel turns and the first reduction."""
    return turns * barrel_to_centre * 1.0  # centre wheel = 1 rev/hour


def seconds_ratio(beat_rate_bph: float, escape_teeth: int = 15) -> Fraction:
    return Fraction(escape_wheel_ratio(beat_rate_bph, escape_teeth)).limit_denominator(1000)
