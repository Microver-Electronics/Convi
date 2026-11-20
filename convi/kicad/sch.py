"""KiCad .kicad_sch reader (KiCad 6 .. 10).

Schematic coordinates: millimetres, Y down (sheet frame).
Library symbol coordinates are kept as written in the file (Y up).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import sexpr as S
from .common import Issues, Stroke, TextEffects, parse_color, parse_effects, parse_stroke

PAPER_SIZES_MM = {
    "A5": (210, 148), "A4": (297, 210), "A3": (420, 297), "A2": (594, 420), "A1": (841, 594), "A0": (1189, 841),
    "A": (279.4, 215.9), "B": (431.8, 279.4), "C": (558.8, 431.8), "D": (863.6, 558.8), "E": (1117.6, 863.6),
    "USLetter": (279.4, 215.9), "USLegal": (355.6, 215.9), "USLedger": (431.8, 279.4),
}


# ----------------------------------------------------------------- library symbols

@dataclass
class LibShape:
    kind: str                 # polyline / rectangle / circle / arc / bezier / text / textbox
    unit: int
    style: int                # body style (0 = common)
    pts: list = field(default_factory=list)   # lib coords (Y up)
    width: float = 0.0
    fill: str = "none"        # none / outline / background / color / hatch...
    fill_color: tuple | None = None
    stroke: Stroke | None = None
    text: str = ""
    angle: float = 0.0
    effects: TextEffects | None = None
    radius: float = 0.0


@dataclass
class LibPin:
    unit: int
    style: int
    etype: str                # input / output / bidirectional / tri_state / passive / free / unspecified / power_in ...
    shape: str                # line / inverted / clock / ...
    pos: tuple                # connection point (lib coords, Y up)
    angle: float              # 0 = pin extends to the right of the connection point (KiCad)
    length: float
    name: str
    number: str
    hidden: bool = False
    name_effects: TextEffects | None = None
    number_effects: TextEffects | None = None
    alternates: list = field(default_factory=list)


@dataclass
class LibField:
    name: str
    value: str
    pos: tuple = (0.0, 0.0)
    angle: float = 0.0
    effects: TextEffects | None = None
    hidden: bool = False


@dataclass
class LibSymbol:
    lib_id: str
    extends: str = ""
    power: str = ""                # "" / "global" / "local"
    pin_names_offset: float = 0.508
    pin_names_hidden: bool = False
    pin_numbers_hidden: bool = False
    in_bom: bool = True
    on_board: bool = True
    exclude_from_sim: bool = False
    fields: dict = field(default_factory=dict)       # name -> LibField
    shapes: list = field(default_factory=list)
    pins: list = field(default_factory=list)
    unit_names: dict = field(default_factory=dict)
    unit_count: int = 1
    has_demorgan: bool = False
    body_style_names: list = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.lib_id.split(":", 1)[1] if ":" in self.lib_id else self.lib_id

    def field_value(self, name, default=""):
        f = self.fields.get(name)
        return f.value if f else default


# ----------------------------------------------------------------- schematic items

@dataclass
class Field:
    name: str
    value: str
    pos: tuple
    angle: float
    effects: TextEffects
    hidden: bool = False
    show_name: bool = False


@dataclass
class SymbolInstance:
    lib_id: str
    lib_name: str             # cache key if different (lib_name token)
    pos: tuple
    angle: float
    mirror: str               # "" / "x" / "y"
    unit: int
    body_style: int
    uuid: str
    fields: list = field(default_factory=list)   # Field
    in_bom: bool = True
    on_board: bool = True
    dnp: bool = False
    exclude_from_sim: bool = False
    instances: list = field(default_factory=list)  # (path, reference, unit)
    pin_alternates: dict = field(default_factory=dict)

    def field(self, name, default=""):
        for f in self.fields:
            if f.name == name:
                return f.value
        return default

    def field_obj(self, name):
        for f in self.fields:
            if f.name == name:
                return f
        return None

    @property
    def transform(self) -> tuple:
        """KiCad TRANSFORM (x1, y1, x2, y2) of this symbol (see eeschema/sch_symbol.cpp)."""
        a = int(round(self.angle)) % 360
        t = {0: (1, 0, 0, 1), 90: (0, 1, -1, 0), 180: (-1, 0, 0, -1), 270: (0, -1, 1, 0)}.get(a, (1, 0, 0, 1))
        if self.mirror == "x":
            t = compose(t, (1, 0, 0, -1))
        elif self.mirror == "y":
            t = compose(t, (-1, 0, 0, 1))
        return t

    def to_sheet(self, lib_pt) -> tuple:
        """Library point (Y up) -> sheet coordinates (Y down)."""
        x1, y1, x2, y2 = self.transform
        px, py = lib_pt[0], -lib_pt[1]
        return self.pos[0] + x1 * px + y1 * py, self.pos[1] + x2 * px + y2 * py


def compose(m, t):
    x1 = m[0] * t[0] + m[2] * t[1]
    y1 = m[1] * t[0] + m[3] * t[1]
    x2 = m[0] * t[2] + m[2] * t[3]
    y2 = m[1] * t[2] + m[3] * t[3]
    return x1, y1, x2, y2


@dataclass
class Wire:
    kind: str           # wire / bus / line (graphic)
    pts: list
    stroke: Stroke
    uuid: str = ""


@dataclass
class Label:
    kind: str           # label / global_label / hierarchical_label / directive_label
    text: str
    pos: tuple
    angle: float
    effects: TextEffects
    shape: str = ""     # input/output/bidirectional/tri_state/passive (global/hier), dot/round/diamond/rectangle (directive)
    fields: list = field(default_factory=list)
    uuid: str = ""
    length: float = 0.0


@dataclass
class SheetPin:
    name: str
    etype: str
    pos: tuple
    angle: float
    effects: TextEffects
    uuid: str = ""


@dataclass
class Sheet:
    pos: tuple
    size: tuple
    name: str
    file: str
    uuid: str
    pins: list = field(default_factory=list)
    fields: list = field(default_factory=list)
    stroke: Stroke | None = None
    fill_color: tuple | None = None
    instances: list = field(default_factory=list)   # (path, page)
    dnp: bool = False
    exclude_from_bom: bool = False


@dataclass
class Graphic:
    kind: str           # polyline / rectangle / circle / arc / bezier / text / textbox / image / table
    pts: list = field(default_factory=list)
    stroke: Stroke | None = None
    fill: str = "none"
    fill_color: tuple | None = None
    text: str = ""
    angle: float = 0.0
    effects: TextEffects | None = None
    radius: float = 0.0
    data: str = ""
    scale: float = 1.0
    raw: list | None = None


@dataclass
class Schematic:
    path: Path
    version: int = 0
    uuid: str = ""
    paper: str = "A4"
    paper_size: tuple = (297.0, 210.0)
    portrait: bool = False
    title_block: dict = field(default_factory=dict)
    lib_symbols: dict = field(default_factory=dict)    # key -> LibSymbol
    symbols: list = field(default_factory=list)
    wires: list = field(default_factory=list)
    buses: list = field(default_factory=list)
    bus_entries: list = field(default_factory=list)    # (pos, size)
    junctions: list = field(default_factory=list)      # (pos, diameter)
    no_connects: list = field(default_factory=list)
    labels: list = field(default_factory=list)
    sheets: list = field(default_factory=list)
    graphics: list = field(default_factory=list)
    bus_aliases: dict = field(default_factory=dict)
    sheet_instances: list = field(default_factory=list)  # root: (path, page)
    legacy_symbol_instances: list = field(default_factory=list)
    embedded: dict = field(default_factory=dict)
    unsupported: dict = field(default_factory=dict)


# ----------------------------------------------------------------- parsing

def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _fill(node):
    f = S.find(node, "fill")
    if f is None:
        return "none", None
    t = str(S.val(f, "type", "none"))
    return t, parse_color(S.find(f, "color"))


def _hidden(node, effects: TextEffects | None):
    return S.flag(node, "hide") or (effects.hidden if effects else False)


def _parse_lib_shape(node, unit, style) -> LibShape | None:
    k = node[0]
    st = parse_stroke(node)
    fill, fc = _fill(node)
    sh = LibShape(kind=k, unit=unit, style=style, width=st.width or 0.0, fill=fill, fill_color=fc, stroke=st)
    if k == "polyline":
        sh.pts = [(p[1], p[2]) for p in S.pts(node) if p[0] == "xy"]
    elif k == "rectangle":
        sh.pts = [S.xy(S.find(node, "start")), S.xy(S.find(node, "end"))]
        sh.radius = S.fval(node, "radius", 0.0)
    elif k == "circle":
        sh.pts = [S.xy(S.find(node, "center"))]
        sh.radius = S.fval(node, "radius", 0.0)
    elif k == "arc":
        if S.find(node, "mid") is not None:
            sh.pts = [S.xy(S.find(node, "start")), S.xy(S.find(node, "mid")), S.xy(S.find(node, "end"))]
        else:
            # legacy: (radius (at) (length) (angles))
            return None
    elif k == "bezier":
        sh.pts = [(p[1], p[2]) for p in S.pts(node) if p[0] == "xy"]
    elif k == "text":
        sh.text = str(node[1]) if len(node) > 1 else ""
        x, y, a = S.at(node)
        sh.pts = [(x, y)]
        sh.angle = a / 10 if a > 360 else a
        sh.effects = parse_effects(S.find(node, "effects"))
        if sh.effects.hidden:
            return None
    elif k == "text_box":
        sh.kind = "textbox"
        sh.text = str(node[1]) if len(node) > 1 else ""
        if S.find(node, "start") is not None:
            sh.pts = [S.xy(S.find(node, "start")), S.xy(S.find(node, "end"))]
        else:
            x, y, a = S.at(node)
            sz = S.xy(S.find(node, "size"))
            sh.pts = [(x, y), (x + sz[0], y - sz[1])]
        sh.effects = parse_effects(S.find(node, "effects"))
    elif k == "ellipse":
        return None
    else:
        return None
    return sh


def _parse_pin(node, unit, style) -> LibPin:
    etype = str(node[1]) if len(node) > 1 else "passive"
    shape = str(node[2]) if len(node) > 2 and not isinstance(node[2], list) else "line"
    x, y, a = S.at(node)
    nm = S.find(node, "name")
    num = S.find(node, "number")
    p = LibPin(unit=unit, style=style, etype=etype, shape=shape, pos=(x, y), angle=a,
               length=S.fval(node, "length", 2.54),
               name=str(nm[1]) if nm is not None and len(nm) > 1 else "",
               number=str(num[1]) if num is not None and len(num) > 1 else "",
               hidden=S.flag(node, "hide"))
    if nm is not None:
        p.name_effects = parse_effects(S.find(nm, "effects"))
    if num is not None:
        p.number_effects = parse_effects(S.find(num, "effects"))
    for alt in S.findall(node, "alternate"):
        p.alternates.append((str(alt[1]), str(alt[2]) if len(alt) > 2 else "", str(alt[3]) if len(alt) > 3 else ""))
    return p


def _unit_style_from_name(name: str, base: str) -> tuple[int, int]:
    """Sub-symbol names look like '<name>_<unit>_<style>'."""
    rest = name[len(base):] if name.startswith(base) else name
    parts = rest.rsplit("_", 2)
    try:
        return int(parts[-2]), int(parts[-1])
    except (ValueError, IndexError):
        return 0, 0


def parse_lib_symbol(node, issues: Issues | None = None) -> LibSymbol:
    lib_id = str(node[1])
    ls = LibSymbol(lib_id=lib_id)
    ls.extends = str(S.val(node, "extends", "") or "")
    pw = S.find(node, "power")
    if pw is not None:
        ls.power = str(pw[1]) if len(pw) > 1 else "global"
    pn = S.find(node, "pin_names")
    if pn is not None:
        ls.pin_names_offset = S.fval(pn, "offset", 0.508)
        ls.pin_names_hidden = S.flag(pn, "hide")
    pnum = S.find(node, "pin_numbers")
    if pnum is not None:
        ls.pin_numbers_hidden = S.flag(pnum, "hide")
    ls.in_bom = S.flag(node, "in_bom", True)
    ls.on_board = S.flag(node, "on_board", True)
    ls.exclude_from_sim = S.flag(node, "exclude_from_sim", False)
    for pr in S.findall(node, "property"):
        name = str(pr[1])
        val = str(pr[2]) if len(pr) > 2 else ""
        x, y, a = S.at(pr)
        eff = parse_effects(S.find(pr, "effects"))
        ls.fields[name] = LibField(name, val, (x, y), a, eff, _hidden(pr, eff))
    base = ls.name
    max_unit = 1
    styles = set()
    for sub in S.findall(node, "symbol"):
        sname = str(sub[1])
        unit, style = _unit_style_from_name(sname, base)
        max_unit = max(max_unit, unit)
        if style:
            styles.add(style)
        un = S.val(sub, "unit_name")
        if un:
            ls.unit_names[unit] = str(un)
        for c in S.children(sub):
            if c[0] == "pin":
                ls.pins.append(_parse_pin(c, unit, style))
            elif c[0] in ("polyline", "rectangle", "circle", "arc", "bezier", "text", "text_box", "ellipse"):
                sh = _parse_lib_shape(c, unit, style)
                if sh:
                    ls.shapes.append(sh)
    # top-level items (rare) belong to all units
    for c in S.children(node):
        if c[0] == "pin":
            ls.pins.append(_parse_pin(c, 0, 0))
        elif c[0] in ("polyline", "rectangle", "circle", "arc", "bezier", "text"):
            sh = _parse_lib_shape(c, 0, 0)
            if sh:
                ls.shapes.append(sh)
    ls.unit_count = max_unit
    ls.has_demorgan = 2 in styles
    bs = S.find(node, "body_styles")
    if bs is not None:
        ls.body_style_names = [str(v) for v in bs[1:] if not isinstance(v, list)]
        if len(ls.body_style_names) > 1:
            ls.has_demorgan = True
    return ls


def flatten(ls: LibSymbol, cache: dict) -> LibSymbol:
    """Resolve 'extends' (derived symbols) using the parent from the cache."""
    if not ls.extends:
        return ls
    parent = None
    nick = ls.lib_id.split(":", 1)[0] + ":" if ":" in ls.lib_id else ""
    for key in (nick + ls.extends, ls.extends):
        if key in cache:
            parent = flatten(cache[key], cache)
            break
    if parent is None:
        return ls
    out = LibSymbol(lib_id=ls.lib_id, power=parent.power or ls.power,
                    pin_names_offset=parent.pin_names_offset, pin_names_hidden=parent.pin_names_hidden,
                    pin_numbers_hidden=parent.pin_numbers_hidden, in_bom=ls.in_bom, on_board=ls.on_board,
                    exclude_from_sim=ls.exclude_from_sim)
    out.fields = dict(parent.fields)
    out.fields.update(ls.fields)
    out.shapes = list(parent.shapes) + list(ls.shapes)
    out.pins = list(parent.pins) + list(ls.pins)
    out.unit_names = dict(parent.unit_names)
    out.unit_count = max(parent.unit_count, ls.unit_count)
    out.has_demorgan = parent.has_demorgan or ls.has_demorgan
    return out


def _parse_field(node) -> Field:
    name = str(node[1]) if len(node) > 1 else ""
    val = str(node[2]) if len(node) > 2 and not isinstance(node[2], list) else ""
    x, y, a = S.at(node)
    eff = parse_effects(S.find(node, "effects"))
    return Field(name=name, value=val, pos=(x, y), angle=a, effects=eff, hidden=_hidden(node, eff),
                 show_name=S.flag(node, "show_name"))


def _parse_symbol(node) -> SymbolInstance:
    lib_id = str(S.val(node, "lib_id", ""))
    lib_name = str(S.val(node, "lib_name", "") or "")
    x, y, a = S.at(node)
    m = S.find(node, "mirror")
    sym = SymbolInstance(lib_id=lib_id, lib_name=lib_name, pos=(x, y), angle=a,
                         mirror=str(m[1]) if m is not None and len(m) > 1 else "",
                         unit=int(_f(S.val(node, "unit", 1), 1)),
                         body_style=int(_f(S.val(node, "body_style", S.val(node, "convert", 1)), 1)),
                         uuid=str(S.val(node, "uuid", "")),
                         in_bom=S.flag(node, "in_bom", True), on_board=S.flag(node, "on_board", True),
                         dnp=S.flag(node, "dnp", False), exclude_from_sim=S.flag(node, "exclude_from_sim", False))
    for pr in S.findall(node, "property"):
        sym.fields.append(_parse_field(pr))
    for pn in S.findall(node, "pin"):
        alt = S.val(pn, "alternate")
        if alt:
            sym.pin_alternates[str(pn[1])] = str(alt)
    inst = S.find(node, "instances")
    if inst is not None:
        for proj in S.findall(inst, "project"):
            for p in S.findall(proj, "path"):
                sym.instances.append((str(p[1]), str(S.val(p, "reference", "")), int(_f(S.val(p, "unit", sym.unit), sym.unit))))
    return sym


def _parse_label(node) -> Label:
    k = node[0]
    text = str(node[1]) if len(node) > 1 and not isinstance(node[1], list) else ""
    x, y, a = S.at(node)
    lb = Label(kind="directive_label" if k == "netclass_flag" else k, text=text, pos=(x, y), angle=a,
               effects=parse_effects(S.find(node, "effects")), shape=str(S.val(node, "shape", "")),
               uuid=str(S.val(node, "uuid", "")), length=S.fval(node, "length", 0.0))
    for pr in S.findall(node, "property"):
        lb.fields.append(_parse_field(pr))
    return lb


def _parse_sheet(node) -> Sheet:
    x, y, _ = S.at(node)
    sz = S.xy(S.find(node, "size"))
    name = ""
    file = ""
    fields = []
    for pr in S.findall(node, "property"):
        f = _parse_field(pr)
        fields.append(f)
        if f.name in ("Sheetname", "Sheet name"):
            name = f.value
        elif f.name in ("Sheetfile", "Sheet file"):
            file = f.value
    fill, fc = _fill(node)
    sh = Sheet(pos=(x, y), size=sz, name=name, file=file, uuid=str(S.val(node, "uuid", "")), fields=fields,
               stroke=parse_stroke(node), fill_color=fc, dnp=S.flag(node, "dnp"),
               exclude_from_bom=not S.flag(node, "in_bom", True))
    for p in S.findall(node, "pin"):
        px, py, pa = S.at(p)
        sh.pins.append(SheetPin(name=str(p[1]), etype=str(p[2]) if len(p) > 2 and not isinstance(p[2], list) else "passive",
                                pos=(px, py), angle=pa, effects=parse_effects(S.find(p, "effects")),
                                uuid=str(S.val(p, "uuid", ""))))
    inst = S.find(node, "instances")
    if inst is not None:
        for proj in S.findall(inst, "project"):
            for p in S.findall(proj, "path"):
                sh.instances.append((str(p[1]), str(S.val(p, "page", ""))))
    return sh


def _parse_graphic(node) -> Graphic | None:
    k = node[0]
    st = parse_stroke(node)
    fill, fc = _fill(node)
    g = Graphic(kind=k, stroke=st, fill=fill, fill_color=fc)
    if k == "polyline":
        g.pts = [(p[1], p[2]) for p in S.pts(node) if p[0] == "xy"]
    elif k == "rectangle":
        g.pts = [S.xy(S.find(node, "start")), S.xy(S.find(node, "end"))]
        g.radius = S.fval(node, "radius", 0.0)
    elif k == "circle":
        g.pts = [S.xy(S.find(node, "center"))]
        g.radius = S.fval(node, "radius", 0.0)
    elif k == "arc":
        g.pts = [S.xy(S.find(node, "start")), S.xy(S.find(node, "mid")), S.xy(S.find(node, "end"))]
    elif k == "bezier":
        g.pts = [(p[1], p[2]) for p in S.pts(node) if p[0] == "xy"]
    elif k == "text":
        g.text = str(node[1]) if len(node) > 1 else ""
        x, y, a = S.at(node)
        g.pts = [(x, y)]
        g.angle = a
        g.effects = parse_effects(S.find(node, "effects"))
    elif k == "text_box":
        g.kind = "textbox"
        g.text = str(node[1]) if len(node) > 1 and not isinstance(node[1], list) else ""
        if S.find(node, "start") is not None:
            g.pts = [S.xy(S.find(node, "start")), S.xy(S.find(node, "end"))]
        else:
            x, y, a = S.at(node)
            sz = S.xy(S.find(node, "size"))
            g.pts = [(x, y), (x + sz[0], y + sz[1])]
            g.angle = a
        g.effects = parse_effects(S.find(node, "effects"))
    elif k == "image":
        x, y, _ = S.at(node)
        g.pts = [(x, y)]
        g.scale = S.fval(node, "scale", 1.0)
        d = S.find(node, "data")
        if d is not None:
            g.data = "".join(str(v) for v in d[1:] if not isinstance(v, list))
    elif k == "table":
        g.raw = node
    else:
        return None
    return g


def parse_schematic(path, issues: Issues | None = None) -> Schematic:
    path = Path(path)
    issues = issues or Issues()
    root = S.parse_file(path)
    if not root or root[0] != "kicad_sch":
        raise ValueError(f"{path.name}: not a KiCad schematic")
    sch = Schematic(path=path)
    sch.version = int(_f(S.val(root, "version", 0)))
    if sch.version and sch.version < 20211123:
        issues.error("sch", f"{path.name}: schematic version {sch.version} is older than KiCad 6. "
                            "Open and save it with KiCad 10 first.")
    sch.uuid = str(S.val(root, "uuid", ""))
    p = S.find(root, "paper")
    if p is not None and len(p) > 1:
        sch.paper = str(p[1])
        if sch.paper == "User" and len(p) >= 4:
            sch.paper_size = (_f(p[2]), _f(p[3]))
        else:
            sch.paper_size = PAPER_SIZES_MM.get(sch.paper, (297.0, 210.0))
        if "portrait" in p[1:]:
            sch.portrait = True
            sch.paper_size = (sch.paper_size[1], sch.paper_size[0])
    tb = S.find(root, "title_block")
    if tb is not None:
        for c in S.children(tb):
            if c[0] == "comment":
                sch.title_block[f"comment{c[1]}"] = str(c[2]) if len(c) > 2 else ""
            else:
                sch.title_block[c[0]] = str(c[1]) if len(c) > 1 else ""
    ls = S.find(root, "lib_symbols")
    if ls is not None:
        raw = {}
        for s in S.findall(ls, "symbol"):
            try:
                sym = parse_lib_symbol(s, issues)
                raw[sym.lib_id] = sym
            except Exception as e:
                issues.error("sch", f"{path.name}: cannot read library symbol {s[1]}: {e}")
        for k, v in raw.items():
            sch.lib_symbols[k] = flatten(v, raw)
    for c in S.children(root):
        k = c[0]
        try:
            if k == "symbol":
                sch.symbols.append(_parse_symbol(c))
            elif k == "wire":
                sch.wires.append(Wire("wire", [(p[1], p[2]) for p in S.pts(c) if p[0] == "xy"], parse_stroke(c),
                                      str(S.val(c, "uuid", ""))))
            elif k == "bus":
                sch.buses.append(Wire("bus", [(p[1], p[2]) for p in S.pts(c) if p[0] == "xy"], parse_stroke(c),
                                      str(S.val(c, "uuid", ""))))
            elif k == "bus_entry":
                x, y, _ = S.at(c)
                sch.bus_entries.append(((x, y), S.xy(S.find(c, "size"), (2.54, 2.54))))
            elif k == "junction":
                x, y, _ = S.at(c)
                sch.junctions.append(((x, y), S.fval(c, "diameter", 0.0)))
            elif k == "no_connect":
                x, y, _ = S.at(c)
                sch.no_connects.append((x, y))
            elif k in ("label", "global_label", "hierarchical_label", "directive_label", "netclass_flag"):
                sch.labels.append(_parse_label(c))
            elif k == "sheet":
                sch.sheets.append(_parse_sheet(c))
            elif k in ("polyline", "rectangle", "circle", "arc", "bezier", "text", "text_box", "image", "table"):
                g = _parse_graphic(c)
                if g is not None:
                    # KiCad uses polylines with 2 points as graphic lines
                    sch.graphics.append(g)
            elif k == "rule_area":
                # KiCad 9 rule area: a dashed outline whose net class flag (a directive label, read above)
                # assigns the nets inside; the assignment itself reaches the board through the project file.
                # The outline is kept as a drawing.
                for sub in c[1:]:
                    if isinstance(sub, list) and sub and sub[0] in ("polyline", "rectangle", "circle"):
                        g = _parse_graphic(sub)
                        if g is not None:
                            if g.kind == "polyline" and len(g.pts) > 2 and g.pts[0] != g.pts[-1]:
                                g.pts = list(g.pts) + [g.pts[0]]
                            sch.graphics.append(g)
            elif k == "bus_alias":
                mem = S.find(c, "members")
                sch.bus_aliases[str(c[1])] = [str(v) for v in (mem[1:] if mem else []) if not isinstance(v, list)]
            elif k == "sheet_instances":
                for pth in S.findall(c, "path"):
                    sch.sheet_instances.append((str(pth[1]), str(S.val(pth, "page", ""))))
            elif k == "symbol_instances":
                for pth in S.findall(c, "path"):
                    sch.legacy_symbol_instances.append((str(pth[1]), str(S.val(pth, "reference", "")),
                                                        int(_f(S.val(pth, "unit", 1), 1)),
                                                        str(S.val(pth, "value", "")), str(S.val(pth, "footprint", ""))))
            elif k == "embedded_files":
                from .embedded import parse_embedded_files
                sch.embedded = parse_embedded_files(c)
            elif k in ("version", "generator", "generator_version", "uuid", "paper", "title_block", "lib_symbols",
                       "embedded_fonts"):
                pass
            else:
                sch.unsupported[k] = sch.unsupported.get(k, 0) + 1
        except Exception as e:
            issues.error("sch", f"{path.name}: failed to read '{k}': {e}")
    return sch
