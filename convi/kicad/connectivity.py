"""KiCad board connectivity from the copper itself: how many connections each net still lacks.

A KiCad board that is not fully routed already has open connections. Altium's un-routed net check
reports those too, so Convi compares per net and only treats what Altium reports beyond KiCad as a
conversion difference. Pure Python (no geometry library): items are tracks/arcs (segments with a
half width), vias (discs over a layer range), pads (polygons on their copper layers) and the zone
fills stored in the board file (KiCad's own fill, islands already removed).
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict

EPS = 0.002      # mm: touching within KiCad's 1 nm rounding and arc flattening
CELL = 2.0       # mm: spatial hash cell


# ------------------------------------------------------------------ geometry helpers
def _pt_seg(p, a, b):
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    L = dx * dx + dy * dy
    if L == 0:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


def _seg_cross(a, b, c, d):
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    d1, d2, d3, d4 = o(c, d, a), o(c, d, b), o(a, b, c), o(a, b, d)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _seg_seg(a, b, c, d):
    if _seg_cross(a, b, c, d):
        return 0.0
    return min(_pt_seg(a, c, d), _pt_seg(b, c, d), _pt_seg(c, a, b), _pt_seg(d, a, b))


def _inside(p, poly):
    x, y = p
    n = len(poly)
    c = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            c = not c
        j = i
    return c


def _arc_points(s, m, e, n=12):
    (x1, y1), (x2, y2), (x3, y3) = s, m, e
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12:
        return [s, e]
    ux = ((x1 * x1 + y1 * y1) * (y2 - y3) + (x2 * x2 + y2 * y2) * (y3 - y1) + (x3 * x3 + y3 * y3) * (y1 - y2)) / d
    uy = ((x1 * x1 + y1 * y1) * (x3 - x2) + (x2 * x2 + y2 * y2) * (x1 - x3) + (x3 * x3 + y3 * y3) * (x2 - x1)) / d
    r = math.hypot(x1 - ux, y1 - uy)
    a1 = math.atan2(y1 - uy, x1 - ux)
    a2 = math.atan2(y2 - uy, x2 - ux)
    a3 = math.atan2(y3 - uy, x3 - ux)

    def norm(a):
        return a % (2 * math.pi)
    span = norm(a3 - a1)
    if norm(a2 - a1) > span:          # the arc goes the other way round
        span -= 2 * math.pi
    return [(ux + r * math.cos(a1 + span * k / n), uy + r * math.sin(a1 + span * k / n)) for k in range(n + 1)]


def _pad_poly(pd):
    """Pad copper outline (absolute), polygonal approximation."""
    sx, sy = pd.size
    ang = math.radians(-pd.angle)       # KiCad: y down, angle counter-clockwise on screen
    ox, oy = pd.offset or (0.0, 0.0)
    if pd.shape == "circle":
        r = sx / 2
        loc = [(ox + r * math.cos(2 * math.pi * k / 16), oy + r * math.sin(2 * math.pi * k / 16)) for k in range(16)]
    elif pd.shape == "oval" and sx != sy:
        r = min(sx, sy) / 2
        h = (max(sx, sy) - min(sx, sy)) / 2
        loc = []
        for side in (1, -1):
            for k in range(9):
                t = math.pi * k / 8 - math.pi / 2
                if sx > sy:
                    loc.append((ox + side * h + side * r * math.cos(t), oy + r * math.sin(t) * side))
                else:
                    loc.append((ox + r * math.sin(t) * side, oy + side * h + side * r * math.cos(t)))
    else:
        loc = [(ox - sx / 2, oy - sy / 2), (ox + sx / 2, oy - sy / 2), (ox + sx / 2, oy + sy / 2), (ox - sx / 2, oy + sy / 2)]
    c, s = math.cos(ang), math.sin(ang)
    return [(pd.pos[0] + x * c - y * s, pd.pos[1] + x * s + y * c) for x, y in loc]


# ------------------------------------------------------------------ items
class _Item:
    __slots__ = ("kind", "net", "layers", "segs", "hw", "poly", "c", "r", "bbox", "edges", "ybands")

    def __init__(self, kind, net, layers):
        self.kind, self.net, self.layers = kind, net, layers
        self.segs, self.hw, self.poly, self.c, self.r = (), 0.0, None, None, 0.0
        self.edges, self.ybands = None, None

    def finish(self):
        pts = [p for s in self.segs for p in s] if self.segs else (self.poly or [self.c])
        g = self.hw + self.r
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        self.bbox = (min(xs) - g, min(ys) - g, max(xs) + g, max(ys) + g)
        if self.kind == "zone":       # fill polygons: index edges by y band for point and distance tests
            n = len(self.poly)
            self.edges = [(self.poly[i - 1], self.poly[i]) for i in range(n)]
            bands = defaultdict(list)
            for e in self.edges:
                y0, y1 = sorted((e[0][1], e[1][1]))
                for k in range(int(math.floor(y0 / CELL)), int(math.floor(y1 / CELL)) + 1):
                    bands[k].append(e)
            self.ybands = bands
        return self

    # -- polygon helpers for pads and zones
    def contains(self, p):
        if self.kind == "zone":
            x, y = p
            c = False
            for (xi, yi), (xj, yj) in self.ybands.get(int(math.floor(y / CELL)), ()):
                if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                    c = not c
            return c
        return _inside(p, self.poly)

    def poly_edges_near(self, box):
        if self.kind != "zone":
            n = len(self.poly)
            return [(self.poly[i - 1], self.poly[i]) for i in range(n)]
        out = []
        for k in range(int(math.floor(box[1] / CELL)), int(math.floor(box[3] / CELL)) + 1):
            out.extend(self.ybands.get(k, ()))
        return out


def _dist(a: _Item, b: _Item) -> float:
    """Gap between two items on a common layer (<= 0 means connected)."""
    if a.poly is None and b.poly is not None:
        a, b = b, a
    if a.poly is None:                       # segments / discs only
        ga = a.segs if a.segs else [(a.c, a.c)]
        gb = b.segs if b.segs else [(b.c, b.c)]
        d = min(_seg_seg(p, q, r, s) for p, q in ga for r, s in gb)
        return d - (a.hw + a.r) - (b.hw + b.r)
    # a is a polygon (pad or zone fill)
    if b.poly is None:
        g = b.hw + b.r
        geo = b.segs if b.segs else [(b.c, b.c)]
        for p, q in geo:
            if a.contains(p) or a.contains(q):
                return -1.0
        box = (b.bbox[0], b.bbox[1], b.bbox[2], b.bbox[3])
        d = min((_seg_seg(p, q, e0, e1) for p, q in geo for e0, e1 in a.poly_edges_near(box)), default=1e9)
        return d - g
    # polygon - polygon
    for p in b.poly:
        if a.contains(p):
            return -1.0
    for p in a.poly:
        if b.contains(p):
            return -1.0
    box = (max(a.bbox[0], b.bbox[0]), max(a.bbox[1], b.bbox[1]), min(a.bbox[2], b.bbox[2]), min(a.bbox[3], b.bbox[3]))
    # edge crossings, each edge of a only against the edges of b in its own y band and x range (was every
    # pair in the common box: two large fills of one net = 10^8 tests, 125 s -> about 2 s on a test board)
    ea = a.poly_edges_near(box)
    for e in ea:
        ex0, ex1 = (e[0][0], e[1][0]) if e[0][0] <= e[1][0] else (e[1][0], e[0][0])
        ey0, ey1 = (e[0][1], e[1][1]) if e[0][1] <= e[1][1] else (e[1][1], e[0][1])
        for f in b.poly_edges_near((ex0, ey0, ex1, ey1)):
            if (f[0][0] < ex0 and f[1][0] < ex0) or (f[0][0] > ex1 and f[1][0] > ex1) or \
                    (f[0][1] < ey0 and f[1][1] < ey0) or (f[0][1] > ey1 and f[1][1] > ey1):
                continue
            if _seg_cross(e[0], e[1], f[0], f[1]):
                return -1.0
    return 1e9


def open_connections(board) -> Counter:
    """Per KiCad net: number of connections still missing (connected copper groups - 1)."""
    cu = [l.name for l in board.layers if l.name.endswith(".Cu")]
    order = {n: i for i, n in enumerate(cu)}
    items: list[_Item] = []
    for t in board.tracks:
        if not t.net or t.layer not in order:
            continue
        it = _Item("track", t.net, (t.layer,))
        it.segs = ((t.start, t.end),)
        it.hw = t.width / 2
        items.append(it.finish())
    for t in board.arcs:
        if not t.net or t.layer not in order:
            continue
        pts = _arc_points(t.start, t.mid, t.end) if t.mid else [t.start, t.end]
        it = _Item("arc", t.net, (t.layer,))
        it.segs = tuple(zip(pts, pts[1:]))
        it.hw = t.width / 2
        items.append(it.finish())
    for v in board.vias:
        if not v.net:
            continue
        a, b = (order.get(v.layers[0], 0), order.get(v.layers[-1], len(cu) - 1)) if v.layers else (0, len(cu) - 1)
        lo, hi = min(a, b), max(a, b)
        it = _Item("via", v.net, tuple(cu[lo:hi + 1]))
        it.c, it.r = v.pos, v.size / 2
        items.append(it.finish())
    for fp in board.footprints:
        for pd in fp.pads:
            lays = tuple(l for l in pd.layers if l in order)
            if not pd.net or not lays or pd.size[0] <= 0 or pd.size[1] <= 0:
                continue
            it = _Item("pad", pd.net, lays)
            it.poly = _pad_poly(pd)
            items.append(it.finish())
    for z in board.zones:
        if z.keepout or not z.net:
            continue
        for lay, polys in (z.filled_polys or {}).items():
            if lay not in order:
                continue
            for pts in polys:
                if len(pts) < 3:
                    continue
                area = abs(sum(pts[i - 1][0] * pts[i][1] - pts[i][0] * pts[i - 1][1] for i in range(len(pts)))) / 2
                if area < 0.01:          # fill slivers KiCad leaves after fracturing: not counted as islands
                    continue
                it = _Item("zone", z.net, (lay,))
                it.poly = [tuple(p) for p in pts]
                items.append(it.finish())

    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # spatial hash per (net, layer)
    grid: dict = defaultdict(list)
    for i, it in enumerate(items):
        x0, y0, x1, y1 = it.bbox
        for lay in it.layers:
            for gx in range(int(math.floor(x0 / CELL)), int(math.floor(x1 / CELL)) + 1):
                for gy in range(int(math.floor(y0 / CELL)), int(math.floor(y1 / CELL)) + 1):
                    grid[(it.net, lay, gx, gy)].append(i)
    tested = set()
    for key, idx in grid.items():
        for a_i in range(len(idx)):
            i = idx[a_i]
            for b_i in range(a_i + 1, len(idx)):
                j = idx[b_i]
                if (i, j) in tested:
                    continue
                tested.add((i, j))
                ri, rj = find(i), find(j)
                if ri == rj:
                    continue
                A, B = items[i], items[j]
                if A.bbox[0] > B.bbox[2] + EPS or B.bbox[0] > A.bbox[2] + EPS or \
                   A.bbox[1] > B.bbox[3] + EPS or B.bbox[1] > A.bbox[3] + EPS:
                    continue
                if _dist(A, B) <= EPS:
                    parent[ri] = rj
    # a group of fill only (a stray island KiCad's fill kept, e.g. 0.56 x 0.13 mm of GND on In2) needs no
    # connection - KiCad reports 0 unconnected items there and Altium un-routes no pour island; dangling
    # tracks / vias do count (Altium's un-routed check lists them, e.g. 48 = 40 pad groups + 8 track groups)
    groups: dict = defaultdict(set)
    real = set()
    for i, it in enumerate(items):
        groups[it.net].add(find(i))
        if it.kind != "zone":
            real.add(find(i))
    groups = {n: g & real for n, g in groups.items()}
    return Counter({n: len(g) - 1 for n, g in groups.items() if len(g) > 1})
