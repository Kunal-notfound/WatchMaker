"""Tiny dependency-free mesh kernel.

The production backend is OpenCascade (CadQuery/build123d) and exports STEP.
This kernel exists so the whole pipeline runs on a laptop with no CAD stack:
it generates watertight-enough meshes for STL/3MF/glTF, for the viewer, and for
volume/mass estimates.  All units are millimetres, Z is the watch axis.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import struct
from dataclasses import dataclass, field

from . import materials as material_lib
from .revision import RenderGenerationError

Vec = tuple[float, float, float]

CREASE_ANGLE_DEG = 35.0


class MeshValidationError(RenderGenerationError):
    """Raised when a generated mesh is structurally invalid."""


@dataclass
class Mesh:
    name: str
    verts: list[Vec] = field(default_factory=list)
    faces: list[tuple[int, int, int]] = field(default_factory=list)
    color: str = "#b9c2cc"
    material: str = "steel"
    role: str = "part"
    meta: dict = field(default_factory=dict)
    normals: list[Vec] | None = None

    # -- transforms -------------------------------------------------------
    def translated(self, dx=0.0, dy=0.0, dz=0.0) -> "Mesh":
        self.verts = [(x + dx, y + dy, z + dz) for x, y, z in self.verts]
        return self

    def rotated_z(self, degrees: float) -> "Mesh":
        a = math.radians(degrees)
        c, s = math.cos(a), math.sin(a)
        self.verts = [(x * c - y * s, x * s + y * c, z) for x, y, z in self.verts]
        return self

    def scaled(self, k: float) -> "Mesh":
        self.verts = [(x * k, y * k, z * k) for x, y, z in self.verts]
        return self

    def copy(self) -> "Mesh":
        return Mesh(self.name, list(self.verts), list(self.faces), self.color,
                    self.material, self.role, dict(self.meta),
                    None if self.normals is None else list(self.normals))

    # -- queries ----------------------------------------------------------
    def bounds(self) -> dict:
        if not self.verts:
            return dict(min=[0, 0, 0], max=[0, 0, 0], size=[0, 0, 0])
        xs, ys, zs = zip(*self.verts)
        mn = [min(xs), min(ys), min(zs)]
        mx = [max(xs), max(ys), max(zs)]
        return dict(min=[round(v, 3) for v in mn], max=[round(v, 3) for v in mx],
                    size=[round(mx[i] - mn[i], 3) for i in range(3)])

    def volume(self) -> float:
        total = 0.0
        for a, b, c in self.faces:
            ax, ay, az = self.verts[a]
            bx, by, bz = self.verts[b]
            cx, cy, cz = self.verts[c]
            total += (ax * (by * cz - bz * cy)
                      - ay * (bx * cz - bz * cx)
                      + az * (bx * cy - by * cx)) / 6.0
        return abs(total)

    def triangle_count(self) -> int:
        return len(self.faces)

    def to_viewer(self) -> dict:
        flat = [round(c, 3) for v in self.verts for c in v]
        faces = [i for f in self.faces for i in f]
        return dict(name=self.name, color=self.color, material=self.material,
                    role=self.role, verts=flat, faces=faces, bounds=self.bounds(),
                    meta=self.meta)


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

def _ring(profile: list[tuple[float, float]], segments: int, closed: bool) -> Mesh:
    """Surface of revolution.  profile entries are (radius, z)."""
    verts: list[Vec] = []
    n = len(profile)
    for seg in range(segments):
        a = 2 * math.pi * seg / segments
        c, s = math.cos(a), math.sin(a)
        for r, z in profile:
            verts.append((r * c, r * s, z))
    faces: list[tuple[int, int, int]] = []
    pairs = n if closed else n - 1
    for seg in range(segments):
        nxt = (seg + 1) % segments
        for i in range(pairs):
            j = (i + 1) % n
            a = seg * n + i
            b = seg * n + j
            c = nxt * n + j
            d = nxt * n + i
            # A profile point on the axis is the same position for every
            # segment.  Emit only the non-degenerate triangle(s) so the mesh
            # has no zero-area faces at disc caps and axis tips.
            at_axis_a = abs(profile[i][0]) <= 1e-12
            at_axis_b = abs(profile[j][0]) <= 1e-12
            if at_axis_a and at_axis_b:
                continue
            if at_axis_a:
                faces.append((a, b, c))
            elif at_axis_b:
                faces.append((a, c, d))
            else:
                faces.append((a, b, c))
                faces.append((a, c, d))
    return ensure_outward(Mesh("revolved", verts, faces))


def revolve(profile: list[tuple[float, float]], segments: int = 64,
            closed: bool = True) -> Mesh:
    """Revolve a closed (r,z) loop into a solid ring, or an open curve into a shell."""
    return _ring(profile, segments, closed)


def disc(radius: float, z0: float, z1: float, segments: int = 64) -> Mesh:
    profile = [(0.0, z0), (radius, z0), (radius, z1), (0.0, z1)]
    return _ring(profile, segments, closed=True)


def tube(inner: float, outer: float, z0: float, z1: float, segments: int = 64) -> Mesh:
    profile = [(inner, z0), (outer, z0), (outer, z1), (inner, z1)]
    return _ring(profile, segments, closed=True)


def frustum(r0: float, r1: float, z0: float, z1: float, segments: int = 64) -> Mesh:
    return _ring([(0.0, z0), (r0, z0), (r1, z1), (0.0, z1)], segments, closed=True)


def dome(radius: float, z0: float, height: float, segments: int = 64,
         steps: int = 8) -> Mesh:
    """A lens: flat underside on z0, spherical cap above."""
    profile = [(0.0, z0), (radius, z0)]
    for i in range(1, steps + 1):
        t = (math.pi / 2) * i / steps
        profile.append((radius * math.cos(t), z0 + height * math.sin(t)))
    profile.append((0.0, z0 + height))
    return _ring(profile, segments, closed=False)


def lens(radius: float, z0: float, height: float, edge: float = 0.12,
         segments: int = 64, steps: int = 8) -> Mesh:
    """A solid crystal with a short cylindrical edge and a domed top.

    ``height`` is the total height above ``z0``.  ``edge`` is the vertical
    cylindrical land before the curvature starts.  This is still a
    presentation-grade surface, not an optical or manufacturable lens.
    """
    height = max(0.05, float(height))
    edge = max(0.0, min(float(edge), height * 0.9))
    cap = max(0.01, height - edge)
    profile: list[tuple[float, float]] = [(0.0, z0), (radius - 0.08, z0),
                                          (radius, z0 + min(0.08, edge)),
                                          (radius, z0 + edge)]
    for i in range(1, steps + 1):
        t = (math.pi / 2) * i / steps
        profile.append((radius * math.cos(t), z0 + edge + cap * math.sin(t)))
    profile.append((0.0, z0 + height))
    return _ring(profile, segments, closed=False)


def chamfered_disc(radius: float, z0: float, z1: float, chamfer: float = 0.15,
                   segments: int = 64) -> Mesh:
    """A disc with a 45-degree chamfer on both rims (a visual fillet stand-in)."""
    c = max(0.0, min(float(chamfer), min(radius * 0.4, (z1 - z0) * 0.45)))
    profile = [(0.0, z0), (radius - c, z0), (radius, z0 + c),
               (radius, z1 - c), (radius - c, z1), (0.0, z1)]
    return _ring(profile, segments, closed=True)


def arc_ring(inner: float, outer: float, z0: float, z1: float,
             start_deg: float = 0.0, end_deg: float = 360.0,
             segments: int = 48) -> Mesh:
    """A flat annular sector, used for rotors, arcs and aperture frames.

    Unlike :func:`tube` this can be partial.  The caps are built as explicit
    quad strips, so the result is a closed solid without relying on a fan
    triangulation of a non-convex polygon.
    """
    inner = max(0.0, float(inner))
    outer = max(inner + 1e-6, float(outer))
    if end_deg <= start_deg:
        end_deg = start_deg + 360.0
    steps = max(2, int(segments))
    full = abs((end_deg - start_deg) - 360.0) < 1e-6
    count = steps if full else steps + 1
    verts: list[Vec] = []
    for i in range(count):
        a = math.radians(start_deg + (end_deg - start_deg) * i / steps)
        c, s = math.cos(a), math.sin(a)
        verts += [(inner * c, inner * s, z0), (outer * c, outer * s, z0),
                  (outer * c, outer * s, z1), (inner * c, inner * s, z1)]
    faces: list[tuple[int, int, int]] = []
    for i in range(steps if full else steps):
        j = (i + 1) % count if full else i + 1
        a = i * 4
        b = j * 4
        # bottom (0,1), outer (1,2), top (2,3), inner (3,0)
        faces += [(a, a + 1, b + 1), (a, b + 1, b)]
        faces += [(a + 1, a + 2, b + 2), (a + 1, b + 2, b + 1)]
        faces += [(a + 2, a + 3, b + 3), (a + 2, b + 3, b + 2)]
        faces += [(a + 3, a, b), (a + 3, b, b + 3)]
    if not full:
        # end caps at the two radial faces
        first, last = 0, steps * 4
        faces += [(first, first + 2, first + 1), (first, first + 3, first + 2)]
        faces += [(last, last + 1, last + 2), (last, last + 2, last + 3)]
    return ensure_outward(Mesh("arc", verts, faces))


def knurled_disc(radius: float, z0: float, z1: float, teeth: int = 36,
                 depth: float = 0.35, segments: int = 40) -> Mesh:
    """A toothed/knurled cylinder for a crown, bezel edge or grip ring."""
    teeth = max(6, int(teeth))
    depth = max(0.02, min(float(depth), radius * 0.45))
    count = max(teeth * 2, int(segments))
    poly: list[tuple[float, float]] = []
    for i in range(count):
        a = 2 * math.pi * i / count
        r = radius - (depth if i % 2 else 0.0)
        poly.append((r * math.cos(a), r * math.sin(a)))
    return extrude(poly, z0, z1, "knurled")


def torus(radius: float, wire: float, z: float = 0.0, segments: int = 48,
          wire_segments: int = 10) -> Mesh:
    verts, faces = [], []
    for i in range(segments):
        a = 2 * math.pi * i / segments
        for j in range(wire_segments):
            b = 2 * math.pi * j / wire_segments
            r = radius + wire * math.cos(b)
            verts.append((r * math.cos(a), r * math.sin(a), z + wire * math.sin(b)))
    for i in range(segments):
        for j in range(wire_segments):
            a = i * wire_segments + j
            b = i * wire_segments + (j + 1) % wire_segments
            c = ((i + 1) % segments) * wire_segments + (j + 1) % wire_segments
            d = ((i + 1) % segments) * wire_segments + j
            faces += [(a, b, c), (a, c, d)]
    return ensure_outward(Mesh("torus", verts, faces))


def _centroid(poly: list[tuple[float, float]]) -> tuple[float, float]:
    # area-weighted centroid; falls back to the mean for degenerate polygons
    area = 0.0
    cx = cy = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(area) < 1e-12:
        xs, ys = zip(*poly)
        return sum(xs) / len(xs), sum(ys) / len(ys)
    return cx / (3 * area), cy / (3 * area)


def extrude(poly: list[tuple[float, float]], z0: float, z1: float,
            name: str = "extrusion") -> Mesh:
    """Extrude a star-shaped polygon (fan caps from the centroid)."""
    n = len(poly)
    cx, cy = _centroid(poly)
    verts = [(cx, cy, z0)]
    verts += [(x, y, z0) for x, y in poly]
    verts += [(cx, cy, z1)]
    verts += [(x, y, z1) for x, y in poly]
    bottom, top = 0, n + 1
    faces = []
    for i in range(n):
        a = 1 + i
        b = 1 + (i + 1) % n
        faces.append((bottom, b, a))
        a2 = n + 2 + i
        b2 = n + 2 + (i + 1) % n
        faces.append((top, a2, b2))
        faces.append((a, b, b2))
        faces.append((a, b2, a2))
    return ensure_outward(Mesh(name, verts, faces))


def box(x0, y0, z0, x1, y1, z1) -> Mesh:
    poly = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return extrude(poly, z0, z1, "box")


def rounded_slab(width: float, length: float, thickness: float, radius: float,
                 segments: int = 8, name: str = "slab") -> Mesh:
    """A stadium (capsule) shaped slab, used for lugs and hands."""
    r = min(radius, width / 2)
    half = max(0.01, length / 2 - r)
    poly: list[tuple[float, float]] = []
    for i in range(segments + 1):
        a = -math.pi / 2 + math.pi * i / segments
        poly.append((half + r * math.cos(a), width / 2 * 0 + r * math.sin(a)))
    for i in range(segments + 1):
        a = math.pi / 2 + math.pi * i / segments
        poly.append((-half + r * math.cos(a), r * math.sin(a)))
    # widen the capsule to `width` by scaling y
    if r > 0:
        poly = [(x, y * (width / 2) / r) for x, y in poly]
    m = extrude(poly, 0.0, thickness, name)
    return ensure_outward(m)


def sweep_ribbon(path: list[tuple[float, float]], width: float, z0: float, z1: float,
                 name: str = "ribbon") -> Mesh:
    """Sweep a flat rectangular strip along a 2-D path (springs, arcs, leads)."""
    pts = [p for i, p in enumerate(path) if i == 0 or p != path[i - 1]]
    n = len(pts)
    if n < 2:
        return Mesh(name)
    verts: list[Vec] = []
    for i, (x, y) in enumerate(pts):
        prev = pts[max(0, i - 1)]
        nxt = pts[min(n - 1, i + 1)]
        dx, dy = nxt[0] - prev[0], nxt[1] - prev[1]
        ln = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / ln * width / 2, dx / ln * width / 2
        verts += [(x + nx, y + ny, z0), (x - nx, y - ny, z0),
                  (x + nx, y + ny, z1), (x - nx, y - ny, z1)]
    faces: list[tuple[int, int, int]] = []
    for i in range(n - 1):
        a = i * 4
        b = (i + 1) * 4
        faces += [(a, b, b + 1), (a, b + 1, a + 1)]          # bottom
        faces += [(a + 2, a + 3, b + 3), (a + 2, b + 3, b + 2)]  # top
        faces += [(a, a + 2, b + 2), (a, b + 2, b)]          # side +
        faces += [(a + 1, b + 1, b + 3), (a + 1, b + 3, a + 3)]  # side -
    faces += [(0, 1, 3), (0, 3, 2)]
    last = (n - 1) * 4
    faces += [(last, last + 2, last + 3), (last, last + 3, last + 1)]
    return ensure_outward(Mesh(name, verts, faces))


def rounded_rect_profile(width: float, height: float, radius: float,
                         corner_segments: int = 3) -> list[tuple[float, float]]:
    """Counter-clockwise rounded rectangle centred on the origin.

    The profile is used as a closed cross-section by :func:`sweep_sections`.
    It is convex, so a centroid fan always triangulates the end caps without
    self-intersection or zero-area triangles.
    """
    width = max(0.05, float(width))
    height = max(0.05, float(height))
    radius = max(0.0, min(float(radius), width * 0.49, height * 0.49))
    steps = max(1, int(corner_segments))
    hx, hy = width / 2.0, height / 2.0
    corners = [
        (hx - radius, hy - radius, 0.0),
        (-hx + radius, hy - radius, 90.0),
        (-hx + radius, -hy + radius, 180.0),
        (hx - radius, -hy + radius, 270.0),
    ]
    poly: list[tuple[float, float]] = []
    for cx, cy, start in corners:
        for index in range(steps + 1):
            angle = math.radians(start + 90.0 * index / steps)
            poly.append((cx + radius * math.cos(angle),
                         cy + radius * math.sin(angle)))
    return poly


def section_frame(path: list[Vec], index: int,
                  axis_hint: Vec = (1.0, 0.0, 0.0)) -> tuple[Vec, Vec, Vec]:
    """Return ``(width_axis, tangent, normal_axis)`` for a swept path point."""
    prev = path[max(0, index - 1)]
    nxt = path[min(len(path) - 1, index + 1)]
    tangent = (nxt[0] - prev[0], nxt[1] - prev[1], nxt[2] - prev[2])
    length = math.sqrt(sum(component * component for component in tangent))
    if length <= 1e-9:
        raise ValueError("sweep path has a zero-length tangent")
    t = tuple(component / length for component in tangent)
    dot = sum(axis_hint[i] * t[i] for i in range(3))
    primary = tuple(axis_hint[i] - dot * t[i] for i in range(3))
    plen = math.sqrt(sum(component * component for component in primary))
    if plen <= 1e-9:
        fallback = (0.0, 1.0, 0.0) if abs(t[1]) < 0.9 else (1.0, 0.0, 0.0)
        dot = sum(fallback[i] * t[i] for i in range(3))
        primary = tuple(fallback[i] - dot * t[i] for i in range(3))
        plen = math.sqrt(sum(component * component for component in primary))
    primary = tuple(component / plen for component in primary)
    secondary = (
        primary[1] * t[2] - primary[2] * t[1],
        primary[2] * t[0] - primary[0] * t[2],
        primary[0] * t[1] - primary[1] * t[0],
    )
    return primary, t, secondary  # type: ignore[return-value]


def sweep_sections(path: list[Vec], sections: list[tuple[float, float, float]],
                   name: str = "sweep", *, axis_hint: Vec = (1.0, 0.0, 0.0),
                   corner_segments: int = 3) -> Mesh:
    """Sweep a changing rounded-rectangle section along a 3-D centre-line.

    Unlike :func:`sweep_ribbon` this produces a closed solid with rounded sides
    and real end caps.  ``axis_hint`` chooses the width direction and is
    projected onto the plane normal to the path tangent, so a strap can follow
    a wrist arc while its width stays across the wrist.  ``sections`` entries
    are ``(width, height, corner_radius)`` in the local width/normal plane.

    This is a presentation-grade swept solid: it has no boolean union with the
    case and no manufacturing fillets, but it is watertight enough for export
    and its centre-line and cross-sections are driven by the solved spec.
    """
    if len(path) < 2 or len(sections) != len(path):
        return Mesh(name)
    steps = max(1, int(corner_segments))
    profile = rounded_rect_profile(sections[0][0], sections[0][1],
                                   sections[0][2], steps)
    ring = len(profile)
    verts: list[Vec] = []
    for index, centre in enumerate(path):
        primary, _, secondary = section_frame(path, index, axis_hint)
        profile = rounded_rect_profile(sections[index][0], sections[index][1],
                                       sections[index][2], steps)
        for u, v in profile:
            verts.append(tuple(centre[axis] + primary[axis] * u
                               + secondary[axis] * v for axis in range(3)))

    faces: list[tuple[int, int, int]] = []
    for index in range(len(path) - 1):
        for j in range(ring):
            a = index * ring + j
            b = index * ring + (j + 1) % ring
            c = (index + 1) * ring + (j + 1) % ring
            d = (index + 1) * ring + j
            faces += [(a, b, c), (a, c, d)]

    first_centre = len(verts)
    verts.append(tuple(sum(verts[j][axis] for j in range(ring)) / ring
                       for axis in range(3)))
    last_ring = (len(path) - 1) * ring
    last_centre = len(verts)
    verts.append(tuple(sum(verts[last_ring + j][axis] for j in range(ring))
                       / ring for axis in range(3)))
    for j in range(ring):
        nxt = (j + 1) % ring
        faces.append((first_centre, nxt, j))
        faces.append((last_centre, last_ring + j, last_ring + nxt))
    return ensure_outward(Mesh(name, verts, faces))


def polygon_area(poly: list[tuple[float, float]]) -> float:
    area = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        area += x0 * y1 - x1 * y0
    return abs(area) / 2


def merge(meshes: list[Mesh], name: str = "assembly", color: str | None = None) -> Mesh:
    verts: list[Vec] = []
    faces: list[tuple[int, int, int]] = []
    for m in meshes:
        offset = len(verts)
        verts += m.verts
        faces += [(a + offset, b + offset, c + offset) for a, b, c in m.faces]
    return Mesh(name, verts, faces, color or (meshes[0].color if meshes else "#cccccc"))


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def to_stl(meshes: list[Mesh], binary: bool = True) -> bytes:
    all_faces = []
    for m in meshes:
        for f in m.faces:
            all_faces.append(tuple(m.verts[i] for i in f))
    if binary:
        buf = bytearray(b"watchcad binary stl".ljust(80, b" "))
        buf += struct.pack("<I", len(all_faces))
        for a, b, c in all_faces:
            n = _normal(a, b, c)
            buf += struct.pack("<12fH", *n, *a, *b, *c, 0)
        return bytes(buf)
    lines = ["solid watchcad"]
    for a, b, c in all_faces:
        n = _normal(a, b, c)
        lines.append(" facet normal %g %g %g" % n)
        lines.append("  outer loop")
        for v in (a, b, c):
            lines.append("   vertex %g %g %g" % v)
        lines.append("  endloop")
        lines.append(" endfacet")
    lines.append("endsolid watchcad")
    return "\n".join(lines).encode()


def ensure_outward(m: Mesh) -> Mesh:
    if _signed_volume(m) < 0:
        m.faces = [(a, c, b) for a, b, c in m.faces]
    return m


def _signed_volume(m: Mesh) -> float:
    total = 0.0
    for a, b, c in m.faces:
        ax, ay, az = m.verts[a]
        bx, by, bz = m.verts[b]
        cx, cy, cz = m.verts[c]
        total += (ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx)
                  + az * (bx * cy - by * cx)) / 6.0
    return total


def _normal(a, b, c):
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    return nx / ln, ny / ln, nz / ln


def vertex_normals(m: Mesh) -> list[Vec]:
    """Area-weighted smooth normals, indexed like ``m.verts``.

    glTF consumers need a normal attribute for physically based materials.
    The kernel keeps geometry indexed, so averaging adjacent face normals is
    deterministic and preserves the original triangle indices.
    """
    acc = [[0.0, 0.0, 0.0] for _ in m.verts]
    for a, b, c in m.faces:
        ux, uy, uz = (m.verts[b][0] - m.verts[a][0],
                      m.verts[b][1] - m.verts[a][1],
                      m.verts[b][2] - m.verts[a][2])
        vx, vy, vz = (m.verts[c][0] - m.verts[a][0],
                      m.verts[c][1] - m.verts[a][1],
                      m.verts[c][2] - m.verts[a][2])
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        for index in (a, b, c):
            acc[index][0] += nx
            acc[index][1] += ny
            acc[index][2] += nz
    out: list[Vec] = []
    for nx, ny, nz in acc:
        length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        out.append((nx / length, ny / length, nz / length))
    return out


def _face_normal_raw(m: Mesh, face: tuple[int, int, int]) -> tuple[float, float, float]:
    a, b, c = (m.verts[i] for i in face)
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    return (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)


def _unit(vector: tuple[float, float, float]) -> Vec:
    length = math.sqrt(sum(component * component for component in vector))
    if not math.isfinite(length) or length <= 1e-12:
        return (0.0, 0.0, 1.0)
    return tuple(component / length for component in vector)  # type: ignore[return-value]


def split_creases(m: Mesh, angle_deg: float = CREASE_ANGLE_DEG) -> Mesh:
    """Return a copy with duplicated vertices where faces meet at a crease.

    Smooth groups are grown from the average normal of each cluster, so a
    curved surface stays smooth while a machined 90-degree rim is split.  The
    returned mesh carries explicit per-vertex ``normals`` for glTF export.
    """
    if not m.faces or not m.verts:
        return Mesh(m.name, list(m.verts), list(m.faces), m.color, m.material,
                    m.role, dict(m.meta), vertex_normals(m))
    limit = math.cos(math.radians(max(1.0, min(89.0, angle_deg))))
    raw = [_face_normal_raw(m, face) for face in m.faces]
    incident: dict[int, list[int]] = {}
    for face_index, face in enumerate(m.faces):
        for vertex in face:
            incident.setdefault(vertex, []).append(face_index)

    # cluster normal for each original vertex / smooth group
    cluster_normals: dict[tuple[int, int], Vec] = {}
    face_cluster: dict[tuple[int, int], int] = {}
    for vertex, faces in incident.items():
        remaining = list(faces)
        group = 0
        while remaining:
            seed = remaining.pop(0)
            members = [seed]
            total = list(raw[seed])
            while True:
                mean = _unit(tuple(component for component in total))
                moved = False
                for face_index in list(remaining):
                    normal = _unit(raw[face_index])
                    if sum(normal[i] * mean[i] for i in range(3)) >= limit:
                        remaining.remove(face_index)
                        members.append(face_index)
                        for i in range(3):
                            total[i] += raw[face_index][i]
                        moved = True
                if not moved:
                    break
            for face_index in members:
                face_cluster[(vertex, face_index)] = group
            cluster_normals[(vertex, group)] = _unit(tuple(total))
            group += 1

    new_verts = list(m.verts)
    normals: list[Vec] = [(0.0, 0.0, 1.0) for _ in m.verts]
    copies: dict[tuple[int, int], int] = {}
    new_faces: list[tuple[int, int, int]] = []
    for face_index, face in enumerate(m.faces):
        remapped = []
        for vertex in face:
            group = face_cluster.get((vertex, face_index), 0)
            key = (vertex, group)
            if group == 0:
                target = vertex
            elif key in copies:
                target = copies[key]
            else:
                target = len(new_verts)
                copies[key] = target
                new_verts.append(m.verts[vertex])
                normals.append((0.0, 0.0, 1.0))
            normals[target] = cluster_normals.get(key, _unit(raw[face_index]))
            remapped.append(target)
        new_faces.append(tuple(remapped))  # type: ignore[arg-type]
    return Mesh(m.name, new_verts, new_faces, m.color, m.material, m.role,
                dict(m.meta), normals)


def validate_mesh(m: Mesh) -> list[dict]:
    """Report structural defects without mutating the mesh."""
    issues: list[dict] = []

    def add(kind: str, detail: str, **fields) -> None:
        row = {"mesh": m.name, "kind": kind, "detail": detail}
        row.update(fields)
        issues.append(row)

    for index, vertex in enumerate(m.verts):
        if len(vertex) != 3 or not all(math.isfinite(float(c)) for c in vertex):
            add("invalid_vertex", "vertex has non-finite coordinates", index=index)
    seen_faces: set[tuple[int, ...]] = set()
    for index, face in enumerate(m.faces):
        if len(face) != 3:
            add("invalid_face", "face is not a triangle", index=index)
            continue
        for vertex in face:
            if not isinstance(vertex, int) or vertex < 0 or vertex >= len(m.verts):
                add("invalid_index", "face references a missing vertex",
                    face=index, index=vertex)
        key = tuple(sorted(face))
        if key in seen_faces:
            add("duplicate_face", "duplicate triangle", face=index)
        seen_faces.add(key)
        if all(0 <= i < len(m.verts) for i in face):
            normal = _face_normal_raw(m, face)
            if math.sqrt(sum(c * c for c in normal)) <= 1e-9:
                add("zero_area", "degenerate triangle has zero area", face=index)
    if m.normals is not None:
        if len(m.normals) != len(m.verts):
            add("normal_count", "normal count does not match vertex count")
        for index, normal in enumerate(m.normals):
            if len(normal) != 3 or not all(math.isfinite(float(c)) for c in normal):
                add("invalid_normal", "normal has non-finite components", index=index)
                continue
            length = math.sqrt(sum(c * c for c in normal))
            if abs(length - 1.0) > 1e-3:
                add("invalid_normal", "normal is not unit length", index=index,
                    length=round(length, 4))
    uvs = _uv_coords(m)
    for index, pair in enumerate(uvs):
        if len(pair) != 2 or not all(math.isfinite(float(c)) for c in pair):
            add("invalid_uv", "UV has non-finite coordinates", index=index)
        elif any(c < -1e-6 or c > 1.0 + 1e-6 for c in pair):
            add("invalid_uv", "UV is outside the unit range", index=index)
    if m.faces and m.verts and _signed_volume(m) <= 0:
        add("inverted_winding", "mesh signed volume is not outward-facing")
    return issues


def validate_meshes(meshes: list[Mesh]) -> list[dict]:
    issues: list[dict] = []
    for mesh in meshes:
        issues.extend(validate_mesh(mesh))
    return issues


def to_obj(meshes: list[Mesh]) -> str:
    lines = ["# watchcad assembly"]
    offset = 1
    for m in meshes:
        lines.append(f"o {m.name}")
        for x, y, z in m.verts:
            lines.append(f"v {x:.4f} {y:.4f} {z:.4f}")
        for f in m.faces:
            lines.append(f"f {f[0] + offset} {f[1] + offset} {f[2] + offset}")
        offset += len(m.verts)
    return "\n".join(lines) + "\n"


def _uv_coords(m: Mesh) -> list[tuple[float, float]]:
    """Deterministic UVs for procedural finish textures.

    Planar projection for dial/hand/crystal surfaces (the viewer's radial and
    grain patterns assume the centre is at 0.5, 0.5); cylindrical projection for
    case, movement and strap parts.  These are presentation UVs, not a
    manufacturable print layout.
    """
    if not m.verts:
        return [(0.0, 0.0)]
    xs = [v[0] for v in m.verts]
    ys = [v[1] for v in m.verts]
    zs = [v[2] for v in m.verts]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    zmin, zmax = min(zs), max(zs)
    if m.role in ("dial", "hand", "crystal", "complication", "gasket"):
        w = max(1e-6, xmax - xmin)
        h = max(1e-6, ymax - ymin)
        return [((x - xmin) / w, (y - ymin) / h) for x, y, _ in m.verts]
    span = max(1e-6, zmax - zmin)
    out = []
    for x, y, z in m.verts:
        u = (math.atan2(y, x) / (2.0 * math.pi)) % 1.0
        out.append((u, (z - zmin) / span))
    return out


def to_gltf(meshes: list[Mesh], name: str = "watch") -> dict:
    """Single-file glTF 2.0 with one node, mesh and material per part."""
    chunks: list[bytes] = []
    views: list[dict] = []
    accessors: list[dict] = []
    materials, nodes, gl_meshes = [], [], []
    byte_offset = 0

    def add_view(data: bytes, target: int) -> int:
        nonlocal byte_offset
        pad = (-len(data)) % 4
        chunks.append(data + b"\0" * pad)
        views.append({"buffer": 0, "byteOffset": byte_offset,
                      "byteLength": len(data), "target": target})
        byte_offset += len(data) + pad
        return len(views) - 1

    for m in meshes:
        issues = validate_mesh(m)
        if issues:
            raise MeshValidationError(
                f"{m.name}: " + "; ".join(
                    f"{row['kind']} ({row['detail']})" for row in issues[:4]))
        export = split_creases(m)
        pos = [c for v in export.verts for c in v]
        pos_bytes = struct.pack(f"<{len(pos)}f", *pos)
        normals = export.normals or vertex_normals(export)
        nrm = [c for v in normals for c in v]
        nrm_bytes = struct.pack(f"<{len(nrm)}f", *nrm)
        idx = [i for f in export.faces for i in f]
        idx_bytes = struct.pack(f"<{len(idx)}I", *idx)
        uvs = _uv_coords(export)
        uv = [c for pair in uvs for c in pair]
        uv_bytes = struct.pack(f"<{len(uv)}f", *uv)
        pv = add_view(pos_bytes, 34962)
        nv = add_view(nrm_bytes, 34962)
        iv = add_view(idx_bytes, 34963)
        uvv = add_view(uv_bytes, 34962)

        xs, ys, zs = export.verts and ([v[0] for v in export.verts], [v[1] for v in export.verts], [v[2] for v in export.verts]) or ([0], [0], [0])
        accessors.append({"bufferView": pv, "componentType": 5126, "count": len(export.verts),
                          "type": "VEC3", "min": [min(xs), min(ys), min(zs)],
                          "max": [max(xs), max(ys), max(zs)]})
        pos_acc = len(accessors) - 1
        accessors.append({"bufferView": nv, "componentType": 5126,
                          "count": len(normals), "type": "VEC3"})
        nrm_acc = len(accessors) - 1
        accessors.append({"bufferView": iv, "componentType": 5125,
                          "count": len(idx), "type": "SCALAR"})
        idx_acc = len(accessors) - 1
        accessors.append({"bufferView": uvv, "componentType": 5126,
                          "count": len(uvs), "type": "VEC2"})
        uv_acc = len(accessors) - 1
        rgb = _hex_rgb(m.color)
        surface = material_lib.pbr(m)
        material = {
            "name": m.name,
            "pbrMetallicRoughness": {
                "baseColorFactor": [*rgb, float(surface["opacity"])],
                "metallicFactor": round(float(surface["metalness"]), 4),
                "roughnessFactor": round(float(surface["roughness"]), 4),
            },
            "extras": {
                "role": m.role,
                "watch_material": m.material,
                "material_key": surface["material_key"],
                "finish": surface["finish"],
                "texture": surface["texture"],
                "texture_seed": hashlib.sha256(
                    "|".join(str(part) for part in (
                        m.name, m.role, m.material, surface["material_key"],
                        surface["finish"], surface["texture"])).encode()
                ).hexdigest()[:16],
                "provenance": surface["provenance"],
            },
        }
        if m.role == "crystal" or float(surface["transmission"]) > 0.0:
            material["alphaMode"] = "BLEND"
            material["doubleSided"] = True
            material["pbrMetallicRoughness"]["baseColorFactor"] = [
                *rgb, min(1.0, float(surface["opacity"]))]
            material["pbrMetallicRoughness"]["roughnessFactor"] = round(
                min(0.2, float(surface["roughness"])), 4)
            material["extras"]["transmission"] = round(float(surface["transmission"]), 4)
            material["extras"]["ior"] = round(float(surface["ior"]), 4)
        elif float(surface["clearcoat"]) > 0:
            material["extras"]["clearcoat"] = round(float(surface["clearcoat"]), 4)
        materials.append(material)
        gl_meshes.append({"name": m.name, "primitives": [{
            "attributes": {"POSITION": pos_acc, "NORMAL": nrm_acc,
                           "TEXCOORD_0": uv_acc},
            "indices": idx_acc,
            "material": len(materials) - 1}]})
        nodes.append({"mesh": len(gl_meshes) - 1, "name": m.name,
                      "extras": {"role": m.role, "watch_material": m.material}})

    buf = b"".join(chunks)
    return {
        "asset": {"version": "2.0", "generator": "watchcad"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "materials": materials,
        "meshes": gl_meshes,
        "buffers": [{"byteLength": len(buf),
                     "uri": "data:application/octet-stream;base64," + base64.b64encode(buf).decode()}],
        "bufferViews": views,
        "accessors": accessors,
    }


def to_glb(meshes: list[Mesh], name: str = "watch") -> bytes:
    """Binary glTF 2.0 (GLB) with the same nodes and materials as ``to_gltf``.

    The JSON chunk is padded with spaces and the BIN chunk with zeroes to the
    4-byte boundaries required by the GLB container.  The output is
    deterministic for a given solved scene, so it can be checksummed alongside
    the other build artefacts.
    """
    gltf = to_gltf(meshes, name)
    uri = gltf["buffers"][0].pop("uri", "")
    payload = base64.b64decode(uri.split(",", 1)[1]) if "," in uri else b""
    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((-len(json_bytes)) % 4)
    payload += b"\0" * ((-len(payload)) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(payload)
    header = struct.pack("<III", 0x46546C67, 2, total)
    json_chunk = struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    bin_chunk = struct.pack("<II", len(payload), 0x004E4942) + payload
    return header + json_chunk + bin_chunk


def _hex_rgb(color: str) -> list[float]:
    """Convert an sRGB hex colour to the linear values glTF requires."""
    color = color.lstrip("#")
    if len(color) != 6:
        return [0.7, 0.7, 0.7]
    return [_srgb_to_linear(int(color[i:i + 2], 16) / 255.0)
            for i in (0, 2, 4)]


def _srgb_to_linear(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
