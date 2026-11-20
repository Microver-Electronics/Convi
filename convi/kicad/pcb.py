"""KiCad .kicad_pcb reader (KiCad 6 .. 10, tolerant of newer files).

All coordinates are kept in the KiCad board frame: millimetres, Y pointing down.
Footprint children are resolved to absolute board coordinates; their local
(footprint frame) coordinates are kept as well for library generation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from .. import sexpr as S
from ..geom import rot
from .common import Stroke, TextEffects, parse_effects, parse_stroke, Issues, unescape_name

# Version from which footprint children are stored in the library frame (KiCad 10.99+).
FP_AFFINE_VERSION = 20260616

COPPER_ORDER = ["F.Cu"] + [f"In{i}.Cu" for i in range(1, 31)] + ["B.Cu"]

FLIP_LAYER = {
    "F.Cu": "B.Cu", "B.Cu": "F.Cu", "F.SilkS": "B.SilkS", "B.SilkS": "F.SilkS",
    "F.Mask": "B.Mask", "B.Mask": "F.Mask", "F.Paste": "B.Paste", "B.Paste": "F.Paste",
    "F.Adhes": "B.Adhes", "B.Adhes": "F.Adhes", "F.CrtYd": "B.CrtYd", "B.CrtYd": "F.CrtYd",
    "F.Fab": "B.Fab", "B.Fab": "F.Fab",
}


# ----------------------------------------------------------------- data classes

@dataclass
class Layer:
    ordinal: int
    name: str            # canonical name, e.g. "F.Cu", "User.1"
    type: str            # signal / power / mixed / jumper / user / front / back ...
    user_name: str = ""  # optional display name

    @property
    def display(self) -> str:
        return self.user_name or self.name

    @property
    def is_copper(self) -> bool:
        return self.name.endswith(".Cu")


@dataclass
class StackupLayer:
    name: str               # "F.Cu", "dielectric 1", "F.Mask" ...
    type: str               # "copper", "core", "prepreg", "Top Solder Mask", ...
    thickness: float = 0.0  # mm
    material: str = ""
    epsilon_r: float = 0.0
    loss_tangent: float = 0.0
    color: str = ""
    sublayers: list = field(default_factory=list)  # extra (thickness, material, er, tand)


@dataclass
class Shape:
    """A graphic primitive. kind: line, arc, circle, rect, poly, bezier."""
    kind: str
    layer: str
    width: float = 0.0
    fill: bool = False
    pts: list = field(default_factory=list)   # line/rect: [a,b]; arc: [s,m,e]; circle: [c, p]; poly: [...]; bezier: 4 pts
    net: str = ""
    stroke_style: str = "solid"
    locked: bool = False
    hatch: str = ""          # hatch fill type (KiCad 9+), treated as filled
    local_pts: list | None = None  # footprint frame points
    arcs: list | None = None  # for poly with arc segments: list of ("arc", s, m, e) in pts order
    uuid: str = ""


@dataclass
class Text:
    text: str
    pos: tuple
    angle: float
    layer: str
    effects: TextEffects
    kind: str = "text"       # text / reference / value / field / textbox
    name: str = ""          # field name for footprint properties
    hidden: bool = False
    knockout: bool = False
    local_pos: tuple | None = None
    local_angle: float = 0.0
    box: list | None = None  # textbox corner points (abs)
    border: Stroke | None = None
    keep_upright: bool = False
    render: tuple | None = None     # KiCad render_cache: (text, angle, [polygon point lists]) in absolute mm
    margins: tuple | None = None    # textbox / table cell inner margins (left, top, right, bottom) mm


@dataclass
class Drill:
    shape: str = "circle"   # circle / oval
    w: float = 0.0
    h: float = 0.0
    offset: tuple = (0.0, 0.0)


@dataclass
class PadLayerShape:
    shape: str
    size: tuple
    offset: tuple = (0.0, 0.0)
    rr_ratio: float = 0.0
    chamfer_ratio: float = 0.0
    chamfer: set = field(default_factory=set)
    delta: tuple = (0.0, 0.0)
    anchor: str = "circle"
    primitives: list = field(default_factory=list)


@dataclass
class Pad:
    number: str
    type: str                 # smd / thru_hole / np_thru_hole / connect
    shape: str                # circle / rect / oval / trapezoid / roundrect / custom / chamfered via roundrect+chamfer
    pos: tuple                # absolute
    angle: float              # absolute degrees
    size: tuple
    layers: list
    local_pos: tuple = (0.0, 0.0)
    drill: Drill | None = None
    rr_ratio: float = 0.0
    chamfer_ratio: float = 0.0
    chamfer: set = field(default_factory=set)
    delta: tuple = (0.0, 0.0)
    offset: tuple = (0.0, 0.0)   # pad shape offset relative to the hole (local, rotated by pad angle)
    net: str = ""
    pinfunction: str = ""
    pintype: str = ""
    mask_margin: float | None = None
    paste_margin: float | None = None
    paste_ratio: float | None = None
    clearance: float | None = None
    zone_connect: int | None = None   # 0 none, 1 thermal, 2 solid, 3 thru-hole-only
    thermal_width: float | None = None
    thermal_gap: float | None = None
    thermal_angle: float | None = None
    fab_property: str = ""
    anchor: str = "circle"
    custom_options: str = ""
    primitives: list = field(default_factory=list)   # Shapes in pad-local frame (unrotated)
    padstack_mode: str = ""      # "", front_inner_back, custom
    layer_shapes: dict = field(default_factory=dict)  # layer -> PadLayerShape
    remove_unused_layers: bool = False
    keep_end_layers: bool = False
    tenting: tuple = (None, None)
    die_length: float = 0.0
    uuid: str = ""
    backdrill: bool = False


@dataclass
class Model3D:
    path: str
    offset: tuple = (0.0, 0.0, 0.0)
    scale: tuple = (1.0, 1.0, 1.0)
    rotate: tuple = (0.0, 0.0, 0.0)
    show: bool = True
    opacity: float = 1.0


@dataclass
class Zone:
    net: str
    layers: list
    outline: list                 # main outline points (abs), arcs flattened to s/m/e points
    holes: list = field(default_factory=list)
    outline_items: list = field(default_factory=list)  # [('xy',x,y) | ('arc',s,m,e)]
    hole_items: list = field(default_factory=list)
    name: str = ""
    priority: int = 0
    connect_pads: str = "thermal"  # thermal / yes (solid) / no / thru_hole_only
    clearance: float = 0.0
    min_thickness: float = 0.25
    filled: bool = True
    fill_mode: str = "solid"      # solid / hatch
    hatch_thickness: float = 0.0
    hatch_gap: float = 0.0
    hatch_orientation: float = 0.0
    thermal_gap: float = 0.5
    thermal_bridge_width: float = 0.5
    island_removal: int = 0       # 0 always, 1 never, 2 below area
    island_area_min: float = 0.0
    smoothing: str = ""
    smoothing_radius: float = 0.0
    keepout: dict | None = None   # tracks/vias/pads/copperpour/footprints -> allowed bool
    is_teardrop: bool = False
    locked: bool = False
    filled_polys: dict = field(default_factory=dict)  # layer -> list of point lists
    in_footprint: str = ""
    uuid: str = ""


@dataclass
class Track:
    start: tuple
    end: tuple
    width: float
    layer: str
    net: str = ""
    mid: tuple | None = None    # arcs only
    locked: bool = False
    uuid: str = ""


@dataclass
class Via:
    pos: tuple
    size: float
    drill: float
    layers: tuple                 # (start, end)
    net: str = ""
    via_type: str = "through"     # through / blind / buried / micro
    tenting: tuple = (None, None)
    remove_unused_layers: bool = False
    keep_end_layers: bool = False
    locked: bool = False
    free: bool = False
    layer_sizes: dict = field(default_factory=dict)   # padstack per-layer diameter
    uuid: str = ""
    backdrill: bool = False
    zone_layers: tuple = ()       # zone_layer_connections: layers where a zone connects (unused-layer removal)


@dataclass
class Footprint:
    lib_id: str
    ref: str
    value: str
    pos: tuple
    angle: float
    layer: str                    # F.Cu / B.Cu
    attrs: set = field(default_factory=set)
    path: str = ""                # schematic link "/uuid/uuid"
    sheetname: str = ""
    sheetfile: str = ""
    properties: dict = field(default_factory=dict)
    descr: str = ""
    tags: str = ""
    pads: list = field(default_factory=list)
    shapes: list = field(default_factory=list)
    texts: list = field(default_factory=list)
    zones: list = field(default_factory=list)
    models: list = field(default_factory=list)
    clearance: float | None = None
    mask_margin: float | None = None
    paste_margin: float | None = None
    paste_ratio: float | None = None
    zone_connect: int | None = None
    thermal_width: float | None = None
    thermal_gap: float | None = None
    net_tie_groups: list = field(default_factory=list)
    locked: bool = False
    uuid: str = ""
    embedded: dict = field(default_factory=dict)  # name -> EmbeddedFile
    dnp: bool = False
    raw_unsupported: list = field(default_factory=list)

    @property
    def flipped(self) -> bool:
        return self.layer == "B.Cu"

    @property
    def lib_name(self) -> str:
        return self.lib_id.split(":", 1)[1] if ":" in self.lib_id else self.lib_id

    @property
    def lib_nick(self) -> str:
        return self.lib_id.split(":", 1)[0] if ":" in self.lib_id else ""


@dataclass
class NetClass:
    name: str
    clearance: float | None = None
    track_width: float | None = None
    via_dia: float | None = None
    via_drill: float | None = None
    uvia_dia: float | None = None
    uvia_drill: float | None = None
    dp_width: float | None = None
    dp_gap: float | None = None
    dp_via_gap: float | None = None
    priority: int = 0
    nets: list = field(default_factory=list)
    patterns: list = field(default_factory=list)
    description: str = ""
    sch_color: tuple | None = None      # schematic wire colour (r, g, b) when the class sets one
    wire_width: float | None = None     # schematic wire width, mil


@dataclass
class Board:
    path: Path
    version: int = 0
    generator: str = ""
    generator_version: str = ""
    thickness: float = 1.6
    paper: str = "A4"
    title_block: dict = field(default_factory=dict)
    layers: list = field(default_factory=list)
    stackup: list = field(default_factory=list)
    stackup_info: dict = field(default_factory=dict)
    setup: dict = field(default_factory=dict)
    nets: list = field(default_factory=list)       # ordered net names ("" first)
    netclasses_legacy: list = field(default_factory=list)
    footprints: list = field(default_factory=list)
    tracks: list = field(default_factory=list)
    arcs: list = field(default_factory=list)
    vias: list = field(default_factory=list)
    zones: list = field(default_factory=list)
    shapes: list = field(default_factory=list)
    texts: list = field(default_factory=list)
    dimensions: list = field(default_factory=list)   # raw nodes
    tables: list = field(default_factory=list)       # raw nodes
    barcodes: list = field(default_factory=list)     # raw nodes
    images: list = field(default_factory=list)       # raw nodes
    groups: list = field(default_factory=list)
    embedded: dict = field(default_factory=dict)
    properties: dict = field(default_factory=dict)
    unsupported: dict = field(default_factory=dict)  # token -> count
    variants: list = field(default_factory=list)

    @property
    def copper_layers(self) -> list[Layer]:
        cu = [l for l in self.layers if l.is_copper]
        return sorted(cu, key=lambda l: COPPER_ORDER.index(l.name) if l.name in COPPER_ORDER else 99)

    def layer(self, name: str) -> Layer | None:
        for l in self.layers:
            if l.name == name:
                return l
        return None


# ----------------------------------------------------------------- parsing helpers

def _uuid(n) -> str:
    return S.val(n, "uuid", "") or S.val(n, "tstamp", "") or ""


def _num(v, d=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _opt(n, name):
    v = S.val(n, name)
    return None if v is None else _num(v)


def expand_layers(names: list[str], board_layers: list[str]) -> list[str]:
    """Expand KiCad wildcard layer names like '*.Cu', 'F&B.Cu', '*.Mask'."""
    out: list[str] = []
    cu = [l for l in board_layers if l.endswith(".Cu")]
    for n in names:
        if n == "*.Cu":
            out.extend(cu)
        elif n == "*In.Cu":
            out.extend([l for l in cu if l.startswith("In")])
        elif n.startswith("*."):
            suf = n[2:]
            out.extend([f"F.{suf}", f"B.{suf}"])
        elif n.startswith("F&B."):
            suf = n[4:]
            out.extend([f"F.{suf}", f"B.{suf}"])
        else:
            out.append(n)
    seen = set()
    res = []
    for l in out:
        if l not in seen:
            seen.add(l)
            res.append(l)
    return res


class _Ctx:
    def __init__(self, board: Board, issues: Issues):
        self.board = board
        self.issues = issues
        self.net_by_code: dict[str, str] = {}
        self.layer_names: list[str] = []
        self.legacy_fp = True

    def net(self, node) -> str:
        """Resolve (net N) or (net "name") or (net N "name")."""
        return unescape_name(self._net(node))

    def _net(self, node) -> str:
        n = S.find(node, "net")
        if n is None:
            return ""
        if len(n) >= 3:
            return str(n[2])
        if len(n) == 2:
            v = n[1]
            if isinstance(v, S.QStr):
                return str(v)
            if str(v).lstrip("-").isdigit():
                return self.net_by_code.get(str(v), "")
            return str(v)
        return ""


def _parse_shape(node, ctx: _Ctx, fp: Footprint | None = None) -> Shape | None:
    t = node[0]
    kind = t.split("_", 1)[1] if "_" in t else t
    if kind == "curve":
        kind = "bezier"
    layer = S.val(node, "layer", "")
    st = parse_stroke(node)
    width = st.width if st.width is not None else S.fval(node, "width", 0.0)
    fill = False
    hatch = ""
    f = S.find(node, "fill")
    if f is not None and len(f) > 1:
        fv = str(f[1])
        if fv in ("solid", "yes"):
            fill = True
        elif fv in ("hatch", "reverse_hatch", "cross_hatch"):
            fill = True
            hatch = fv
    elif S.has_atom(node, "fill"):
        fill = True
    sh = Shape(kind=kind, layer=str(layer), width=width, fill=fill, hatch=hatch,
               stroke_style=st.style, net=ctx.net(node), uuid=_uuid(node),
               locked=S.flag(node, "locked"))
    if kind in ("line", "rect"):
        sh.pts = [S.xy(S.find(node, "start")), S.xy(S.find(node, "end"))]
    elif kind == "arc":
        if S.find(node, "mid") is not None:
            sh.pts = [S.xy(S.find(node, "start")), S.xy(S.find(node, "mid")), S.xy(S.find(node, "end"))]
        else:
            # legacy: (start center) (end start-point) (angle deg)
            c = S.xy(S.find(node, "start"))
            p = S.xy(S.find(node, "end"))
            ang = S.fval(node, "angle", 0.0)
            e = (c[0] + rot(p[0] - c[0], p[1] - c[1], -ang)[0], c[1] + rot(p[0] - c[0], p[1] - c[1], -ang)[1])
            m = (c[0] + rot(p[0] - c[0], p[1] - c[1], -ang / 2)[0], c[1] + rot(p[0] - c[0], p[1] - c[1], -ang / 2)[1])
            sh.pts = [p, m, e]
    elif kind == "circle":
        sh.pts = [S.xy(S.find(node, "center")), S.xy(S.find(node, "end"))]
    elif kind == "poly":
        raw = S.pts(node)
        flat = []
        arcs = []
        for item in raw:
            if item[0] == "xy":
                flat.append((item[1], item[2]))
            else:
                arcs.append(item)
                flat.extend([item[1], item[2], item[3]])
        sh.pts = flat
        if arcs:
            sh.arcs = raw
    elif kind == "bezier":
        sh.pts = [(p[1], p[2]) for p in S.pts(node) if p[0] == "xy"]
    elif kind == "ellipse":
        # KiCad 10.99+ native ellipse: approximate as poly
        c = S.xy(S.find(node, "center"))
        rx = S.fval(node, "major_radius", 0) or S.fval(node, "radius_x", 0)
        ry = S.fval(node, "minor_radius", 0) or S.fval(node, "radius_y", 0)
        ang = S.fval(node, "angle", 0)
        pts = []
        for i in range(72):
            a = 2 * math.pi * i / 72
            px, py = rx * math.cos(a), ry * math.sin(a)
            px, py = rot(px, py, ang)
            pts.append((c[0] + px, c[1] + py))
        sh.kind = "poly"
        sh.pts = pts
    else:
        ctx.issues.warn("pcb", f"Unsupported graphic '{t}' skipped")
        return None
    if fp is not None:
        sh.local_pts = list(sh.pts)
        if sh.arcs:
            sh.arcs = [("xy", *_fp_abs(fp, (a[1], a[2]))) if a[0] == "xy" else
                       ("arc", _fp_abs(fp, a[1]), _fp_abs(fp, a[2]), _fp_abs(fp, a[3])) for a in sh.arcs]
        sh.pts = [_fp_abs(fp, p) for p in sh.pts]
    return sh


def _fp_abs(fp: Footprint, p) -> tuple:
    dx, dy = rot(p[0], p[1], fp.angle)
    return fp.pos[0] + dx, fp.pos[1] + dy


def _parse_render_cache(node):
    """KiCad stores the outline-font (TTF) rendering of a text as absolute polygons:
    (render_cache "TEXT" angle (polygon (pts (xy ..) ..) ..) ..). Returns (text, angle, [[(x, y), ..], ..])."""
    rc = S.find(node, "render_cache")
    if rc is None:
        return None
    try:
        ang = float(rc[2]) if len(rc) > 2 and not isinstance(rc[2], list) else 0.0
    except (TypeError, ValueError):
        ang = 0.0
    polys = []
    for pg in S.findall(rc, "polygon"):
        for pt in S.findall(pg, "pts"):
            ring = [(float(c[1]), float(c[2])) for c in pt[1:] if isinstance(c, list) and c and c[0] == "xy"]
            if ring:
                polys.append(ring)
    return (str(rc[1]) if len(rc) > 1 else "", ang, polys) if polys else None


def _parse_text(node, ctx: _Ctx, fp: Footprint | None = None) -> Text | None:
    t = node[0]
    if t in ("fp_text", "gr_text"):
        if t == "fp_text":
            kind = str(node[1])
            txt = str(node[2]) if len(node) > 2 else ""
        else:
            kind = "text"
            txt = str(node[1]) if len(node) > 1 else ""
        name = ""
    elif t == "property":
        name = str(node[1])
        txt = str(node[2]) if len(node) > 2 else ""
        kind = {"Reference": "reference", "Value": "value"}.get(name, "field")
    else:
        return None
    x, y, ang = S.at(node)
    layer = str(S.val(node, "layer", ""))
    eff = parse_effects(S.find(node, "effects"))
    hidden = S.flag(node, "hide") or eff.hidden
    knock = False
    lay = S.find(node, "layer")
    if lay is not None and "knockout" in lay[2:]:
        knock = True
    tx = Text(text=txt, pos=(x, y), angle=ang, layer=layer, effects=eff, kind=kind, name=name,
              hidden=hidden, knockout=knock)
    tx.render = _parse_render_cache(node)
    if fp is not None:
        at_node = S.find(node, "at")
        unlocked = (at_node is not None and "unlocked" in at_node[1:]) or S.flag(node, "unlocked")
        tx.keep_upright = not unlocked
        tx.local_pos = (x, y)
        if ctx.legacy_fp:
            tx.pos = _fp_abs(fp, (x, y))
            tx.local_angle = ang - fp.angle
        else:
            tx.pos = _fp_abs(fp, (x, y))
            tx.local_angle = ang
            tx.angle = ang + fp.angle
    if S.find(node, "unlocked") is not None and fp is not None:
        pass
    return tx


def _parse_textbox(node, ctx: _Ctx, fp: Footprint | None = None) -> Text | None:
    txt = str(node[1]) if len(node) > 1 and not isinstance(node[1], list) else ""
    layer = str(S.val(node, "layer", ""))
    eff = parse_effects(S.find(node, "effects"))
    if S.find(node, "start") is not None:
        a = S.xy(S.find(node, "start"))
        b = S.xy(S.find(node, "end"))
        box = [a, (b[0], a[1]), b, (a[0], b[1])]
    elif S.find(node, "size") is not None and S.find(node, "at") is not None:
        # schematic text boxes and table cells: (at x y angle) (size w h)
        x, y, _ = S.at(node)
        w, h = S.xy(S.find(node, "size"))
        box = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    else:
        box = [(p[1], p[2]) for p in S.pts(node) if p[0] == "xy"]
    ang = S.fval(node, "angle", 0.0)
    if fp is not None:
        box = [_fp_abs(fp, p) for p in box]
    border = parse_stroke(node) if S.flag(node, "border", True) else None
    xs = [p[0] for p in box] or [0]
    ys = [p[1] for p in box] or [0]
    mg = S.find(node, "margins")
    margins = tuple(float(v) for v in mg[1:5]) if mg is not None and len(mg) >= 5 else None
    return Text(text=txt, pos=(min(xs), min(ys)), angle=ang, layer=layer, effects=eff, kind="textbox",
                box=box, border=border, margins=margins)


def parse_table(node) -> tuple[list, list]:
    """KiCad table -> (cells as textboxes without border, [(start, end, width)] grid lines).

    Lines follow KiCad: outer border and header line with the border stroke, row / column separators
    with the separator stroke, never through a merged cell."""
    ncol = max(1, int(S.fval(node, "column_count", 1)))
    layer = str(S.val(node, "layer", ""))
    cw = S.find(node, "column_widths")
    rh = S.find(node, "row_heights")
    widths = [float(v) for v in cw[1:]] if cw is not None else []
    heights = [float(v) for v in rh[1:]] if rh is not None else []
    cells_n = S.find(node, "cells")
    raw = [c for c in (cells_n[1:] if cells_n is not None else []) if isinstance(c, list) and c[0] == "table_cell"]
    cells = []
    for c in raw:
        t = _parse_textbox(c, None)
        t.border = None
        t.layer = t.layer or layer
        sp = S.find(c, "span")
        t.name = f"{int(float(sp[1])) if sp else 1},{int(float(sp[2])) if sp and len(sp) > 2 else 1}"
        cells.append(t)
    lines = []
    if not raw or not widths or not heights:
        return cells, lines
    nrow = len(heights)
    x0, y0 = cells[0].pos
    xs = [x0]
    for w in widths:
        xs.append(xs[-1] + w)
    ys = [y0]
    for h in heights:
        ys.append(ys[-1] + h)
    owner = [[None] * ncol for _ in range(nrow)]
    for i, t in enumerate(cells):
        r, c = divmod(i, ncol)
        if r >= nrow:
            break
        cs, rs = (int(v) for v in t.name.split(","))
        if cs <= 0 or rs <= 0:
            continue
        for rr in range(r, min(nrow, r + rs)):
            for cc in range(c, min(ncol, c + cs)):
                owner[rr][cc] = i

    bnode, snode = S.find(node, "border"), S.find(node, "separators")
    bw = parse_stroke(bnode).width if bnode is not None else None
    sw = parse_stroke(snode).width if snode is not None else None
    bw, sw = (bw if bw and bw > 0 else 0.1), (sw if sw and sw > 0 else 0.1)

    def hline(r, w):           # boundary above row r
        for c in range(ncol):
            if owner[r - 1][c] is None or owner[r - 1][c] != owner[r][c]:
                lines.append(((xs[c], ys[r]), (xs[c + 1], ys[r]), w))

    def vline(c, w):           # boundary left of column c
        for r in range(nrow):
            if owner[r][c - 1] is None or owner[r][c - 1] != owner[r][c]:
                lines.append(((xs[c], ys[r]), (xs[c], ys[r + 1]), w))

    if bnode is not None and S.flag(bnode, "external", True):
        X1, Y1, X2, Y2 = xs[0], ys[0], xs[-1], ys[-1]
        lines += [((X1, Y1), (X2, Y1), bw), ((X2, Y1), (X2, Y2), bw), ((X2, Y2), (X1, Y2), bw),
                  ((X1, Y2), (X1, Y1), bw)]
    header = bnode is not None and S.flag(bnode, "header", True) and nrow > 1
    if header:
        hline(1, bw)
    if snode is not None and S.flag(snode, "rows", True):
        for r in range(2 if header else 1, nrow):
            hline(r, sw)
    if snode is not None and S.flag(snode, "cols", True):
        for c in range(1, ncol):
            vline(c, sw)
    return cells, lines


def _parse_pad_shape_block(n, layer_shape: PadLayerShape, ctx, fp):
    for c in S.children(n):
        k = c[0]
        if k == "shape":
            layer_shape.shape = str(c[1])
        elif k == "size":
            layer_shape.size = (_num(c[1]), _num(c[2]))
        elif k == "offset":
            layer_shape.offset = (_num(c[1]), _num(c[2]))
        elif k == "roundrect_rratio":
            layer_shape.rr_ratio = _num(c[1])
        elif k == "chamfer_ratio":
            layer_shape.chamfer_ratio = _num(c[1])
        elif k == "chamfer":
            layer_shape.chamfer = set(str(a) for a in c[1:] if not isinstance(a, list))
        elif k == "rect_delta":
            layer_shape.delta = (_num(c[1]), _num(c[2]))
        elif k == "options":
            layer_shape.anchor = str(S.val(c, "anchor", "circle"))
        elif k == "primitives":
            for pr in S.children(c):
                sh = _parse_shape(pr, ctx, None)
                if sh:
                    layer_shape.primitives.append(sh)


def _parse_pad(node, ctx: _Ctx, fp: Footprint) -> Pad:
    number = str(node[1]) if len(node) > 1 else ""
    ptype = str(node[2]) if len(node) > 2 else "smd"
    pshape = str(node[3]) if len(node) > 3 else "rect"
    x, y, ang = S.at(node)
    a = S.find(node, "at")
    has_angle = a is not None and len(a) > 3
    if not ctx.legacy_fp:
        ang = ang + fp.angle
    elif not has_angle:
        ang = 0.0
    size = S.xy(S.find(node, "size"), (0.0, 0.0))
    lay = S.find(node, "layers")
    layers = expand_layers([str(v) for v in lay[1:]] if lay else [], ctx.layer_names)
    pad = Pad(number=number, type=ptype, shape=pshape, pos=_fp_abs(fp, (x, y)), angle=ang, size=size,
              layers=layers, local_pos=(x, y), net=ctx.net(node), uuid=_uuid(node))
    d = S.find(node, "drill")
    if d is not None:
        dr = Drill()
        vals = [v for v in d[1:] if not isinstance(v, list)]
        if vals and vals[0] == "oval":
            dr.shape = "oval"
            vals = vals[1:]
        if vals:
            dr.w = _num(vals[0])
            dr.h = _num(vals[1]) if len(vals) > 1 else dr.w
        off = S.find(d, "offset")
        if off is not None:
            dr.offset = (_num(off[1]), _num(off[2]))
            pad.offset = dr.offset
        pad.drill = dr if dr.w > 0 else None
    pad.rr_ratio = S.fval(node, "roundrect_rratio", 0.0)
    pad.chamfer_ratio = S.fval(node, "chamfer_ratio", 0.0)
    ch = S.find(node, "chamfer")
    if ch is not None:
        pad.chamfer = set(str(v) for v in ch[1:] if not isinstance(v, list))
    rd = S.find(node, "rect_delta")
    if rd is not None:
        pad.delta = (_num(rd[1]), _num(rd[2]))
    pad.pinfunction = str(S.val(node, "pinfunction", ""))
    pad.pintype = str(S.val(node, "pintype", ""))
    pad.mask_margin = _opt(node, "solder_mask_margin")
    pad.paste_margin = _opt(node, "solder_paste_margin")
    pad.paste_ratio = _opt(node, "solder_paste_margin_ratio")
    pad.clearance = _opt(node, "clearance")
    zc = S.val(node, "zone_connect")
    pad.zone_connect = int(zc) if zc is not None else None
    pad.thermal_width = _opt(node, "thermal_bridge_width") if S.find(node, "thermal_bridge_width") is not None else _opt(node, "thermal_width")
    pad.thermal_gap = _opt(node, "thermal_gap")
    pad.thermal_angle = _opt(node, "thermal_bridge_angle")
    pad.die_length = S.fval(node, "die_length", 0.0)
    pad.remove_unused_layers = S.flag(node, "remove_unused_layers")
    pad.keep_end_layers = S.flag(node, "keep_end_layers")
    pp = S.find(node, "property")
    if pp is not None and len(pp) > 1:
        pad.fab_property = str(pp[1])
    opts = S.find(node, "options")
    if opts is not None:
        pad.custom_options = str(S.val(opts, "clearance", ""))
        pad.anchor = str(S.val(opts, "anchor", "circle"))
    prims = S.find(node, "primitives")
    if prims is not None:
        for pr in S.children(prims):
            sh = _parse_shape(pr, ctx, None)
            if sh:
                pad.primitives.append(sh)
    ps = S.find(node, "padstack")
    if ps is not None:
        pad.padstack_mode = str(S.val(ps, "mode", "front_inner_back"))
        for ln in S.findall(ps, "layer"):
            lname = str(ln[1])
            pls = PadLayerShape(shape=pad.shape, size=pad.size)
            _parse_pad_shape_block(ln, pls, ctx, fp)
            pad.layer_shapes[lname] = pls
    t = S.find(node, "tenting")
    if t is not None:
        pad.tenting = _front_back(t)
    if S.find(node, "backdrill") is not None or S.find(node, "tertiary_drill") is not None:
        pad.backdrill = True
    return pad


def _front_back(node):
    front = back = None
    rest = node[1:]
    if rest and not isinstance(rest[0], list):
        for v in rest:
            if v == "front":
                front = True
            elif v == "back":
                back = True
            elif v == "none":
                front = back = False
        if front is None and back is not None:
            front = False
        if back is None and front is not None:
            back = False
        return front, back
    f = S.find(node, "front")
    b = S.find(node, "back")
    if f is not None:
        front = str(f[1]).lower() in ("yes", "true") if len(f) > 1 else True
    if b is not None:
        back = str(b[1]).lower() in ("yes", "true") if len(b) > 1 else True
    return front, back


def _parse_model(node) -> Model3D:
    m = Model3D(path=str(node[1]) if len(node) > 1 else "")
    for c in S.children(node):
        k = c[0]
        if k in ("offset", "at", "scale", "rotate"):
            v = S.find(c, "xyz")
            if v is None:
                continue
            t = (_num(v[1]), _num(v[2]), _num(v[3]))
            if k == "at":
                m.offset = (t[0] * 25.4, t[1] * 25.4, t[2] * 25.4)
            elif k == "offset":
                m.offset = t
            elif k == "scale":
                m.scale = t
            else:
                m.rotate = t
        elif k == "hide":
            m.show = not (len(c) == 1 or str(c[1]).lower() in ("yes", "true"))
        elif k == "opacity":
            m.opacity = _num(c[1], 1.0)
    if S.has_atom(node, "hide"):
        m.show = False
    return m


def _parse_zone(node, ctx: _Ctx, fp: Footprint | None = None) -> Zone:
    net = ctx.net(node)
    if not net:
        net = str(S.val(node, "net_name", "") or "")
    lay = S.find(node, "layers")
    if lay is not None:
        layers = expand_layers([str(v) for v in lay[1:] if not isinstance(v, list)], ctx.layer_names)
    else:
        layers = [str(S.val(node, "layer", ""))]
    polys = []
    items = []
    for p in S.findall(node, "polygon"):
        polys.append(_poly_pts(p))
        items.append(S.pts(p))
    z = Zone(net=net, layers=layers, outline=polys[0] if polys else [], holes=polys[1:],
             outline_items=items[0] if items else [], hole_items=items[1:],
             name=str(S.val(node, "name", "")), uuid=_uuid(node), locked=S.flag(node, "locked"))
    z.priority = int(_num(S.val(node, "priority", 0)))
    cp = S.find(node, "connect_pads")
    if cp is not None:
        mode = [v for v in cp[1:] if not isinstance(v, list)]
        z.connect_pads = str(mode[0]) if mode else "thermal"
        z.clearance = S.fval(cp, "clearance", 0.0)
    z.min_thickness = S.fval(node, "min_thickness", 0.25)
    f = S.find(node, "fill")
    if f is not None:
        z.filled = len(f) > 1 and str(f[1]) == "yes" or S.has_atom(f, "yes")
        z.fill_mode = str(S.val(f, "mode", "solid"))
        if z.fill_mode == "segment":
            z.fill_mode = "solid"
        z.thermal_gap = S.fval(f, "thermal_gap", 0.5)
        z.thermal_bridge_width = S.fval(f, "thermal_bridge_width", 0.5)
        z.smoothing = str(S.val(f, "smoothing", ""))
        z.smoothing_radius = S.fval(f, "radius", 0.0)
        z.island_removal = int(_num(S.val(f, "island_removal_mode", 0)))
        z.island_area_min = S.fval(f, "island_area_min", 0.0)
        z.hatch_thickness = S.fval(f, "hatch_thickness", 0.0)
        z.hatch_gap = S.fval(f, "hatch_gap", 0.0)
        z.hatch_orientation = S.fval(f, "hatch_orientation", 0.0)
    ko = S.find(node, "keepout")
    if ko is not None:
        z.keepout = {}
        for c in S.children(ko):
            z.keepout[c[0]] = str(c[1]) == "allowed" if len(c) > 1 else True
    attr = S.find(node, "attr")
    if attr is not None and S.find(attr, "teardrop") is not None:
        z.is_teardrop = True
    for fpoly in S.findall(node, "filled_polygon"):
        lyr = str(S.val(fpoly, "layer", layers[0] if layers else ""))
        z.filled_polys.setdefault(lyr, []).append(_poly_pts(fpoly))
    if fp is not None:
        z.in_footprint = fp.ref
        if ctx.legacy_fp:
            pass  # footprint zones are stored in board coordinates in KiCad <= 10
        else:
            z.outline = [_fp_abs(fp, p) for p in z.outline]
            z.holes = [[_fp_abs(fp, p) for p in h] for h in z.holes]
            tr = lambda it: ("xy", *_fp_abs(fp, (it[1], it[2]))) if it[0] == "xy" else                 ("arc", _fp_abs(fp, it[1]), _fp_abs(fp, it[2]), _fp_abs(fp, it[3]))
            z.outline_items = [tr(it) for it in z.outline_items]
            z.hole_items = [[tr(it) for it in h] for h in z.hole_items]
    return z


def _poly_pts(node) -> list:
    out = []
    for item in S.pts(node):
        if item[0] == "xy":
            out.append((item[1], item[2]))
        else:
            # approximate arc segments by start/mid/end; converter refines
            out.extend([item[1], item[2], item[3]])
    return out


def _parse_footprint(node, ctx: _Ctx) -> Footprint:
    lib_id = str(node[1]) if len(node) > 1 and not isinstance(node[1], list) else ""
    x, y, ang = S.at(node)
    fp = Footprint(lib_id=lib_id, ref="", value="", pos=(x, y), angle=ang,
                   layer=str(S.val(node, "layer", "F.Cu")), uuid=_uuid(node),
                   locked=S.flag(node, "locked") or S.has_atom(node, "locked"))
    fp.path = str(S.val(node, "path", ""))
    fp.sheetname = str(S.val(node, "sheetname", ""))
    fp.sheetfile = str(S.val(node, "sheetfile", ""))
    fp.descr = str(S.val(node, "descr", ""))
    fp.tags = str(S.val(node, "tags", ""))
    fp.clearance = _opt(node, "clearance")
    fp.mask_margin = _opt(node, "solder_mask_margin")
    fp.paste_margin = _opt(node, "solder_paste_margin")
    fp.paste_ratio = _opt(node, "solder_paste_margin_ratio") if S.find(node, "solder_paste_margin_ratio") else _opt(node, "solder_paste_ratio")
    zc = S.val(node, "zone_connect")
    fp.zone_connect = int(zc) if zc is not None else None
    fp.thermal_width = _opt(node, "thermal_width")
    fp.thermal_gap = _opt(node, "thermal_gap")
    attr = S.find(node, "attr")
    if attr is not None:
        fp.attrs = set(str(v) for v in attr[1:] if not isinstance(v, list))
    for k in ("dnp", "board_only", "exclude_from_bom", "exclude_from_pos_files", "allow_missing_courtyard"):
        if S.find(node, k) is not None and S.flag(node, k):
            fp.attrs.add(k)
    fp.dnp = "dnp" in fp.attrs
    ntg = S.find(node, "net_tie_pad_groups")
    if ntg is not None:
        fp.net_tie_groups = [str(v) for v in ntg[1:] if not isinstance(v, list)]
    ef = S.find(node, "embedded_files")
    if ef is not None:
        from .embedded import parse_embedded_files
        fp.embedded = parse_embedded_files(ef)
    for c in S.children(node):
        k = c[0]
        if k == "property":
            tx = _parse_text(c, ctx, fp)
            if tx:
                fp.properties[tx.name] = tx.text
                if tx.kind == "reference":
                    fp.ref = tx.text
                elif tx.kind == "value":
                    fp.value = tx.text
                fp.texts.append(tx)
        elif k == "fp_text":
            tx = _parse_text(c, ctx, fp)
            if tx:
                if tx.kind == "reference":
                    fp.ref = tx.text
                elif tx.kind == "value":
                    fp.value = tx.text
                fp.texts.append(tx)
        elif k in ("fp_text_box",):
            tb = _parse_textbox(c, ctx, fp)
            if tb:
                fp.texts.append(tb)
        elif k in ("fp_line", "fp_arc", "fp_circle", "fp_rect", "fp_poly", "fp_curve", "fp_ellipse"):
            sh = _parse_shape(c, ctx, fp)
            if sh:
                fp.shapes.append(sh)
        elif k == "pad":
            fp.pads.append(_parse_pad(c, ctx, fp))
        elif k == "zone":
            fp.zones.append(_parse_zone(c, ctx, fp))
        elif k == "model":
            fp.models.append(_parse_model(c))
        elif k in ("dimension", "table", "barcode", "image", "group", "point"):
            fp.raw_unsupported.append(c)
    return fp


def _parse_stackup(node, board: Board):
    for c in S.children(node):
        if c[0] == "layer":
            sl = StackupLayer(name=str(c[1]), type=str(S.val(c, "type", "")))
            sl.thickness = S.fval(c, "thickness", 0.0)
            sl.material = str(S.val(c, "material", ""))
            sl.epsilon_r = S.fval(c, "epsilon_r", 0.0)
            sl.loss_tangent = S.fval(c, "loss_tangent", 0.0)
            sl.color = str(S.val(c, "color", ""))
            # sublayers: repeated thickness/material/epsilon_r after (addsublayer)
            seen_sub = False
            cur = None
            for cc in c[2:]:
                if not isinstance(cc, list):
                    continue
                if cc[0] == "addsublayer":
                    seen_sub = True
                    cur = {"thickness": 0.0, "material": "", "epsilon_r": 0.0, "loss_tangent": 0.0}
                    sl.sublayers.append(cur)
                elif seen_sub and cur is not None and cc[0] in cur:
                    cur[cc[0]] = _num(cc[1]) if cc[0] != "material" else str(cc[1])
            board.stackup.append(sl)
        else:
            board.stackup_info[c[0]] = str(c[1]) if len(c) > 1 else True


def _parse_setup(node, board: Board):
    st = board.setup
    for c in S.children(node):
        k = c[0]
        if k == "stackup":
            _parse_stackup(c, board)
        elif k in ("pad_to_mask_clearance", "solder_mask_min_width", "pad_to_paste_clearance",
                   "pad_to_paste_clearance_ratio"):
            st[k] = _num(c[1])
        elif k in ("aux_axis_origin", "grid_origin"):
            st[k] = (_num(c[1]), _num(c[2]))
        elif k == "allow_soldermask_bridges_in_footprints":
            st[k] = str(c[1]) == "yes"
        elif k == "tenting":
            st["tenting"] = _front_back(c)
        elif k in ("covering", "plugging", "capping", "filling"):
            st[k] = _front_back(c)
        elif k == "pcbplotparams":
            pass
        else:
            st[k] = c[1:] if len(c) > 1 else True


def _parse_netclass_legacy(node) -> NetClass:
    nc = NetClass(name=str(node[1]), description=str(node[2]) if len(node) > 2 and not isinstance(node[2], list) else "")
    nc.clearance = _opt(node, "clearance")
    nc.track_width = _opt(node, "trace_width")
    nc.via_dia = _opt(node, "via_dia")
    nc.via_drill = _opt(node, "via_drill")
    nc.uvia_dia = _opt(node, "uvia_dia")
    nc.uvia_drill = _opt(node, "uvia_drill")
    nc.dp_width = _opt(node, "diff_pair_width")
    nc.dp_gap = _opt(node, "diff_pair_gap")
    nc.nets = [str(n[1]) for n in S.findall(node, "add_net")]
    return nc


def parse_board(path, issues: Issues | None = None) -> Board:
    path = Path(path)
    issues = issues or Issues()
    root = S.parse_file(path)
    if not root or root[0] != "kicad_pcb":
        raise ValueError(f"{path.name}: not a KiCad PCB file")
    b = Board(path=path)
    ctx = _Ctx(b, issues)
    b.version = int(_num(S.val(root, "version", 0)))
    ctx.legacy_fp = b.version < FP_AFFINE_VERSION
    b.generator = str(S.val(root, "generator", ""))
    b.generator_version = str(S.val(root, "generator_version", ""))
    if b.version and b.version < 20211014:
        issues.error("pcb", f"{path.name}: board file version {b.version} is older than KiCad 6. "
                            "Open and save it with KiCad 10 first.")
    gen = S.find(root, "general")
    if gen is not None:
        b.thickness = S.fval(gen, "thickness", 1.6)
    p = S.find(root, "paper")
    if p is not None and len(p) > 1:
        b.paper = str(p[1])
    tb = S.find(root, "title_block")
    if tb is not None:
        for c in S.children(tb):
            if c[0] == "comment":
                b.title_block[f"comment{c[1]}"] = str(c[2]) if len(c) > 2 else ""
            else:
                b.title_block[c[0]] = str(c[1]) if len(c) > 1 else ""
    lay = S.find(root, "layers")
    if lay is not None:
        for c in S.children(lay):
            if len(c) >= 2:
                user = ""
                rest = [v for v in c[3:] if not isinstance(v, list)] if len(c) > 3 else []
                for v in rest:
                    if isinstance(v, S.QStr):
                        user = str(v)
                b.layers.append(Layer(ordinal=int(_num(c[0], -1)), name=str(c[1]),
                                      type=str(c[2]) if len(c) > 2 else "user", user_name=user))
    ctx.layer_names = [l.name for l in b.layers]
    for c in S.children(root):
        k = c[0]
        try:
            if k == "setup":
                _parse_setup(c, b)
            elif k == "net":
                if len(c) >= 3:
                    nm = unescape_name(str(c[2]))
                    ctx.net_by_code[str(c[1])] = str(c[2])
                    if nm not in b.nets:
                        b.nets.append(nm)
                elif len(c) == 2:
                    nm = unescape_name(str(c[1]))
                    if nm not in b.nets:
                        b.nets.append(nm)
            elif k == "net_class":
                b.netclasses_legacy.append(_parse_netclass_legacy(c))
            elif k in ("footprint", "module"):
                b.footprints.append(_parse_footprint(c, ctx))
            elif k == "segment":
                b.tracks.append(Track(start=S.xy(S.find(c, "start")), end=S.xy(S.find(c, "end")),
                                      width=S.fval(c, "width", 0.2), layer=str(S.val(c, "layer", "")),
                                      net=ctx.net(c), locked=S.flag(c, "locked"), uuid=_uuid(c)))
            elif k == "arc":
                b.arcs.append(Track(start=S.xy(S.find(c, "start")), end=S.xy(S.find(c, "end")),
                                    mid=S.xy(S.find(c, "mid")), width=S.fval(c, "width", 0.2),
                                    layer=str(S.val(c, "layer", "")), net=ctx.net(c),
                                    locked=S.flag(c, "locked"), uuid=_uuid(c)))
            elif k == "via":
                b.vias.append(_parse_via(c, ctx))
            elif k == "zone":
                b.zones.append(_parse_zone(c, ctx))
            elif k in ("gr_line", "gr_arc", "gr_circle", "gr_rect", "gr_poly", "gr_curve", "gr_ellipse"):
                sh = _parse_shape(c, ctx)
                if sh:
                    b.shapes.append(sh)
            elif k == "gr_text":
                tx = _parse_text(c, ctx)
                if tx:
                    b.texts.append(tx)
            elif k == "gr_text_box":
                tb2 = _parse_textbox(c, ctx)
                if tb2:
                    b.texts.append(tb2)
            elif k == "dimension":
                b.dimensions.append(c)
            elif k == "table":
                b.tables.append(c)
            elif k == "barcode":
                b.barcodes.append(c)
            elif k == "image":
                b.images.append(c)
            elif k == "group":
                b.groups.append(c)
            elif k == "embedded_files":
                from .embedded import parse_embedded_files
                b.embedded = parse_embedded_files(c)
            elif k == "property":
                b.properties[str(c[1])] = str(c[2]) if len(c) > 2 else ""
            elif k == "variants":
                b.variants.append(c)
            elif k in ("version", "generator", "generator_version", "general", "paper", "title_block",
                       "layers", "generated", "target", "point", "embedded_fonts", "host", "page"):
                if k in ("target",):
                    b.unsupported[k] = b.unsupported.get(k, 0) + 1
            else:
                b.unsupported[k] = b.unsupported.get(k, 0) + 1
        except Exception as e:  # keep going, report
            issues.error("pcb", f"Failed to read '{k}' item: {e}")
    if "" not in b.nets:
        b.nets.insert(0, "")
    # nets referenced by items but missing from the net table (KiCad 10 has no table)
    seen = set(b.nets)
    for coll in (b.tracks, b.arcs, b.vias, b.zones):
        for it in coll:
            if it.net and it.net not in seen:
                seen.add(it.net)
                b.nets.append(it.net)
    for fp in b.footprints:
        for pd in fp.pads:
            if pd.net and pd.net not in seen:
                seen.add(pd.net)
                b.nets.append(pd.net)
    return b


def _parse_via(c, ctx: _Ctx) -> Via:
    vt = "through"
    for v in c[1:]:
        if not isinstance(v, list) and v in ("blind", "buried", "micro"):
            vt = str(v)
    lay = S.find(c, "layers")
    layers = tuple(str(v) for v in lay[1:3]) if lay is not None and len(lay) >= 3 else ("F.Cu", "B.Cu")
    via = Via(pos=S.xy(S.find(c, "at")), size=S.fval(c, "size", 0.6), drill=S.fval(c, "drill", 0.3),
              layers=layers, net=ctx.net(c), via_type=vt, uuid=_uuid(c),
              remove_unused_layers=S.flag(c, "remove_unused_layers"),
              keep_end_layers=S.flag(c, "keep_end_layers"),
              locked=S.flag(c, "locked") or S.has_atom(c, "locked"), free=S.flag(c, "free"))
    t = S.find(c, "tenting")
    if t is not None:
        via.tenting = _front_back(t)
    zl = S.find(c, "zone_layer_connections")
    if zl is not None:
        via.zone_layers = tuple(str(v) for v in zl[1:] if not isinstance(v, list))
    ps = S.find(c, "padstack")
    if ps is not None:
        for ln in S.findall(ps, "layer"):
            sz = S.find(ln, "size")
            if sz is not None:
                via.layer_sizes[str(ln[1])] = _num(sz[1])
    if S.find(c, "backdrill") is not None or S.find(c, "tertiary_drill") is not None:
        via.backdrill = True
    return via
