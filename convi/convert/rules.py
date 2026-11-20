"""Net classes, differential pairs and design rules (KiCad -> Altium).

Every Altium rule Convi writes has a KiCad source (net class, board setup, zone/pad setting or a .kicad_dru
rule) and changes a DRC or routing result; rules that would never decide anything are not written:

* Clearance follows KiCad's resolution order: local overrides (pad, footprint, zone clearances) > custom
  rules (later rule wins) > net class (max of both items' classes) > board minimum.  Altium uses the single
  highest-priority matching rule, so KiCad's max() is emulated by ordering the class rules by gap.
* A net-class rule equal to the Default class, or fully covered by a custom rule, is dropped.
* KiCad's intersectsCourtyard() becomes an Altium room per footprint (Courtyard_<ref>, DRC off) used with
  TouchesRoom().
* Differential-pair rules are scoped to the pairs themselves (InNetClass never matches a pair rule).

Rules are generated most-specific first.  Altium gives every newly added rule the highest priority, so the
RULE commands are re-emitted per kind in reverse order (least specific first, most specific last).
"""
from __future__ import annotations

import math

from ..altium.ir import mm
from ..kicad.pcb import _fp_abs
from .dru import (FALSE, GEOMETRIC, TRUE, Translator, evaluate, has_fn, objects, parse_dru, split_ab, split_dnf,
                  strip_type)

# KiCad's DRC accepts values within 0.0005 mm of a minimum (ADVANCED_CFG DRCEpsilon); Altium has no
# tolerance, so minimum limits are written that much lower to give the same DRC verdicts.
DRC_EPS = 0.0005
EPS = 1e-6
BIG = 100.0  # mm, "no maximum"


def lim(v: float) -> int:
    """Altium minimum limit for a KiCad minimum value (mm)."""
    return mm(max(0.0, float(v) - DRC_EPS))


def q(name: str) -> str:
    """Quote a name for an Altium query string."""
    return "'" + name.replace("'", "''") + "'"


def kicad_dp_match(name: str):
    """KiCad's MatchDpSuffix(): (+1|-1, complement net, base) or (0, None, None)."""
    rv, count = 0, 0
    comp = None
    for ch in reversed(name):
        count += 1
        if ch.isdigit() or ch == "_":
            continue
        if ch == "+":
            comp, rv = "-", 1
        elif ch == "-":
            comp, rv = "+", -1
        elif ch == "N":
            comp, rv = "P", -1
        elif ch == "P":
            comp, rv = "N", 1
        break
    if rv == 0 or count < 1:
        return 0, None, None
    base = name[: len(name) - count]
    return rv, base + comp + name[len(name) - count + 1:], base


def _diff_pairs(names: list[str]) -> list[tuple[str, str, str]]:
    """KiCad differential pairs among net names: (base, positive, negative)."""
    s = set(names)
    out = []
    for n in names:
        rv, other, base = kicad_dp_match(n)
        if rv == 1 and other in s:
            out.append((base, n, other))
    return out


class _RuleBuffer:
    """Collect RULE commands, forward everything else, flush reversed per kind."""

    def __init__(self, ir):
        self.ir = ir
        self.rules = []
        self.lines = ir.lines

    def cmd(self, _cmd, **kw):
        if _cmd == "RULE":
            self.rules.append(kw)
        else:
            self.ir.cmd(_cmd, **kw)

    def flush(self):
        kinds = []
        for r in self.rules:
            if r["kind"] not in kinds:
                kinds.append(r["kind"])
        if "room" in kinds:               # rooms first: other rules refer to them with TouchesRoom()
            kinds.remove("room")
            kinds.insert(0, "room")
        used = set()
        for k in kinds:
            for r in reversed([r for r in self.rules if r["kind"] == k]):
                name = r["name"]
                n, i = name, 2
                while n.lower() in used:
                    n = f"{name[:56]}_{i}"
                    i += 1
                used.add(n.lower())
                r = dict(r, name=n)
                self.ir.cmd("RULE", **r)


def emit_classes_and_rules(conv, ir_real):
    ir = _RuleBuffer(ir_real)
    RuleBuilder(conv, ir).emit()
    ir.flush()


# ---------------------------------------------------------------------------------------------- helpers
def _rectangles(poly, eps=1e-6):
    """An axis-parallel polygon as rectangles (x0, y0, x1, y1) that cover it exactly; None otherwise."""
    n = len(poly)
    if n < 4 or any(abs(poly[i][0] - poly[i - 1][0]) > eps and abs(poly[i][1] - poly[i - 1][1]) > eps
                    for i in range(n)):
        return None
    xs = sorted({round(p[0], 6) for p in poly})
    slabs = []
    for xa, xb in zip(xs, xs[1:]):
        xm = (xa + xb) / 2
        ys = sorted(round(poly[i][1], 6) for i in range(n)
                    if abs(poly[i][1] - poly[i - 1][1]) <= eps
                    and min(poly[i][0], poly[i - 1][0]) < xm < max(poly[i][0], poly[i - 1][0]))
        slabs.append((xa, xb, tuple(zip(ys[0::2], ys[1::2]))))
    out = []
    for xa, xb, ivs in slabs:          # merge neighbouring slabs with the same intervals
        if out and out[-1][2] == ivs and abs(out[-1][1] - xa) <= eps:
            out[-1] = (out[-1][0], xb, ivs)
        else:
            out.append((xa, xb, ivs))
    return [(xa, ya, xb, yb) for xa, xb, ivs in out for ya, yb in ivs]


def _rule_areas(b) -> dict:
    """Named KiCad rule areas (net-less zones) that are made of rectangles: name -> rectangles."""
    areas: dict = {}
    for z in list(b.zones) + [z for fp in b.footprints for z in fp.zones]:
        if z.net or not z.name or z.holes:
            continue
        rects = _rectangles(z.outline)
        if rects is not None:
            areas.setdefault(z.name, []).extend(rects)
    return areas


def _courtyards(b):
    """ref -> {"sides": {"F","B"}, "bbox": (x0,y0,x1,y1) mm, "rect": bool}"""
    out = {}
    for fp in b.footprints:
        pts, sides, area = [], set(), 0.0
        for sh in fp.shapes:
            if sh.layer not in ("F.CrtYd", "B.CrtYd"):
                continue
            sides.add(sh.layer[0])
            if sh.kind == "rect" and sh.local_pts:
                (ax, ay), (bx, by) = sh.local_pts[0], sh.local_pts[1]
                pts += [_fp_abs(fp, p) for p in ((ax, ay), (bx, ay), (bx, by), (ax, by))]
                area += abs(bx - ax) * abs(by - ay)
            elif sh.kind == "circle" and len(sh.pts) >= 2:
                (cx, cy), (px, py) = sh.pts[0], sh.pts[1]
                r = math.hypot(px - cx, py - cy)
                pts += [(cx - r, cy - r), (cx + r, cy + r)]
                area = -1.0
            elif sh.kind == "arc" and len(sh.pts) == 3:
                pts += _arc_samples(*sh.pts)
                area = -1.0
            else:
                pts += list(sh.pts)
                if sh.kind == "poly" and len(sh.pts) >= 3 and area >= 0:
                    a = 0.0
                    for i in range(len(sh.pts)):
                        x0, y0 = sh.pts[i]
                        x1, y1 = sh.pts[(i + 1) % len(sh.pts)]
                        a += x0 * y1 - x1 * y0
                    area += abs(a) / 2
                elif sh.kind != "line":
                    area = -1.0
        if not pts:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        bb = (min(xs), min(ys), max(xs), max(ys))
        bba = (bb[2] - bb[0]) * (bb[3] - bb[1])
        rect = area > 0 and abs(area - bba) < 1e-3 * max(bba, 1e-9)
        if area == 0.0:     # outline drawn from lines: rectangular if exactly 4 axis-parallel lines
            lines = [s for s in fp.shapes if s.layer in ("F.CrtYd", "B.CrtYd")]
            rect = len(lines) == 4 and all(s.kind == "line" and (abs(s.pts[0][0] - s.pts[1][0]) < 1e-6 or
                                                                  abs(s.pts[0][1] - s.pts[1][1]) < 1e-6)
                                           for s in lines)
        out[fp.ref] = {"sides": sides, "bbox": bb, "rect": rect, "fp": fp}
    return out


def _arc_samples(s, m, e, n=16):
    ax, ay = s
    bx, by = m
    cx, cy = e
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return [s, m, e]
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    a0 = math.atan2(ay - uy, ax - ux)
    am = math.atan2(by - uy, bx - ux)
    a1 = math.atan2(cy - uy, cx - ux)

    def ccw(a, b):
        return (b - a) % (2 * math.pi)
    sweep = ccw(a0, a1)
    if ccw(a0, am) > sweep:          # the arc runs clockwise
        sweep = sweep - 2 * math.pi
    return [(ux + r * math.cos(a0 + sweep * i / n), uy + r * math.sin(a0 + sweep * i / n)) for i in range(n + 1)]


