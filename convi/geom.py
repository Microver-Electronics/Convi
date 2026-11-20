"""Small 2D geometry helpers (KiCad frame: mm, Y down)."""
from __future__ import annotations

import math

Pt = tuple[float, float]


def rot(x: float, y: float, deg: float) -> Pt:
    """Rotate a vector the way KiCad's RotatePoint does (Y-down, CCW on screen)."""
    if deg == 0:
        return x, y
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    # snap exact quadrants to avoid -0.0000001 noise
    if abs(c) < 1e-12:
        c = 0.0
    if abs(s) < 1e-12:
        s = 0.0
    return x * c + y * s, -x * s + y * c


def add(a: Pt, b: Pt) -> Pt:
    return a[0] + b[0], a[1] + b[1]


def dist(a: Pt, b: Pt) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def norm_angle(a: float) -> float:
    a = math.fmod(a, 360.0)
    if a < 0:
        a += 360.0
    if abs(a - 360.0) < 1e-9:
        a = 0.0
    return a


def circle_from_3pts(p1: Pt, p2: Pt, p3: Pt):
    """Center and radius of circle through 3 points (None if collinear)."""
    ax, ay = p1
    bx, by = p2
    cx, cy = p3
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    return (ux, uy), math.hypot(ax - ux, ay - uy)


def arc_from_3pts_yup(s: Pt, m: Pt, e: Pt):
    """Arc through start/mid/end in a Y-UP frame.

    Returns (center, radius, start_angle_deg, end_angle_deg) with the arc going
    counter-clockwise from start_angle to end_angle (Altium convention), or None.
    """
    r = circle_from_3pts(s, m, e)
    if r is None:
        return None
    (cx, cy), rad = r
    a_s = math.degrees(math.atan2(s[1] - cy, s[0] - cx))
    a_m = math.degrees(math.atan2(m[1] - cy, m[0] - cx))
    a_e = math.degrees(math.atan2(e[1] - cy, e[0] - cx))
    # is mid within CCW sweep from s to e ?
    sweep_se = norm_angle(a_e - a_s)
    sweep_sm = norm_angle(a_m - a_s)
    if sweep_sm <= sweep_se:
        return (cx, cy), rad, norm_angle(a_s), norm_angle(a_e)
    return (cx, cy), rad, norm_angle(a_e), norm_angle(a_s)


def bezier_points(p0: Pt, p1: Pt, p2: Pt, p3: Pt, max_err: float = 0.01) -> list[Pt]:
    """Flatten a cubic bezier into a polyline."""
    length = dist(p0, p1) + dist(p1, p2) + dist(p2, p3)
    n = max(4, min(64, int(math.sqrt(length / max(max_err, 1e-6)) + 1)))
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        x = u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0]
        y = u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]
        out.append((x, y))
    return out


def arc_points(center: Pt, radius: float, a0: float, a1: float, max_err: float = 0.005) -> list[Pt]:
    """Points along a CCW (Y-up) arc from a0 to a1 degrees."""
    sweep = norm_angle(a1 - a0) or 360.0
    if radius <= 0:
        return [center]
    step = 2 * math.degrees(math.acos(max(-1.0, min(1.0, 1 - max_err / radius)))) if radius > max_err else 45
    n = max(2, int(math.ceil(sweep / max(step, 1.0))))
    return [(center[0] + radius * math.cos(math.radians(a0 + sweep * i / n)),
             center[1] + radius * math.sin(math.radians(a0 + sweep * i / n))) for i in range(n + 1)]


def polygon_area(points: list[Pt]) -> float:
    a = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def bbox(points) -> tuple[float, float, float, float] | None:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)
