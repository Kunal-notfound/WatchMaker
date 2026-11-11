"""Tiny dependency-free mesh kernel.

The production backend is OpenCascade (CadQuery/build123d) and exports STEP.
This kernel exists so the whole pipeline runs on a laptop with no CAD stack:
it generates watertight-enough meshes for STL/3MF/glTF, for the viewer, and for
volume/mass estimates.  All units are millimetres, Z is the watch axis.
"""

from __future__ import annotations

import base64
import json
import math
import struct
from dataclasses import dataclass, field

Vec = tuple[float, float, float]


@dataclass
class Mesh:
    name: str
    verts: list[Vec] = field(default_factory=list)
    faces: list[tuple[int, int, int]] = field(default_factory=list)
    color: str = "#b9c2cc"
    material: str = "steel"
    role: str = "part"
    meta: dict = field(default_factory=dict)

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
                    self.material, self.role, dict(self.meta))

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
        pos = [c for v in m.verts for c in v]
        pos_bytes = struct.pack(f"<{len(pos)}f", *pos)
        idx = [i for f in m.faces for i in f]
        idx_bytes = struct.pack(f"<{len(idx)}I", *idx)
        pv = add_view(pos_bytes, 34962)
        iv = add_view(idx_bytes, 34963)

        xs, ys, zs = m.verts and ([v[0] for v in m.verts], [v[1] for v in m.verts], [v[2] for v in m.verts]) or ([0], [0], [0])
        accessors.append({"bufferView": pv, "componentType": 5126, "count": len(m.verts),
                          "type": "VEC3", "min": [min(xs), min(ys), min(zs)],
                          "max": [max(xs), max(ys), max(zs)]})
        pos_acc = len(accessors) - 1
        accessors.append({"bufferView": iv, "componentType": 5125,
                          "count": len(idx), "type": "SCALAR"})
        idx_acc = len(accessors) - 1
        rgb = _hex_rgb(m.color)
        materials.append({
            "name": m.name,
            "pbrMetallicRoughness": {
                "baseColorFactor": [*rgb, 1.0],
                "metallicFactor": 0.15 if m.role in ("dial", "crystal") else 0.85,
                "roughnessFactor": 0.6 if m.role == "dial" else 0.3,
            },
        })
        gl_meshes.append({"name": m.name, "primitives": [{
            "attributes": {"POSITION": pos_acc}, "indices": idx_acc,
            "material": len(materials) - 1}]})
        nodes.append({"mesh": len(gl_meshes) - 1, "name": m.name})

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


def _hex_rgb(color: str) -> list[float]:
    color = color.lstrip("#")
    if len(color) != 6:
        return [0.7, 0.7, 0.7]
    return [int(color[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
