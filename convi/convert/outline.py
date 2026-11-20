"""Chain KiCad Edge.Cuts graphics into closed contours."""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..geom import bezier_points, circle_from_3pts, dist, polygon_area


@dataclass
class Seg:
    kind: str          # line / arc
    a: tuple           # start
    b: tuple           # end
    m: tuple | None = None   # arc mid point

    def reversed(self) -> "Seg":
        return Seg(self.kind, self.b, self.a, self.m)

    def sample(self, max_err=0.01) -> list:
        """Points from a (inclusive) to b (exclusive)."""
        if self.kind == "line":
            return [self.a]
        r = circle_from_3pts(self.a, self.m, self.b)
        if r is None:
            return [self.a]
        (cx, cy), rad = r
        a0 = math.atan2(self.a[1] - cy, self.a[0] - cx)
        am = math.atan2(self.m[1] - cy, self.m[0] - cx)
        a1 = math.atan2(self.b[1] - cy, self.b[0] - cx)
        # determine direction through mid
        def norm(x):
            while x < 0:
                x += 2 * math.pi
            while x >= 2 * math.pi:
                x -= 2 * math.pi
            return x
        ccw_total = norm(a1 - a0) or 2 * math.pi
        ccw_mid = norm(am - a0)
        sweep = ccw_total if ccw_mid <= ccw_total else -(2 * math.pi - ccw_total)
        step = 2 * math.acos(max(-1.0, min(1.0, 1 - max_err / rad))) if rad > max_err else math.pi / 4
        n = max(2, int(math.ceil(abs(sweep) / max(step, 1e-3))))
        return [(cx + rad * math.cos(a0 + sweep * i / n), cy + rad * math.sin(a0 + sweep * i / n)) for i in range(n)]


def shapes_to_segs(shapes) -> tuple[list[Seg], list[list[Seg]]]:
    """Convert Shape objects to open segments + already closed contours (circles, rects, polys)."""
    segs: list[Seg] = []
    closed: list[list[Seg]] = []
    for sh in shapes:
        p = sh.pts
        if sh.kind == "line":
            if dist(p[0], p[1]) > 1e-6:
                segs.append(Seg("line", p[0], p[1]))
        elif sh.kind == "arc":
            segs.append(Seg("arc", p[0], p[2], p[1]))
        elif sh.kind == "circle":
            c, e = p
            r = dist(c, e)
            if r > 0:
                a = (c[0] + r, c[1])
                b = (c[0] - r, c[1])
                closed.append([Seg("arc", a, b, (c[0], c[1] - r)), Seg("arc", b, a, (c[0], c[1] + r))])
        elif sh.kind == "rect":
            (x1, y1), (x2, y2) = p
            pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
            closed.append([Seg("line", pts[i], pts[(i + 1) % 4]) for i in range(4)])
        elif sh.kind == "poly":
            if sh.arcs:
                items = sh.arcs
                cont = []
                prev = None
                first = None
                for it in items:
                    if it[0] == "xy":
                        pt = (it[1], it[2])
                        if prev is not None and dist(prev, pt) > 1e-6:
                            cont.append(Seg("line", prev, pt))
                        prev = pt
                    else:
                        s, m, e = it[1], it[2], it[3]
                        if prev is not None and dist(prev, s) > 1e-6:
                            cont.append(Seg("line", prev, s))
                        cont.append(Seg("arc", s, e, m))
                        prev = e
                    if first is None:
                        first = cont[0].a if cont else prev
                if cont and dist(cont[-1].b, cont[0].a) > 1e-6:
                    cont.append(Seg("line", cont[-1].b, cont[0].a))
                if cont:
                    closed.append(cont)
            else:
                pts = p
                if len(pts) >= 3:
                    closed.append([Seg("line", pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))
                                   if dist(pts[i], pts[(i + 1) % len(pts)]) > 1e-6])
        elif sh.kind == "bezier" and len(p) == 4:
            bp = bezier_points(*p)
            for i in range(len(bp) - 1):
                segs.append(Seg("line", bp[i], bp[i + 1]))
    return segs, closed


def chain(segs: list[Seg], tol: float = 0.02) -> tuple[list[list[Seg]], list[list[Seg]]]:
    """Chain open segments into contours. Returns (closed, open_chains)."""
    remaining = list(segs)
    closed, opened = [], []

    def key(p):
        return (round(p[0] / tol), round(p[1] / tol))

    while remaining:
        cur = [remaining.pop(0)]
        progress = True
        while progress:
            progress = False
            if dist(cur[-1].b, cur[0].a) <= tol and len(cur) > 1:
                break
            end = cur[-1].b
            best, best_i, best_rev, best_d = None, -1, False, tol
            for i, s in enumerate(remaining):
                d1 = dist(end, s.a)
                d2 = dist(end, s.b)
                if d1 <= best_d:
                    best, best_i, best_rev, best_d = s, i, False, d1
                if d2 <= best_d:
                    best, best_i, best_rev, best_d = s, i, True, d2
            if best is not None:
                remaining.pop(best_i)
                nxt = best.reversed() if best_rev else best
                # snap the joint
                nxt = Seg(nxt.kind, end, nxt.b, nxt.m)
                cur.append(nxt)
                progress = True
        if dist(cur[-1].b, cur[0].a) <= tol:
            cur[-1] = Seg(cur[-1].kind, cur[-1].a, cur[0].a, cur[-1].m)
            closed.append(cur)
        else:
            # try extending backwards too
            opened.append(cur)
    return closed, opened


def contour_points(cont: list[Seg], max_err=0.01) -> list:
    pts = []
    for s in cont:
        pts.extend(s.sample(max_err))
    return pts


def contour_area(cont: list[Seg]) -> float:
    return polygon_area(contour_points(cont, 0.05))


def point_in_poly(pt, poly) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-300) + xi:
            inside = not inside
        j = i
    return inside


def build_board_outline(shapes, tol=0.02):
    """Return (outer_contour, cutouts, problems)."""
    segs, closed = shapes_to_segs(shapes)
    ch_closed, opened = chain(segs, tol)
    contours = closed + ch_closed
    problems = []
    if opened:
        problems.append(f"{len(opened)} Edge.Cuts chain(s) are not closed (gap > {tol} mm)")
    if not contours:
        return None, [], problems + ["No closed board outline found on Edge.Cuts"]
    contours.sort(key=lambda c: abs(contour_area(c)), reverse=True)
    outer = contours[0]
    outer_pts = contour_points(outer, 0.05)
    cutouts = []
    for c in contours[1:]:
        pts = contour_points(c, 0.05)
        if pts and point_in_poly(pts[0], outer_pts):
            cutouts.append(c)
        else:
            problems.append("Additional board outline outside the main outline (Altium supports one board shape); "
                            "kept as mechanical graphics only")
    return outer, cutouts, problems