def _list(names, k: int = 6) -> str:
    names = list(dict.fromkeys(names))
    return ", ".join(names[:k]) + (f" ... (+{len(names) - k})" if len(names) > k else "")


def _common_name(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    p = names[0]
    for n in names[1:]:
        while not n.startswith(p):
            p = p[:-1]
    p = p.rstrip("_- .")
    return p if len(p) >= 3 else names[0]


# KiCad DRC checks (board rule_severities keys) behind each Altium rule kind: a check the KiCad project sets
# to 'ignore' keeps its Altium rule (values stay visible) but out of DRC
SEVERITY_KEYS = {
    "clearance": ("clearance",), "short": ("shorting_items",), "outline": ("copper_edge_clearance",),
    "width": ("track_width",), "vias": ("via_diameter",), "annular": ("annular_width",),
    "holesize": ("drill_out_of_range",), "holetohole": ("hole_to_hole",), "sliver": ("solder_mask_bridge",),
    "silkmask": ("silk_over_copper",), "silksilk": ("silk_overlap",), "compclear": ("courtyards_overlap",),
    "creepage": ("creepage",),
}


# ---------------------------------------------------------------------------------------------- builder
class RuleBuilder:
    def __init__(self, conv, ir):
        self.conv = conv
        self.ir = ir
        self.b = conv.b
        self.st = getattr(conv, "project", None)
        self.iss = conv.issues
        self.report: list[dict] = []
        conv.rule_report = self.report
        self.brd = (self.st.rules if self.st else {}) or {}
        self.min_clear = float(self.brd.get("min_clearance", 0.0) or 0.0)
        self.min_w = float(self.brd.get("min_track_width", 0.0) or 0.0)
        self.min_vd = float(self.brd.get("min_via_diameter", 0.0) or 0.0)
        self.min_hd = float(self.brd.get("min_through_hole_diameter", 0.0) or 0.0)
        ds = (((self.st.data or {}).get("board") or {}).get("design_settings") or {}) if self.st else {}
        self.sev = ds.get("rule_severities") or {}
        self.sev_off: dict[str, list[str]] = {}      # KiCad check -> Altium rules left out of DRC
        self.sev_warn: dict[str, str] = {}           # Altium rule -> KiCad check the project reports as a warning
        conv.rule_kicad_warning = self.sev_warn

        # nets and classes
        self.netmap = {}
        for kn in self.b.nets:
            an = conv.altium_net_name(kn)
            if an:
                self.netmap[kn] = an
        self.classes = list(self.st.netclasses) if self.st else list(self.b.netclasses_legacy)
        self.cls_by_name = {c.name: c for c in self.classes}
        self.default = self.cls_by_name.get("Default")
        self._net_classes = {}
        for kn in self.netmap:
            cls = list(self.st.classes_of(kn)) if self.st else []
            for nc in self.b.netclasses_legacy:
                if kn in nc.nets and nc.name not in cls:
                    cls.append(nc.name)
            self._net_classes[kn] = [c for c in cls if c != "Default" and c in self.cls_by_name]
        self.members: dict[str, list[str]] = {}
        for kn, cls in self._net_classes.items():
            for c in cls:
                self.members.setdefault(c, [])
                if self.netmap[kn] not in self.members[c]:
                    self.members[c].append(self.netmap[kn])
        self.created_classes = [c.name for c in self.classes if c.name != "Default" and c.name in self.members]
        # effective class (KiCad: the highest-priority class of a net decides)
        self.eff: dict[str, object] = {}
        for kn, cls in self._net_classes.items():
            objs = sorted((self.cls_by_name[c] for c in cls), key=lambda c: c.priority)
            self.eff[kn] = objs[0] if objs else self.default
        self.eff_nets: dict[str, list[str]] = {}
        for kn, c in self.eff.items():
            if c is not None and c.name != "Default":
                self.eff_nets.setdefault(c.name, []).append(kn)
        self.overlap = {}       # class -> higher-priority classes sharing nets with it
        for kn, cls in self._net_classes.items():
            if len(cls) > 1:
                objs = sorted((self.cls_by_name[c] for c in cls), key=lambda c: c.priority)
                for i, c in enumerate(objs):
                    self.overlap.setdefault(c.name, set()).update(o.name for o in objs[:i])

        # custom rules
        self.cy = _courtyards(self.b)
        cu = conv.lm.copper
        disp = {}
        for name in cu:
            lay = self.b.layer(name)
            disp[name] = lay.display if lay else name
        self.layer_name = disp
        self.tr = Translator(self.created_classes, list(self.netmap), lambda n: self.netmap.get(n),
                             [fp.ref for fp in self.b.footprints], {r: v["sides"] for r, v in self.cy.items()},
                             cu, disp, areas=_rule_areas(self.b))
        self.krules = parse_dru(conv.dru_path, self.iss) if getattr(conv, "dru_path", None) else []

    # ------------------------------------------------------------------ small helpers
    def classes_of(self, kn: str) -> list[str]:
        return self._net_classes.get(kn, [])

    def cls_scope(self, name: str) -> str:
        s = f"InNetClass({q(name)})"
        hi = sorted(self.overlap.get(name, ()))
        if hi:
            s = f"({s} and not ({' or '.join(f'InNetClass({q(h)})' for h in hi)}))"
        return s

    def eff_val(self, kn: str, attr: str):
        c = self.eff.get(kn) or self.default
        v = getattr(c, attr, None) if c else None
        if v is None and self.default is not None:
            v = getattr(self.default, attr, None)
        return v

    def eff_clear(self, kn: str) -> float:
        return max(self.eff_val(kn, "clearance") or 0.0, self.min_clear) if kn else self.dflt_clear

    @property
    def dflt_clear(self) -> float:
        d = self.default.clearance if self.default and self.default.clearance is not None else 0.2
        return max(d, self.min_clear)

    def note(self, source: str, altium: str, kind: str, detail: str = ""):
        self.report.append({"kicad": source, "altium": altium, "kind": kind, "detail": detail})

    def rule(self, kind, name, source, **kw):
        name = name[:60]
        keys = SEVERITY_KEYS.get(kind, ())
        if keys and all(self.sev.get(k) == "ignore" for k in keys):
            kw["nodrc"] = 1
            source += " (KiCad: check set to ignore, so not in DRC)"
            for k in keys:
                self.sev_off.setdefault(k, []).append(name)
        elif any(self.sev.get(k) == "warning" for k in keys):
            self.sev_warn[name] = next(k for k in keys if self.sev.get(k) == "warning")
        self.ir.cmd("RULE", kind=kind, name=name, comment=source, **kw)
        self.report.append({"kicad": source, "altium": name, "kind": kind, "detail": "created"})

    def item(self, kn="", typ=None, ref=None):
        return {"net": kn, "type": typ, "ref": ref}

    def ev(self, node, a, b_=None):
        env = {"A": a}
        if b_ is not None:
            env["B"] = b_
        return evaluate(node, env, self.classes_of)

    def ev2(self, node, a, b_):
        """KiCad tests two-object rules in both orders."""
        x = self.ev(node, a, b_)
        if x is True:
            return True
        y = self.ev(node, b_, a)
        if y is True:
            return True
        return None if None in (x, y) else False

    def layer_set(self, kr):
        if kr.layer is None:
            return None
        return self.tr.layers_matching(kr.layer)

    def with_layers(self, scope: str, layers) -> str | None:
        if layers is None:
            return scope
        if not layers:
            return None
        lq = " or ".join(f"OnLayer({q(self.layer_name.get(l, l))})" for l in layers)
        if len(layers) > 1:
            lq = f"({lq})"
        return lq if scope == "All" else f"({scope}) and {lq}"

    # ------------------------------------------------------------------ main
    def emit(self):
        ir = self.ir
        b = self.b
        # net classes
        for name in self.created_classes:
            ir.cmd("CLASS", kind="net", name=name)
            for an in self.members[name]:
                ir.cmd("CLASSM", name=name, member=an)
        # differential pairs (KiCad naming rule on KiCad net names)
        self.pairs = []
        used = set()
        for base, p, n in _diff_pairs(list(self.netmap)):
            nm = (base.split("/")[-1].rstrip("_") or self.netmap[p])
            k, i = nm, 2
            while k in used:
                k = f"{nm}_{i}"
                i += 1
            used.add(k)
            self.pairs.append((k, p, n))
            ir.cmd("DIFFPAIR", name=k, p=self.netmap[p], n=self.netmap[n])

        kinds = ["clearance", "width", "vias", "diffpair", "polyconnect", "holesize", "holetohole", "annular",
                 "maskexp", "pasteexp", "sliver", "silkmask", "silksilk", "compclear", "outline", "creepage", "short"]
        # Altium's default Height rule (25.4 mm) has no KiCad counterpart and flags tall parts (MOVs, current
        # transformers): switched off (a deleted default rule comes back)
        # Altium's default Net Antennae rule is KiCad's dangling track / via check (track_dangling,
        # via_dangling): off when KiCad ignores both, else its violations are named as KiCad warnings
        # (e.g. 82 antennae on a test board; KiCad reports the same stubs as warnings)
        off = ["Height"]
        dang = [self.sev.get(k, "warning") for k in ("track_dangling", "via_dangling")]
        if all(v == "ignore" for v in dang):
            off.append("NetAntennae")
            self.sev_off.setdefault("track_dangling/via_dangling", []).append("NetAntennae")
        elif any(v == "warning" for v in dang):
            self.sev_warn["NetAntennae"] = "track_dangling/via_dangling"
        ir.cmd("RULECLEAR", kinds=",".join(kinds), names="|".join(off))
        self.note("(none: KiCad has no part height rule)", "Height", "height", "Altium default rule switched off")
        self.conv.creepage_rules = []      # (Altium rule name, mm, KiCad rule) for the PcbDoc patch

        self.custom = self._translate_custom()
        self._rooms()
        self._clearance()
        self._width()
        self._vias()
        self._diffpairs()
        self._custom_misc()
        self._polyconnect()
        self._manufacturing()
        self._unsupported_board_rules()
        if self.sev_off:
            self.iss.info("rules", "KiCad checks set to 'ignore' in the project: their Altium rules stay but are "
                                   "left out of DRC: " + "; ".join(f"{k} -> {_list(v, 3)}"
                                                                   for k, v in sorted(self.sev_off.items())))
        made = [r for r in self.report if r["detail"] == "created"]
        rooms = sum(1 for r in made if r["kind"] == "room")
        self.iss.info("rules", f"Design rules: {len(made) - rooms} Altium rules + {rooms} courtyard room(s) from "
                               f"{len(self.krules)} custom KiCad rule(s), net classes and board setup "
                               f"(mapping in convi_report.json)")

    # ------------------------------------------------------------------ custom rules
    def _translate_custom(self):
        """Translate .kicad_dru rules; returns them highest precedence first (KiCad: last rule wins)."""
        out = []
        for kr in self.krules:
            if kr.error:
                self.iss.warn("rules", f"Custom rule '{kr.name}' not converted: {kr.error}")
                self.note(kr.name, "", "-", f"not converted: {kr.error}")
                kr.cond = None
                continue
            out.append(kr)
        return list(reversed(out))

    def _scopes(self, kr, unary: bool):
        """(s1, s2) Altium queries for a rule's condition; None scope = never matches."""
        a, bpart = split_ab(kr.cond)
        if unary and bpart != TRUE:
            raise ValueError("B-object terms on a single-object constraint")
        s1 = self.tr.to_query(a)
        s2 = self.tr.to_query(bpart)
        return s1, s2

    # ------------------------------------------------------------------ rooms (courtyards)
    def _rooms(self):
        # translate every custom condition once so the translator knows which courtyards are used
        for kr in self.custom:
            try:
                a, bp = split_ab(kr.cond)
                self.tr.to_query(a)
                self.tr.to_query(bp)
            except ValueError:
                try:
                    for a, bp in split_dnf(kr.cond):
                        self.tr.to_query(a)
                        self.tr.to_query(bp)
                except ValueError:
                    pass
        for ref, nm in sorted(self.tr.rooms.items()):
            c = self.cy[ref]
            x0, y0, x1, y1 = c["bbox"]
            side = "B" if (c["sides"] == {"B"} or (len(c["sides"]) == 2 and c["fp"].flipped)) else "T"
            self.rule("room", nm, f"KiCad courtyard of {ref} (intersectsCourtyard)", s1=f"InComponent({q(ref)})",
                      l=side, x1=self.conv.X(x0), y1=self.conv.Y(y1), x2=self.conv.X(x1), y2=self.conv.Y(y0))
            if not c["rect"]:
                self.iss.info("rules", f"Room '{nm}': the courtyard of {ref} is not a rectangle; the room uses its "
                                       f"bounding rectangle ({x1 - x0:.2f} x {y1 - y0:.2f} mm)")

        for name, rooms in sorted(self.tr.area_rooms.items()):
            for nm, (x0, y0, x1, y1) in rooms:
                self.rule("room", nm, f"KiCad rule area '{name}' (insideArea / intersectsArea)",
                          s1="InComponent('__convi_none__')", l="T",
                          x1=self.conv.X(x0), y1=self.conv.Y(y1), x2=self.conv.X(x1), y2=self.conv.Y(y0))
            if len(rooms) > 1:
                self.iss.info("rules", f"Rule area '{name}' became {len(rooms)} rooms (Altium rooms are rectangles; "
                                       f"together they cover the area exactly)")

    # ------------------------------------------------------------------ clearance
    def _clearance(self):
        b = self.b
        mc = self.min_clear
        d = self.dflt_clear
        nets = list(self.netmap) + [""]
        # keepouts: KiCad rule areas only forbid objects inside them
        if any(z.keepout for z in b.zones) or any(z.keepout for fp in b.footprints for z in fp.zones):
            self.rule("clearance", "Convi_KeepoutEdge", "KiCad rule areas (keepouts) need no clearance",
                      s1="IsKeepOut", s2="All", gap=0)

        # board cutouts (slots): KiCad holds copper off Edge.Cuts only with the edge clearance (Altium's board
        # outline clearance rule); its clearance rules do not reach the board edge, Altium's do
        if getattr(self.conv, "board_cutouts", 0):
            self.rule("clearance", "Convi_BoardCutout", "KiCad clearance rules do not apply to board edges and "
                      "cutouts (the copper to edge clearance does)", s1="IsBoardCutoutRegion", s2="All", gap=0)

        # local overrides: pads, footprints, zones
        overrides = []
        pad_cl: dict[float, list[str]] = {}
        comp_cl: dict[float, list[str]] = {}
        for fp in b.footprints:
            if fp.clearance:
                comp_cl.setdefault(fp.clearance, []).append(fp.ref)
            for pd in fp.pads:
                if pd.clearance:
                    lst = pad_cl.setdefault(pd.clearance, [])
                    if f"{fp.ref}-{pd.number}" not in lst:
                        lst.append(f"{fp.ref}-{pd.number}")
        # KiCad's hole clearance (board minimum, measured from the drill edge) is a separate constraint that
        # applies on top of copper clearances; Altium measures from the pad copper, so pads whose copper ring
        # is too thin for it (NPTH pads the size of their hole) get a pad-class clearance of (hole clearance - ring)
        self.hole_clear_pads = 0
        self.hole_rules = []          # (gap, class name, source): emitted just above the Default clearance
        hc = float(self.brd.get("min_hole_clearance", 0.0) or 0.0)
        if hc > 0:
            # KiCad's zone filler and DRC treat the two kinds of hole differently (zone_filler.cpp,
            # knockoutPadClearance): an NPTH hole gets only the physical / hole clearances - copper
            # clearance rules (net class, zone, custom 'clearance') never reach it; a plated hole gets the
            # copper clearance as well.  Key: (gap, npth)
            hole_cl: dict[tuple, list[str]] = {}
            for fp in b.footprints:
                for pd in fp.pads:
                    if pd.drill is None or pd.type not in ("thru_hole", "np_thru_hole"):
                        continue
                    dmin = min(pd.drill.w, pd.drill.h or pd.drill.w)
                    has_cu = any(l.endswith(".Cu") for l in pd.layers)
                    ring = max(0.0, (min(pd.size) - dmin) / 2.0) if has_cu else 0.0
                    npth = pd.type == "np_thru_hole"
                    base = self.eff_clear(pd.net or "")
                    if hc - ring <= base + EPS:
                        continue
                    gap = round(max(hc - ring, mc), 6)
                    lst = hole_cl.setdefault((gap, npth), [])
                    m = f"{fp.ref}-{pd.number}"
                    if m not in lst:
                        lst.append(m)
            # pads ('ref-number') whose Altium outline is exactly their non-plated hole: a clearance
            # violation of such a pad is KiCad's own hole clearance violation (the pipeline reports it so)
            exact: dict[str, bool] = {}
            for fp in b.footprints:
                for pd in fp.pads:
                    k = f"{fp.ref}-{pd.number}"
                    ok = (pd.type == "np_thru_hole" and pd.drill is not None
                          and abs(pd.size[0] - pd.drill.w) < EPS
                          and abs(pd.size[1] - (pd.drill.h or pd.drill.w)) < EPS)
                    exact[k] = exact.get(k, True) and ok
            self.conv.hole_exact_pads = {m for (cl, npth), mems in hole_cl.items() if npth
                                         for m in mems if exact.get(m)}
            self.conv.hole_clearance_mm = hc
            hole_item = self.item("", "pad")
            for i, ((cl, npth), mems) in enumerate(sorted(hole_cl.items())):
                n = f"Convi_HoleClearance_{i + 1}"
                self.ir.cmd("CLASS", kind="pad", name=n)
                for m in mems:
                    self.ir.cmd("CLASSM", name=n, member=m)
                self.hole_clear_pads += len(mems)
                src = f"KiCad hole clearance {hc:g} mm from the drill edge ({_list(mems)})"
                if npth:
                    # NPTH: the hole clearance decides against everything (a 2.5 mm isolation or 4 mm mains
                    # clearance rule does not reach a net-less NPTH hole in KiCad), so this override sits
                    # above the class and custom rules (seen on a test board: a USB isolation rule held the
                    # isolated-ground pours 2.5 mm off the mounting holes and flagged 71 hole/USB pairs KiCad
                    # accepts).
                    # Only custom hole / physical clearance rules that surely apply raise it, per pour.
                    src += " (NPTH: copper clearance rules do not apply in KiCad)"
                    overrides.append((cl, 2, n, f"InPadClass({q(n)})", src))
                    kinds = ("hole_clearance", "physical_hole_clearance", "physical_clearance")
                else:
                    # plated: against tracks and pads it is the weakest rule (KiCad checks the hole clearance
                    # on top of the copper clearance, so any class or custom rule that also matches decides);
                    # below the Default clearance only this one applies
                    self.hole_rules.append((cl, n, src))
                    kinds = ("clearance", "hole_clearance", "physical_hole_clearance", "physical_clearance")
                # against pours (the pour honours one rule per pair, and zone rules sit above the plated
                # hole rule): each polygon at the largest gap KiCad's filler uses for this hole
                by_gap: dict[float, list[str]] = {}
                for z, pn in getattr(self.conv, "polygon_names", []):
                    if npth:
                        g = cl
                    else:
                        g = max(cl, z.clearance or 0.0, self.eff_clear(z.net or "") if z.net else mc,
                                self.dflt_clear)
                    zi = self.item(z.net or "", "zone")
                    for kr in self.custom:
                        for c in kr.constraints:
                            # only rules that surely apply: one that depends on where things are (a rule
                            # area, a courtyard) would otherwise hold every pour off every hole (seen on a test
                            # board: an 8 mm area rule emptied the pours around a connector's NPTH pegs)
                            if c.kind in kinds and "min" in c.vals and self.ev2(kr.cond, zi, hole_item) is True:
                                g = max(g, float(c.vals["min"]))
                    if npth and g <= cl + EPS:
                        continue            # the override above already gives this pour the hole clearance
                    by_gap.setdefault(round(g, 6), []).append(pn)
                for j, (g, pns) in enumerate(sorted(by_gap.items(), key=lambda t: -t[0])):
                    overrides.append((g, 4, f"{n}_pour" + (f"_{j + 1}" if len(by_gap) > 1 else ""),
                                      (f"InPadClass({q(n)})", " or ".join(f"InNamedPolygon({q(x)})" for x in pns)),
                                      src + f" against the pours of {_list(pns)}"))
        for i, (cl, mems) in enumerate(sorted(pad_cl.items())):
            n = f"Convi_PadClearance_{i + 1}"
            self.ir.cmd("CLASS", kind="pad", name=n)
            for m in mems:
                self.ir.cmd("CLASSM", name=n, member=m)
            overrides.append((max(cl, mc), 2, n, f"InPadClass({q(n)})",
                              f"KiCad pad clearance override ({_list(mems)})"))
        for i, (cl, mems) in enumerate(sorted(comp_cl.items())):
            n = f"Convi_FpClearance_{i + 1}"
            self.ir.cmd("CLASS", kind="comp", name=n)
            for m in mems:
                self.ir.cmd("CLASSM", name=n, member=m)
            overrides.append((max(cl, mc), 1, n, f"InComponentClass({q(n)})",
                              f"KiCad footprint clearance override ({_list(mems)})"))
        # KiCad fills a zone at the largest of the zone clearance and the net class clearances of the zone's
        # net and of the other item (e.g. 0.4 mm class vias inside a 0.3 mm zone). An Altium rule takes
        # one value, so a zone rule holds max(zone, own class) and leaves out the classes that need more,
        # which their own class rule then decides
        cls_gap = {c: max(self.cls_by_name[c].clearance if self.cls_by_name[c].clearance is not None
                          else self.dflt_clear, mc) for c in self.eff_nets}
        zone_groups: dict[float, list[str]] = {}
        for z, nm in getattr(self.conv, "polygon_names", []):
            own = self.eff_clear(z.net or "") if z.net else mc
            cl = max(z.clearance or 0.0, mc, own)
            zi = self.item(z.net or "", "zone")
            same = all((z.clearance or 0.0) <= max(own, self.eff_clear(n)) + EPS for n in nets)
            touched = any(self.ev2(kr.cond, zi, self.item(n)) is not False
                          for kr in self.custom for c in kr.constraints if c.kind == "clearance" for n in nets)
            if same and not touched:
                continue
            zone_groups.setdefault(round(cl, 6), []).append(nm)
        zone_rules = []
        for cl, nms in zone_groups.items():
            name = f"Clearance_{nms[0]}" if len(nms) == 1 else f"Clearance_Zones_{cl:g}mm"
            scope = " or ".join(f"InNamedPolygon({q(n)})" for n in nms)
            wider = sorted(c for c, g in cls_gap.items() if g > cl + EPS)
            other = "All" if not wider else "not (" + " or ".join(self.cls_scope(c) for c in wider) + ")"
            zone_rules.append((cl, 0, name, scope, f"KiCad zone clearance {cl:g} mm ({_list(nms)})"
                               + (f"; net classes {', '.join(wider)} keep their larger clearance" if wider else ""),
                               other))
        # pad / footprint overrides beat custom rules; a zone's own clearance does not (KiCad fills a zone
        # with a matching custom rule's clearance, e.g. a 0.15 mm fanout rule inside a 0.25 mm zone)
        for cl, _, name, scope, src in sorted(overrides, key=lambda o: (-o[0], -o[1])):
            s1, s2 = scope if isinstance(scope, tuple) else (scope, "All")
            self.rule("clearance", name, src, s1=s1, s2=s2, gap=lim(cl))

        # custom rules (highest precedence first)
        self.custom_clear = []
        self.split_rules = []
        for kr in self.custom:
            for c in kr.constraints:
                if c.kind not in ("clearance", "creepage"):
                    continue
                if "min" not in c.vals:
                    self.iss.warn("rules", f"Custom rule '{kr.name}': {c.kind} without a minimum not converted")
                    continue
                try:
                    pairs = [self._scopes(kr, unary=False)]
                except ValueError as e:
                    # '(A.x && B.x) || (A.y && B.y)' (pad pairs inside one courtyard, one term per part):
                    # one Altium rule per term
                    try:
                        pairs = [(self.tr.to_query(a), self.tr.to_query(b)) for a, b in split_dnf(kr.cond)]
                    except ValueError as e2:
                        why = e2 if "more than" in str(e2) else e
                        self.iss.warn("rules", f"Custom rule '{kr.name}' not converted: {why}")
                        self.note(kr.name, "", c.kind, f"not converted: {why}")
                        continue
                pairs = [(self.with_layers(s1, self.layer_set(kr)) if s1 else None, s2) for s1, s2 in pairs]
                pairs = [(s1, s2) for s1, s2 in pairs if s1 and s2]
                if not pairs:
                    self.iss.info("rules", f"Custom rule '{kr.name}' matches no object on this board; not created")
                    self.note(kr.name, "", c.kind, "matches nothing on this board")
                    continue
                if c.kind == "clearance" and float(c.vals["min"]) < mc - EPS:
                    # KiCad's DRC and zone filler apply a custom clearance rule below the board minimum
                    # (checked with KiCad 7.0.11's DRC on a test board: a 0.14 mm fine-pitch rule under a
                    # 0.2 mm board minimum flags none of a part's 0.15 mm pad gaps; without the rule it flags
                    # six), although KiCad's documentation calls the board minimum absolute
                    self.iss.info("rules", f"Custom rule '{kr.name}': clearance {c.vals['min']:g} mm is below the board "
                                           f"minimum clearance {mc:g} mm; KiCad's DRC and zone fill apply the rule's "
                                           f"{c.vals['min']:g} mm (a custom rule overrides the board minimum), so the "
                                           f"Altium rule uses {c.vals['min']:g} mm too")
                base = "KiCad_" + kr.name
                many = len(pairs) > 1
                if many:
                    self.split_rules.append((kr.name, len(pairs)))
                for i, (s1, s2) in enumerate(pairs):
                    nm = f"{base[:54]}_{i + 1}" if many else base
                    src = f"KiCad rule '{kr.name}'" + (f" (term {i + 1} of {len(pairs)})" if many else "")
                    if c.kind == "clearance":
                        if i == 0:
                            self.custom_clear.append(kr)
                        self.rule("clearance", nm, src, s1=s1, s2=s2, gap=lim(c.vals["min"]))
                        if not many:
                            self._pour_variant(kr, nm, s1, s2, lim(c.vals["min"]), nets)
                    else:
                        label = f"{c.vals['min']:g}mm"
                        # KiCad measures creepage between copper of two nets only; Altium's rule also
                        # reaches keepout regions, and its polygon pour backs off them by the distance
                        q1, q2 = f"({s1}) and not IsKeepOut", f"({s2}) and not IsKeepOut"
                        # scripts cannot set the distance: the pipeline writes it into the saved PcbDoc
                        # (altium/pcbdoc_patch.py) and warns only if that fails
                        rn = nm[:60]
                        self.rule("creepage", rn, src, s1=q1, s2=q2, gap=mm(c.vals["min"]), label=label)
                        self.conv.creepage_rules.append((rn, float(c.vals["min"]), kr.name))

        for cl, _, name, scope, src, other in sorted(zone_rules, key=lambda o: -o[0]):
            self.rule("clearance", name, src, s1=scope, s2=other, gap=lim(cl))

        # differential pair partners: KiCad's router keeps the pair at the class diff-pair gap
        for name, p, n in self.pairs:
            c = self.eff.get(p) or self.default
            gap = c.dp_gap if c and c.dp_gap else None
            cl = self.eff_clear(p)
            if gap and gap < cl - EPS:
                self.rule("clearance", f"DiffPairGap_{name}", f"KiCad net class '{c.name}' diff-pair gap",
                          s1=f"InNet({q(self.netmap[p])})", s2=f"InNet({q(self.netmap[n])})", gap=lim(max(gap, mc)))

        # net classes: KiCad uses the larger clearance of the two nets
        used = [self.cls_by_name[c] for c in self.eff_nets]
        cl_of = {c.name: max(c.clearance if c.clearance is not None else d, mc) for c in used}
        above = sorted([c for c in used if cl_of[c.name] > d + EPS], key=lambda c: (-cl_of[c.name], c.priority))
        below = sorted([c for c in used if cl_of[c.name] < d - EPS], key=lambda c: (-cl_of[c.name], c.priority))
        pending_holes = sorted(getattr(self, "hole_rules", []), key=lambda t: -t[0])
        for c in above + below:
            gap = cl_of[c.name]
            # a hole clearance rule goes above the class rules with a smaller gap: KiCad takes the larger
            while pending_holes and pending_holes[0][0] > gap + EPS:
                hcl, hn, hsrc = pending_holes.pop(0)
                self.rule("clearance", hn, hsrc, s1=f"InPadClass({q(hn)})", s2="All", gap=lim(hcl))
            if c in above:
                partners = None
                s2 = "All"
            else:
                partners = [y.name for y in below if cl_of[y.name] <= gap + EPS]
                s2 = " or ".join(self.cls_scope(y) for y in partners)
            mine = self.eff_nets[c.name]
            other = nets if partners is None else [n for y in partners for n in self.eff_nets[y]]
            if self._clear_shadowed(mine, other):
                self.iss.info("rules", f"Net class '{c.name}' clearance: every pair is decided by a custom rule; "
                                       f"no class rule needed")
                self.note(f"net class '{c.name}' clearance", "", "clearance", "covered by custom rules")
                continue
            self.rule("clearance", f"Clearance_{c.name}", f"KiCad net class '{c.name}' clearance {gap:g} mm",
                      s1=self.cls_scope(c.name), s2=s2, gap=lim(gap))
        for c in used:
            if abs(cl_of[c.name] - d) < EPS:
                self.note(f"net class '{c.name}' clearance", "Clearance", "clearance", "same as Default")
        for cl, n, src in pending_holes:
            self.rule("clearance", n, src, s1=f"InPadClass({q(n)})", s2="All", gap=lim(cl))
        self.rule("clearance", "Clearance", f"KiCad Default net class / board minimum clearance {d:g} mm",
                  s1="All", s2="All", gap=lim(d))

    def _pour_variant(self, kr, nm, s1, s2, gap, nets):
        """Altium's pour engine resolves a polygon's clearance with the polygon object itself, for which a
        geometric scope (TouchesRoom, a courtyard) never matches: a custom rule that reaches a zone only by
        position is repeated with the zone named (e.g. a 0.15 mm fanout rule, poured at the 0.25 mm zone
        clearance without this)."""
        if not has_fn(kr.cond, ("intersectsCourtyard", "intersectsFrontCourtyard", "intersectsBackCourtyard")):
            return
        side_a, side_b = [], []
        for z, pn in getattr(self.conv, "polygon_names", []):
            zi = self.item(z.net or "", "zone")
            if any(self.ev(kr.cond, zi, self.item(n)) is not False for n in nets):
                side_a.append(pn)
            elif any(self.ev(kr.cond, self.item(n), zi) is not False for n in nets):
                side_b.append(pn)
        for zones, other in ((side_a, s2), (side_b, s1)):
            if not zones:
                continue
            zs = " or ".join(f"InNamedPolygon({q(n)})" for n in zones)
            self.rule("clearance", (nm + "_pour")[:60], f"KiCad rule '{kr.name}' for the pour of {_list(zones)} "
                      f"(Altium's pour does not test a polygon against a room)", s1=zs, s2=other, gap=gap)

    def _clear_shadowed(self, mine, other) -> bool:
        net_only = [kr for kr in self.custom_clear if not has_fn(kr.cond, GEOMETRIC + ("Type", "memberOfFootprint"))]
        if not net_only:
            return False
        for a in mine:
            for o in other:
                if not any(self.ev2(kr.cond, self.item(a), self.item(o)) is True for kr in net_only):
                    return False
        return True

    # ------------------------------------------------------------------ width
    def _width(self):
        dw = self.default.track_width if self.default and self.default.track_width else None
        custom_net_only = []
        for kr in self.custom:
            for c in kr.constraints:
                if c.kind != "track_width":
                    continue
                v = c.vals
                if not v:
                    continue
                try:
                    s1, _ = self._scopes(kr, unary=True)
                except ValueError as e:
                    self.iss.warn("rules", f"Custom rule '{kr.name}' not converted: {e}")
                    self.note(kr.name, "", "width", f"not converted: {e}")
                    continue
                s1 = self.with_layers(s1, self.layer_set(kr)) if s1 else None
                if not s1:
                    self.note(kr.name, "", "width", "matches nothing on this board")
                    continue
                pref = v.get("opt", v.get("min", v.get("max")))
                lo = max(v.get("min", self.min_w), self.min_w)
                hi = v.get("max", max(pref, BIG / 10))
                self.rule("width", "KiCad_" + kr.name, f"KiCad rule '{kr.name}'", s1=s1, min=lim(min(lo, pref)),
                          pref=mm(pref), max=mm(max(hi, pref)))
                if kr.layer is None and not has_fn(kr.cond, GEOMETRIC + ("Type", "memberOfFootprint")):
                    custom_net_only.append(kr)
        for c in sorted(self.classes, key=lambda c: c.priority):
            if c.name == "Default" or c.name not in self.eff_nets or c.track_width is None:
                continue
            if dw is not None and abs(c.track_width - dw) < EPS:
                self.note(f"net class '{c.name}' track width", "Width", "width", "same as Default")
                continue
            if all(any(self.ev(kr.cond, self.item(n)) is True for kr in custom_net_only) for n in self.eff_nets[c.name]):
                self.iss.info("rules", f"Net class '{c.name}' track width: replaced by a custom rule for all its nets")
                self.note(f"net class '{c.name}' track width", "", "width", "covered by custom rules")
                continue
            self.rule("width", f"Width_{c.name}", f"KiCad net class '{c.name}' track width {c.track_width:g} mm",
                      s1=self.cls_scope(c.name), min=lim(min(self.min_w, c.track_width)), pref=mm(c.track_width),
                      max=mm(max(BIG / 10, c.track_width)))
        if dw:
            self.rule("width", "Width", f"KiCad Default net class track width {dw:g} mm / board minimum",
                      s1="All", min=lim(min(self.min_w, dw)), pref=mm(dw), max=mm(BIG / 10))

    # ------------------------------------------------------------------ vias
    def _vias(self):
        for kr in self.custom:
            for c in kr.constraints:
                if c.kind not in ("via_diameter", "hole_size") or not c.vals:
                    continue
                try:
                    s1, _ = self._scopes(kr, unary=True)
                except ValueError as e:
                    self.iss.warn("rules", f"Custom rule '{kr.name}' not converted: {e}")
                    continue
                s1 = self.with_layers(s1, self.layer_set(kr)) if s1 else None
                if not s1:
                    continue
                v = c.vals
                if c.kind == "via_diameter":
                    pref = v.get("opt", v.get("min", v.get("max")))
                    self.rule("vias", "KiCad_" + kr.name, f"KiCad rule '{kr.name}'", s1=s1,
                              min=lim(v.get("min", pref)), pref=mm(pref), max=mm(v.get("max", max(pref, 2.0))),
                              hmin=mm(0.1), hpref=mm(pref / 2), hmax=mm(pref))
                else:
                    self.rule("holesize", "KiCad_" + kr.name, f"KiCad rule '{kr.name}'", s1=s1,
                              min=lim(v.get("min", 0.1)), max=mm(v.get("max", 6.35)))
        dflt = self.default
        for c in sorted(self.classes, key=lambda c: c.priority):
            if c.name == "Default" or c.name not in self.eff_nets or c.via_dia is None or c.via_drill is None:
                continue
            if dflt and dflt.via_dia is not None and abs(c.via_dia - dflt.via_dia) < EPS and \
                    abs(c.via_drill - (dflt.via_drill or 0)) < EPS:
                self.note(f"net class '{c.name}' via", "RoutingVias", "vias", "same as Default")
                continue
            self._via_rule(f"RoutingVias_{c.name}", self.cls_scope(c.name), c, f"KiCad net class '{c.name}' via")
        if dflt and dflt.via_dia is not None and dflt.via_drill is not None:
            self._via_rule("RoutingVias", "All", dflt, "KiCad Default net class via / board minimum")

    def _via_rule(self, name, s1, c, src):
        self.rule("vias", name, f"{src} {c.via_dia:g}/{c.via_drill:g} mm", s1=s1,
                  min=lim(min(self.min_vd, c.via_dia)), pref=mm(c.via_dia), max=mm(max(c.via_dia, 2.0)),
                  hmin=lim(min(self.min_hd, c.via_drill)), hpref=mm(c.via_drill), hmax=mm(max(c.via_drill, 1.5)))

    # ------------------------------------------------------------------ differential pairs
    def _diffpairs(self):
        groups: dict[tuple, dict] = {}
        for name, p, n in self.pairs:
            c = self.eff.get(p) or self.default
            ip, inn = self.item(p), self.item(n)
            srcs = {f"net class '{c.name}'": []} if c else {}
            # KiCad: an unset class field inherits Default; with no diff pair width/gap anywhere KiCad has no
            # pair constraint, so the pair keeps the ordinary track width / clearance
            def dpv(attr, plain, last):
                for a in (attr, plain):
                    for x in (c, self.default):
                        if x and getattr(x, a, None):
                            return getattr(x, a), a == attr
                return last, False
            wv, w_set = dpv("dp_width", "track_width", 0.2)
            gv, g_set = dpv("dp_gap", "clearance", 0.25)
            if not (w_set and g_set):
                self.iss.info("rules", f"Differential pair '{name}': KiCad defines no diff pair "
                                       f"{'width' if not w_set else 'gap'}{' / gap' if not (w_set or g_set) else ''}; "
                                       f"Altium pair rule uses the net class track width / clearance")
            w = (self.min_w, wv, None)
            g = (self.min_clear, gv, None)
            unc = None
            partial = []
            # per copper layer, the value of the highest-priority custom rule that reaches it: a rule with a
            # layer condition ('inner' / 'outer') only sets its own layers
            cu = list(self.conv.lm.copper)
            per = {"track_width": {}, "diff_pair_gap": {}}
            for kr in self.custom:
                vp, vn = self.ev(kr.cond, ip), self.ev(kr.cond, inn)
                if vp is False or vn is False:
                    continue
                lays = self.layer_set(kr)
                for con in kr.constraints:
                    k = con.kind
                    if k not in ("track_width", "diff_pair_gap", "diff_pair_uncoupled"):
                        continue
                    v = con.vals
                    if k == "diff_pair_uncoupled":
                        if unc is not None:
                            continue
                        unc = v.get("max")
                    else:
                        targets = [L for L in (cu if lays is None else lays) if L not in per[k]]
                        if not targets:
                            continue
                        pref = v.get("opt", v.get("min", v.get("max")))
                        lo = self.min_w if k == "track_width" else self.min_clear
                        val = (max(v.get("min", lo), lo) if k == "track_width" else v.get("min", lo), pref,
                               v.get("max"))
                        for L in targets:
                            per[k][L] = val
                    srcs.setdefault(f"'{kr.name}'", []).append(k)
                    if None in (vp, vn):
                        partial.append(kr.name)
            fw = {L: per["track_width"].get(L, w) for L in cu}
            fg = {L: per["diff_pair_gap"].get(L, g) for L in cu}
            if cu:
                w, g = fw[cu[0]], fg[cu[0]]
            over = tuple((L, fw[L], fg[L]) for L in cu if fw[L] != w or fg[L] != g)
            key = (round(w[0], 6), round(w[1], 6), w[2], round(g[0], 6), round(g[1], 6), g[2], unc, over)
            grp = groups.setdefault(key, {"pairs": [], "classes": [], "srcs": {}, "partial": set(), "w": w,
                                          "g": g, "unc": unc, "over": over})
            grp["pairs"].append(name)
            if c and c.name not in grp["classes"]:
                grp["classes"].append(c.name)
            for src, ks in srcs.items():
                lst = grp["srcs"].setdefault(src, [])
                lst.extend(k for k in ks if k not in lst)
            grp["partial"].update(partial)
        dflt = self.default
        dkey = None
        if dflt and dflt.dp_width and dflt.dp_gap:
            dkey = (round(self.min_w, 6), round(dflt.dp_width, 6), None, round(self.min_clear, 6),
                    round(dflt.dp_gap, 6), None, None, ())
        for key, grp in groups.items():
            if key == dkey:
                continue
            w, g, unc = grp["w"], grp["g"], grp["unc"]
            scope = " or ".join(f"InDifferentialPair({q(p)})" for p in grp["pairs"])
            nm = "DiffPair_" + "_".join(grp["classes"])
            extra = {}
            if unc is not None:
                extra["uncoupled"] = mm(unc)

            def six(w_, g_):
                return [lim(min(w_[0], w_[1])), mm(w_[1]), mm(w_[2] if w_[2] is not None else max(w_[1], 1.0)),
                        lim(min(g_[0], g_[1])), mm(g_[1]),
                        mm(g_[2] if g_[2] is not None else max(g_[1] * 2, g_[1] + 0.5))]
            if grp["over"]:
                extra["perlayer"] = ";".join(":".join([self.conv.lm.get(L)] + [str(x) for x in six(lw, lg)])
                                             for L, lw, lg in grp["over"] if self.conv.lm.get(L))
            src = "KiCad " + " + ".join(f"{k} ({', '.join(v)})" if v else k for k, v in grp["srcs"].items())
            self.rule("diffpair", nm, src, s1=scope,
                      minw=lim(min(w[0], w[1])), prefw=mm(w[1]), maxw=mm(w[2] if w[2] is not None else max(w[1], 1.0)),
                      mingap=lim(min(g[0], g[1])), prefgap=mm(g[1]),
                      maxgap=mm(g[2] if g[2] is not None else max(g[1] * 2, g[1] + 0.5)), **extra)
            for r in sorted(grp["partial"]):
                self.iss.info("rules", f"Custom rule '{r}': applies only to part of the pair in KiCad (courtyard "
                                       f"condition); Altium's pair rule {nm} applies to the whole pair")
        if dkey:
            self.rule("diffpair", "DiffPairsRouting", "KiCad Default net class diff pair", s1="All",
                      minw=lim(min(self.min_w, dflt.dp_width)), prefw=mm(dflt.dp_width),
                      maxw=mm(max(dflt.dp_width, 1.0)), mingap=lim(min(self.min_clear, dflt.dp_gap)),
                      prefgap=mm(dflt.dp_gap), maxgap=mm(max(dflt.dp_gap * 2, dflt.dp_gap + 0.5)))
        for kr in self.custom:
            for con in kr.constraints:
                if con.kind in ("diff_pair_gap", "diff_pair_uncoupled") and not any(
                        f"'{kr.name}'" in g["srcs"] for g in groups.values()):
                    self.iss.warn("rules", f"Custom rule '{kr.name}': {con.kind} matches no differential pair")
                    self.note(kr.name, "", "diffpair", "matches no differential pair")

    # ------------------------------------------------------------------ length, skew, disallow
    def _custom_misc(self):
        layers_rules: dict[str, dict] = {}
        for kr in reversed(self.custom):          # file order: merged names read naturally
            for con in kr.constraints:
                k = con.kind
                if k in ("clearance", "creepage", "track_width", "via_diameter", "hole_size", "diff_pair_gap",
                         "diff_pair_uncoupled", "zone_connection", "thermal_relief_gap", "thermal_spoke_width"):
                    continue
                try:
                    if k in ("length", "skew"):
                        node = kr.cond
                        if has_fn(node, GEOMETRIC + ("Type", "memberOfFootprint")):
                            raise ValueError(f"{k} is a per-net check; object/position conditions cannot apply")
                        s1, _ = self._scopes(kr, unary=True)
                        if not s1:
                            self.note(kr.name, "", k, "matches nothing on this board")
                            continue
                        v = con.vals
                        if k == "length":
                            self.rule("length", "KiCad_" + kr.name, f"KiCad rule '{kr.name}'", s1=s1,
                                      min=mm(v.get("min", 0.0)), max=mm(v.get("max", BIG * 10)))
                        else:
                            if "max" not in v:
                                raise ValueError("skew without a maximum")
                            if "within_diff_pairs" in con.words:
                                pairs = [nm for nm, p, n in self.pairs
                                         if self.ev(node, self.item(p)) is True and self.ev(node, self.item(n)) is True]
                                s1 = " or ".join(f"InDifferentialPair({q(x)})" for x in pairs) or None
                                if not s1:
                                    continue
                            self.rule("matched", f"KiCad_{kr.name[:48]}_skew", f"KiCad rule '{kr.name}' (skew)", s1=s1,
                                      tol=mm(v["max"]))
                    elif k == "disallow":
                        self._disallow(kr, con, layers_rules)
                    elif k == "edge_clearance":
                        # copper to board edge for the matching objects: Altium's Board Outline Clearance
                        # rule takes a scope like KiCad's condition (one object)
                        if "min" not in con.vals:
                            raise ValueError("edge clearance without a minimum")
                        s1, _ = self._scopes(kr, unary=True)
                        if not s1:
                            self.note(kr.name, "", k, "matches nothing on this board")
                            continue
                        self.rule("outline", "KiCad_" + kr.name, f"KiCad rule '{kr.name}' (edge clearance)",
                                  s1=self.with_layers(s1, self.layer_set(kr)), gap=lim(float(con.vals["min"])))
                    elif k in ("connection_width", "min_resolved_spokes"):
                        self.iss.info("rules", f"Custom rule '{kr.name}': {k} has no Altium rule (Altium checks "
                                               f"polygon necks only while pouring); not converted")
                        self.note(kr.name, "", k, "no Altium equivalent")
                    else:
                        self.iss.warn("rules", f"Custom rule '{kr.name}': constraint '{k}' not converted")
                        self.note(kr.name, "", k, "not converted")
                except ValueError as e:
                    self.iss.warn("rules", f"Custom rule '{kr.name}' ({k}) not converted: {e}")
                    self.note(kr.name, "", k, f"not converted: {e}")
        copper = list(self.conv.lm.copper)
        for s1, ent in layers_rules.items():
            allowed = [l for l in copper if l not in ent["deny"]]
            codes = [self.conv.lm.code[l] for l in allowed if self.conv.lm.code.get(l)]
            nm = "KiCad_" + _common_name(ent["names"])
            self.rule("routinglayers", nm, "KiCad rule " + " + ".join(f"'{n}'" for n in ent["names"]) +
                      " (disallow track)", s1=s1, allow=",".join(codes) or "-")

    def _disallow(self, kr, con, layers_rules):
        words = [w.lower() for w in con.words]
        layers = self.layer_set(kr)
        for w in words:
            if w == "track":
                node, _ = strip_type(kr.cond, "track")
                if has_fn(node, GEOMETRIC + ("Type", "memberOfFootprint")):
                    raise ValueError("disallow track with object/position conditions")
                a, bp = split_ab(node)
                if bp != TRUE:
                    raise ValueError("B-object terms")
                s1 = self.tr.to_query(a)
                if not s1:
                    self.note(kr.name, "", "disallow", "matches nothing on this board")
                    continue
                ent = layers_rules.setdefault(s1, {"deny": set(), "names": []})
                ent["deny"].update(layers if layers is not None else self.conv.lm.copper)
                ent["names"].append(kr.name)
            elif w in ("via", "through_via", "micro_via", "blind_via", "buried_via"):
                node, _ = strip_type(kr.cond, "via")
                if has_fn(node, GEOMETRIC + ("Type", "memberOfFootprint")):
                    raise ValueError("disallow via with object/position conditions")
                if layers is not None:
                    self.iss.info("rules", f"Custom rule '{kr.name}': layer restriction ignored (Altium counts vias "
                                           f"per net)")
                a, bp = split_ab(node)
                s1 = self.tr.to_query(a)
                if not s1:
                    continue
                self.rule("maxvias", "KiCad_" + kr.name, f"KiCad rule '{kr.name}' (disallow {w})", s1=s1, limit=0)
            else:
                raise ValueError(f"disallow {w} has no Altium rule")

    # ------------------------------------------------------------------ polygon connect
    def _polyconnect(self):
        b = self.b

        def style_of(v):
            return {0: "none", 1: "relief", 2: "direct", 3: "relief"}.get(v, "relief")

        polys = list(getattr(self.conv, "polygon_names", []))
        zstyle = {"yes": "direct", "no": "none", "thermal": "relief", "thru_hole_only": "relief"}
        settings = []
        for z, nm in polys:
            st = zstyle.get(z.connect_pads, "relief")
            settings.append((st, round(z.thermal_gap, 6), round(z.thermal_bridge_width, 6)))
        if settings:
            counts = {}
            for s in settings:
                counts[s] = counts.get(s, 0) + 1
            dflt = max(counts, key=lambda s: (counts[s], s == ("relief", 0.5, 0.5)))
        else:
            dflt = ("relief", 0.5, 0.5)

        # custom zone_connection rules -> evaluated per pad below, emitted as rules
        customs = []
        for kr in self.custom:
            cons = {c.kind: c for c in kr.constraints}
            if not any(k in cons for k in ("zone_connection", "thermal_relief_gap", "thermal_spoke_width")):
                continue
            zc = cons.get("zone_connection")
            st = None
            if zc is not None:
                w = (zc.words or ["thermal_reliefs"])[0].lower()
                st = {"solid": "direct", "thermal_reliefs": "relief", "none": "none"}.get(w)
                if st is None:
                    self.iss.warn("rules", f"Custom rule '{kr.name}': zone_connection '{w}' not converted")
                    continue
            gap = cons["thermal_relief_gap"].vals.get("min") if "thermal_relief_gap" in cons else None
            spoke = cons["thermal_spoke_width"].vals.get("min") if "thermal_spoke_width" in cons else None
            vias_only = strip_type(kr.cond, "via")[1]
            if vias_only and st == "direct":
                self.iss.info("rules", f"Custom rule '{kr.name}': vias always connect solid to KiCad zones; covered by "
                                       f"PolygonConnect_Vias")
                self.note(kr.name, "PolygonConnect_Vias", "polyconnect", "vias always solid in KiCad")
                continue
            customs.append((kr, st, gap, spoke))

        # pad / footprint local overrides (KiCad: pad > footprint > custom rule > zone)
        pad_zc: dict[tuple, list[str]] = {}
        comp_zc: dict[tuple, list[str]] = {}
        for fp in b.footprints:
            if fp.zone_connect is not None:
                comp_zc.setdefault((fp.zone_connect, fp.thermal_gap, fp.thermal_width), []).append(fp.ref)
            for pd in fp.pads:
                if pd.zone_connect is None and pd.thermal_gap is None and pd.thermal_width is None:
                    continue
                zc = pd.zone_connect if pd.zone_connect is not None else fp.zone_connect
                if zc is not None and self._custom_style(customs, fp.ref, pd) == style_of(zc) == "direct":
                    continue          # a custom rule already gives this pad the same solid connection
                lst = pad_zc.setdefault((zc, pd.thermal_gap, pd.thermal_width), [])
                if f"{fp.ref}-{pd.number}" not in lst:
                    lst.append(f"{fp.ref}-{pd.number}")
        ents = []
        for i, ((zc, tg, tw), mems) in enumerate(sorted(pad_zc.items(), key=lambda kv: str(kv[0]))):
            n = f"Convi_PadZoneConnect_{i + 1}"
            self.ir.cmd("CLASS", kind="pad", name=n)
            for m in mems:
                self.ir.cmd("CLASSM", name=n, member=m)
            ents.append((n, f"KiCad pad zone connection override ({_list(mems)})", "All",
                         f"InPadClass({q(n)})", style_of(zc if zc is not None else 1), tg or dflt[1], tw or dflt[2]))
        for i, ((zc, tg, tw), mems) in enumerate(sorted(comp_zc.items(), key=lambda kv: str(kv[0]))):
            n = f"Convi_FpZoneConnect_{i + 1}"
            self.ir.cmd("CLASS", kind="comp", name=n)
            for m in mems:
                self.ir.cmd("CLASSM", name=n, member=m)
            ents.append((n, f"KiCad footprint zone connection override ({_list(mems)})", "All",
                         f"InComponentClass({q(n)})", style_of(zc), tg or dflt[1], tw or dflt[2]))
        for kr, st, gap, spoke in customs:
            try:
                s1, _ = self._scopes(kr, unary=True)
            except ValueError as e:
                self.iss.warn("rules", f"Custom rule '{kr.name}' not converted: {e}")
                self.note(kr.name, "", "polyconnect", f"not converted: {e}")
                continue
            s1 = self.with_layers(s1, self.layer_set(kr)) if s1 else None
            if not s1:
                self.note(kr.name, "", "polyconnect", "matches nothing on this board")
                continue
            ents.append(("KiCad_" + kr.name, f"KiCad rule '{kr.name}'", "All", s1, st or "relief",
                         gap if gap is not None else dflt[1], spoke if spoke is not None else dflt[2]))
        # KiCad never puts thermal reliefs on vias
        if dflt[0] != "direct" or any(s[0] != "direct" for s in settings):
            ents.append(("PolygonConnect_Vias", "KiCad connects vias to zones solid", "All", "IsVia", "direct",
                         dflt[1], dflt[2]))
        groups: dict[tuple, list[str]] = {}
        smd: dict[tuple, list[str]] = {}
        for (z, nm), s in zip(polys, settings):
            if z.connect_pads == "thru_hole_only":
                smd.setdefault((s[1], s[2]), []).append(nm)
            if s != dflt:
                groups.setdefault(s, []).append(nm)
        for (tg, tw), nms in smd.items():
            ents.append((f"PolygonConnect_SMD_{nms[0]}" if len(nms) == 1 else "PolygonConnect_SMD",
                         f"KiCad zones connecting through-hole pads only ({_list(nms)})",
                         " or ".join(f"InNamedPolygon({q(n)})" for n in nms), "IsSMTPad", "none", tg, tw))
        for s, nms in groups.items():
            nm = f"PolygonConnect_{nms[0]}" if len(nms) == 1 else \
                f"PolygonConnect_{'Solid' if s[0] == 'direct' else 'None' if s[0] == 'none' else 'Relief'}"
            ents.append((nm, f"KiCad zone connection of {_list(nms)}",
                         " or ".join(f"InNamedPolygon({q(n)})" for n in nms), "All", s[0], s[1], s[2]))
        ents.append(("PolygonConnect", "KiCad zone connection (most zones" +
                     (f": {_list([nm for (z, nm), s in zip(polys, settings) if s == dflt])}" if polys else "") + ")",
                     "All", "All", dflt[0], dflt[1], dflt[2]))
        # KiCad's spoke angle is per pad: thermal_bridge_angle, else 45 degrees for a round pad (a custom pad
        # with a round anchor too) and 90 for the others. Altium's is per rule: the pads with the less common
        # angle get a pad class and a copy of each relief rule just above it
        zone_nets = {z.net for z, _ in polys if z.net}
        by_angle: dict[int, list[str]] = {}
        for fp in b.footprints:
            for pd in fp.pads:
                if pd.net not in zone_nets:
                    continue
                a = pd.thermal_angle
                if a is None:
                    a = 45 if pd.shape == "circle" or (pd.shape == "custom" and pd.anchor == "circle") else 90
                a = 45 if round(float(a)) % 90 == 45 else 90
                by_angle.setdefault(a, []).append(f"{fp.ref}-{pd.number}")
        main = max(by_angle, key=lambda a: (len(by_angle[a]), a == 90)) if by_angle else 90
        other = [a for a in by_angle if a != main] if any(e[4] == "relief" for e in ents) else []
        if other:
            oa = other[0]
            cn = f"Convi_ThermalAngle_{oa}"
            self.ir.cmd("CLASS", kind="pad", name=cn)
            for m in by_angle[oa]:
                self.ir.cmd("CLASSM", name=cn, member=m)
        for name, src, s1, s2, st, gap, width in ents:
            if other and st == "relief":
                s2o = f"InPadClass({q(cn)})" if s2 == "All" else f"({s2}) and InPadClass({q(cn)})"
                self.rule("polyconnect", (name + f"_{oa}deg")[:60],
                          src + f"; spokes at {oa} degrees for {len(by_angle[oa])} pads (KiCad's default for round "
                                f"pads is 45, for others 90)", s1=s1, s2=s2o, style=st, gap=mm(gap),
                          width=mm(width), entries=4, angle=oa)
            self.rule("polyconnect", name, src, s1=s1, s2=s2, style=st, gap=mm(gap), width=mm(width),
                      entries=4, angle=main)

    def _custom_style(self, customs, ref, pd):
        it = self.item(pd.net or "", "pad", ref)
        for kr, st, gap, spoke in customs:
            v = self.ev(kr.cond, it)
            if v is None:
                return None
            if v:
                return st
        return None

    # ------------------------------------------------------------------ manufacturing / board setup
    def _manufacturing(self):
        b = self.b
        brd = self.brd
        max_hole = 6.35
        for fp in b.footprints:
            for pd in fp.pads:
                if pd.drill:
                    max_hole = max(max_hole, pd.drill.w, pd.drill.h)
        self.rule("holesize", "HoleSize", "KiCad board minimum through-hole", s1="All", min=lim(self.min_hd),
                  max=mm(max_hole + 0.01))
        if brd.get("min_hole_to_hole") is not None:
            self.rule("holetohole", "HoleToHoleClearance", "KiCad board minimum hole to hole", s1="All", s2="All",
                      gap=lim(float(brd["min_hole_to_hole"])))
        if brd.get("min_via_annular_width") is not None:
            self.rule("annular", "MinimumAnnularRing", "KiCad board minimum annular width", s1="All",
                      min=lim(float(brd["min_via_annular_width"])))
        self.rule("maskexp", "SolderMaskExpansion", "KiCad board solder mask expansion", s1="All",
                  exp=mm(float(b.setup.get("pad_to_mask_clearance", 0.0) or 0.0)))
        self.rule("pasteexp", "PasteMaskExpansion", "KiCad board paste clearance", s1="All",
                  exp=mm(float(b.setup.get("pad_to_paste_clearance", 0.0) or 0.0)))
        self.rule("sliver", "MinimumSolderMaskSliver", "KiCad board solder mask minimum web", s1="All", s2="All",
                  min=mm(float(b.setup.get("solder_mask_min_width", 0.0) or 0.0)))
        silk = float(brd.get("min_silk_clearance", 0.0) or 0.0)
        # KiCad's "silkscreen clipped by solder mask" flags overlap only: min_silk_clearance is silk to silk
        # (verified: KiCad DRC passes a B.Silk text 0.026 mm from a mask opening with min_silk_clearance
        # 0.1 on a test board).  A 0.1 mm Altium rule reported what KiCad accepts.
        self.rule("silkmask", "SilkToSolderMaskClearance", "KiCad silkscreen clipped by solder mask check "
                  "(overlap)", s1="All", s2="All", gap=0)
        self.rule("silksilk", "SilkToSilkClearance", "KiCad board minimum silkscreen clearance", s1="All", s2="All",
                  gap=mm(silk))
        # KiCad checks courtyard overlap, not 3D bodies: no body clearance required
        self.rule("compclear", "ComponentClearance", "KiCad courtyard overlap check", s1="All", s2="All", gap=0,
                  vgap=0)
        # NPTH pads no bigger than their drill have no copper (KiCad flashes none): KiCad's edge clearance
        # and short checks never see them, so mouse bites may cut the edge (seen on a test board: 16 outline + 12
        # short violations between the panel tabs' holes and the board cutouts).  Altium counts such a pad as
        # copper; it is left out when the board has no NPTH pad with real copper.
        npth_cu = any(pd.type == "np_thru_hole" and pd.drill is not None
                      and min(pd.size) > min(pd.drill.w, pd.drill.h or pd.drill.w) + 1e-6
                      and any(l.endswith(".Cu") for l in pd.layers)
                      for fp in self.b.footprints for pd in fp.pads)
        edge_s1 = "OnCopper" if npth_cu else "OnCopper And Not (IsPad And (PadIsPlated = 'False'))"
        if brd.get("min_copper_edge_clearance") is not None:
            self.rule("outline", "BoardOutlineClearance", "KiCad board copper to edge clearance", s1=edge_s1,
                      gap=lim(float(brd["min_copper_edge_clearance"])))
        # Altium's short-circuit check also takes board cutouts as objects; KiCad keeps copper off a slot with
        # the edge clearance only (above)
        cut = bool(getattr(self.conv, "board_cutouts", 0))
        self.rule("short", "ShortCircuit", "KiCad shorting items check" + (" (board cutouts: edge clearance only)"
                                                                          if cut else ""),
                  s1="Not IsBoardCutoutRegion" if cut else "All", s2="Not IsBoardCutoutRegion" if cut else "All")

    def _unsupported_board_rules(self):
        brd = self.brd
        skipped = []
        for k, label in (("min_hole_clearance", "hole to copper clearance"),
                         ("min_text_height", "minimum text height"), ("min_text_thickness", "minimum text thickness"),
                         ("min_resolved_spokes", "minimum thermal spokes")):
            v = brd.get(k)
            if v and k == "min_hole_clearance":
                done = getattr(self, "hole_clear_pads", 0)
                self.note(f"board setup {k}", "Convi_HoleClearance_*" if done else "", "clearance",
                          f"{done} pad(s) whose copper ring is thinner than the hole clearance get a pad-class "
                          f"clearance; elsewhere the copper clearance is the larger" if done else
                          "every pad's and via's copper clearance already covers it")
                continue
            if v:
                skipped.append(f"{label} {v:g}")
                self.note(f"board setup {k}", "", "-", "no Altium rule")
        if skipped:
            self.iss.info("rules", "KiCad board constraints without an Altium rule (check manually): " +
                          ", ".join(skipped))
