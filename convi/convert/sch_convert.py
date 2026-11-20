"""KiCad schematic hierarchy -> Convi IR (Altium SchDoc per sheet instance + SchLib).

Coordinates in the IR are mils (floats -> milli-mils).  Altium sheet frame is Y up:
    X = x_mm / 0.0254,  Y = (sheet_height_mm - y_mm) / 0.0254
"""
from __future__ import annotations

import base64
import copy
import hashlib
import math
import re
import string
from dataclasses import dataclass, field
from collections import defaultdict
from pathlib import Path

from ..altium.ir import IRWriter
from ..geom import arc_from_3pts_yup
from ..kicad.common import Issues, TextEffects, unescape_name
from .markers import marker_kind
from ..kicad.sch import (Label, LibPin, LibShape, LibSymbol, Schematic, Sheet, SymbolInstance,
                         parse_schematic)
from ..kicad.sch import compose

MIL = 0.0254
TEXT_VAR_RE = re.compile(r"\$\{([^}]+)\}")

# KiCad default colours -> Altium BGR integers
def rgb(r, g, b) -> int:
    return int(r) + int(g) * 256 + int(b) * 65536


COL_BODY = rgb(132, 0, 0)
COL_FILL = rgb(255, 255, 194)


def m(v_mm: float) -> float:
    """mm -> mil via KiCad's integer nanometres (identical inputs always give identical outputs)."""
    return round(round(v_mm * 1e6) / 25400.0, 4)


def page_height_mil(h_mm: float) -> float:
    """Y-flip reference: page height rounded up to 50 mil so KiCad's 50 mil grid stays on Altium's grid."""
    return math.ceil(m(h_mm) / 50.0) * 50.0


def altium_uid(*parts: str) -> str:
    """Deterministic 8-letter Altium unique id."""
    h = hashlib.sha1("|".join(parts).encode()).digest()
    letters = string.ascii_uppercase
    return "".join(letters[b % 26] for b in h[:8])


def overbar(text: str) -> str:
    """KiCad ~{ABC} overbar -> Altium A\\B\\C\\ notation."""
    def rep(mt):
        return "".join(ch + "\\" for ch in mt.group(1))
    t = re.sub(r"~\{([^}]*)\}", rep, text)
    return t


def plain_markup(text: str) -> str:
    """KiCad subscript / superscript markup ('20mV_{p-p}', 'V_{ACT}') as plain text: Altium texts and pin
    names have neither, and the braces would show."""
    return re.sub(r"([_^])\{([^{}]*)\}", r"\1\2", text or "")


_GROUP_BRACE = re.compile(r"(?<![_^~])\{")


def split_group_bus(text: str):
    """KiCad group bus name 'PREFIX{ALIAS or MEMBERS}' -> (prefix, inner) or None.  Braces of text markup
    ('V_{FAN}', '~{CE}', 'PCIe_{x4}') are not group braces; the group brace is the last one that is not
    markup and the name ends with its closing brace ('PCIe_{x4}{PCIe}' -> ('PCIe_{x4}', 'PCIe'))."""
    t = text or ""
    if not t.endswith("}"):
        return None
    starts = [mt.start() for mt in _GROUP_BRACE.finditer(t)]
    if not starts:
        return None
    i = starts[-1]
    inner = t[i + 1:-1]
    if "{" in inner or "}" in inner:
        return None
    return t[:i], inner


def font_pt(e: TextEffects | None) -> int:
    """KiCad text size (cap height, mm) -> Altium schematic font points (1 pt ~ 10 mil em, Arial cap 0.716 em)."""
    h = e.size[0] if e and e.size else 1.27
    return max(4, int(round(h / MIL / 0.716 / 10.0)))


def frame_text(text: str) -> str:
    """Altium text frames break lines on CR LF only; a bare LF (KiCad) is dropped and the
    words on both sides run together ("CP2102N is\nunpowered" -> "isunpowered")."""
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")


# house schematic style (as in hand-drawn Altium projects): grounds = power ground, earth =
# earth, supplies = bar; KiCad global labels kept as power ports use the circle (Convi's mapping);
# wires in Altium's default colour and width
HOUSE_POWER_STYLES = {"gnd", "earth", "bar", "circle"}


def check_house_style(lines, issues, wire_class_colours: bool = False) -> None:
    """Report any converted schematic object drawn outside the house style.  Coloured wires
    are expected (info, not a warning) when the KiCad project gives its net classes colours."""
    bad_power, coloured = {}, 0
    for ln in lines:
        f = ln.split("\t")
        if f[0] == "POWER":
            kv = dict(x.split("=", 1) for x in f[1:] if "=" in x)
            st = kv.get("style", "")
            if st not in HOUSE_POWER_STYLES:
                bad_power[kv.get("text", "?")] = st
        elif f[0] == "WIRE" and any(x.startswith(("color=", "lw=")) for x in f[1:]):
            coloured += 1
    if bad_power:
        issues.warn("sch", "Power ports outside the house style: " +
                    ", ".join(f"{k} ({v})" for k, v in sorted(bad_power.items())))
    if coloured and wire_class_colours:
        issues.info("sch", f"{coloured} wire(s) drawn in the KiCad net class colour/width: the KiCad project "
                           "defines net class schematic colours (house default: Altium's default wire)")
    elif coloured:
        issues.warn("sch", f"{coloured} wire(s) not in Altium's default colour/width (house style)")


def line_size(w_mm: float | None) -> int:
    """Altium TSize: 0 smallest (hairline), 1 small (10 mil), 2 medium (30 mil), 3 large (50 mil).

    KiCad symbol strokes are 0.15-0.25 mm (body) and 0.508 mm (capacitor plates).  Mapping
    0.25 mm to Medium and 0.508 mm to Large drew 30/50 mil lines: capacitor plates became
    solid bars that hid their own designator/value.  Hand-drawn Altium libraries typically use Small
    for practically every symbol line, so everything up to 0.6 mm maps to Small."""
    if not w_mm or w_mm <= 0.6:
        return 1
    if w_mm <= 1.0:
        return 2
    return 3


def _page_key(page: str):
    try:
        return (0, float(page), "")
    except (TypeError, ValueError):
        return (1, 0.0, str(page))


@dataclass
class SheetInstance:
    path: tuple                 # sheet uuids from root (root = ())
    sch: Schematic
    name: str
    page: str = ""
    sheet: Sheet | None = None  # the parent's sheet object (None for root)
    parent: "SheetInstance | None" = None
    out_name: str = ""
    children: list = field(default_factory=list)

    @property
    def path_str(self) -> str:
        return "/" + "/".join(self.path) if self.path else "/"


@dataclass
class SymDef:
    name: str
    lib: LibSymbol
    signature: str


class SchConverter:
    def __init__(self, root_path: Path, issues: Issues, project=None, footprint_of: dict | None = None,
                 lib_name="Convi.SchLib", pcblib_name="Convi.PcbLib", global_labels="auto", net_map=None):
        self.root_path = Path(root_path)
        self.issues = issues
        self.project = project
        self.footprint_of = footprint_of or {}     # KiCad symbol path -> (altium pattern, pcb ref)
        self.lib_name = lib_name
        self.pcblib_name = pcblib_name
        self.global_labels = global_labels          # auto / power / port (resolved in load())
        self.hierarchy_mode = 2
        self.allow_port_net_names = False
        self.append_sheet_numbers = False     # PrjPcb AppendSheetNumberToLocalNets (see plan_local_net_names)
        # sheet templates (.SchDot): one customer family, the smallest size the sheet fits on
        # (not below template_min).  Used templates are copied into the output project (the
        # sheets' template) and, as .SchDoc, into the work folder for the builder to read.
        self.template_family: list = []
        self.template_min = "A3"
        self.template_out: Path | None = None       # output project folder
        self.template_work: Path | None = None      # _convi work folder
        self.template_of: dict = {}                  # SchDoc name -> Template
        self.lib_footprints: dict = {}               # SchLib symbol name -> {footprint: uses}
        self.markers: dict = {}                      # designator -> testpoint/mountinghole/fiducial
        self.bom_rows: dict = {}                     # designator -> KiCad BOM data (verify.compare_bom)
        self._fit_checked: set = set()
        self._tpl_copied: set = set()
        self.cache: dict[Path, Schematic] = {}
        self.instances: list[SheetInstance] = []
        self.symdefs: dict[str, SymDef] = {}
        self.sym_of_key: dict = {}
        self.uid_path_of: dict[str, str] = {}      # KiCad symbol instance path -> Altium '\UID\UID' path
        self.libref_of: dict[str, str] = {}        # KiCad symbol instance path -> SchLib name
        self.ref_of: dict[str, str] = {}           # KiCad symbol instance path -> schematic designator
        self.not_on_board: set = set()
        self.text_vars = dict(project.text_vars) if project else {}
        self.root = None
        self.pin_spots: dict = {}          # (ref, pin) -> (SchDoc name, (x_mil, y_mil)) of the hot spot
        self._cur_pin_ctx = None
        self._apart: dict | None = None    # SchLib name -> stacked pin numbers KiCad keeps apart
        self.glue: dict = {}               # SchDoc name -> [(x, y, net name)]

    # ------------------------------------------------------------------ hierarchy
    @property
    def wire_class_colours(self) -> bool:
        """The KiCad project gives at least one net class a schematic wire colour."""
        return any(getattr(c, "sch_color", None) for c in (self.project.netclasses if self.project else []))

    def load(self):
        root_sch = self._sch(self.root_path)
        # the root's page number as KiCad numbers it (sheet_instances path "/"; some projects number it 0)
        root_page = next((pg for pth, pg in getattr(root_sch, "sheet_instances", []) if pth == "/" and pg), "1")
        self.root = SheetInstance(path=(), sch=root_sch, name=self.root_path.stem, page=root_page)
        self.instances = [self.root]
        self._walk(self.root, depth=0)
        # names: single-use files keep their name, multi-use get the sheet name appended
        used: dict[Path, int] = {}
        for inst in self.instances:
            used[inst.sch.path] = used.get(inst.sch.path, 0) + 1
        taken = set()
        for inst in self.instances:
            stem = inst.sch.path.stem
            if used[inst.sch.path] > 1 and inst.sheet is not None:
                base = f"{stem}_{re.sub(r'[^A-Za-z0-9_.-]+', '_', inst.name)}"
            else:
                base = stem
            nm = base
            k = 2
            while nm.lower() in taken:
                nm = f"{base}_{k}"
                k += 1
            taken.add(nm.lower())
            inst.out_name = nm + ".SchDoc"
        self._resolve_label_mode()
        self._merge_split_units()
        snapped = []
        for sch in {id(i.sch): i.sch for i in self.instances}.values():
            snapped += [(sch.path.name, r, d) for r, d in self._snap_off_grid(sch)]
        if snapped:
            ex = ", ".join(f"{r} by {d:.3f} mm" for _, r, d in snapped[:4]) + (" ..." if len(snapped) > 4 else "")
            self.issues.info("sch", f"{len(snapped)} symbol(s) sat off KiCad's 50 mil connection grid (KiCad's ERC "
                                    f"does not flag them, Altium's does): moved onto it along their wires ({ex})")
        multi = [p for p, c in used.items() if c > 1]
        if multi:
            self.issues.info("sch", f"{len(multi)} sheet file(s) are used more than once (repeated hierarchy); "
                                    "each instance becomes its own SchDoc so designators match the PCB exactly")

    def _resolve_label_mode(self):
        """Choose how KiCad global labels and the Altium net scope are written.

        KiCad: global label = net on every sheet, local label = this sheet only, hierarchical label =
        sheet pin.  Altium (house style, as in hand-drawn projects): ports for signals between
        sheets, power ports only for supply symbols, net labels local.
          * no hierarchical labels / sheet pins anywhere -> global labels become PORTS and the scope
            is Automatic (ports present -> flat: ports global, net labels local) or Flat when the
            design has no port at all (Automatic would then make net labels global).  Ports must
            name nets (AllowPortNetNames=1) so net names stay the KiCad names.
          * hierarchical labels / sheet pins used -> ports are needed for the sheet pins, so global
            labels stay POWER PORTS and the scope stays Hierarchical (the only exact mapping).  KiCad
            names a net after its hierarchical label when no local label is on it, so ports name nets
            here too (AllowPortNetNames=1; net labels still win, as KiCad's local labels do).  Without
            it Altium gives such nets NetR32_2-style names (e.g. TACH, ISENSE...).  Verified in
            Altium; AllowSheetEntryNetNames is not needed.
        """
        hier = any(lb.kind == "hierarchical_label" for s in self.cache.values() for lb in s.labels) or \
            any(sh.pins for s in self.cache.values() for sh in s.sheets)
        has_global = any(lb.kind == "global_label" for s in self.cache.values() for lb in s.labels)
        mode = self.global_labels
        if mode == "auto":
            mode = "power" if hier else "port"
        elif mode == "port" and hier:
            self.issues.warn("sch", "Global labels as ports with hierarchical sheet pins in the design: "
                                    "global nets are only joined through the sheet entries; using power "
                                    "ports instead")
            mode = "power"
        self.global_labels = mode
        self.hierarchy_mode = (0 if has_global else 1) if mode == "port" else 2  # Automatic/Flat, Hierarchical
        self.allow_port_net_names = True
        self.issues.info("sch", "KiCad global labels -> " + ("ports" if mode == "port" else "power ports")
                         + ", net identifier scope " + {0: "Automatic", 1: "Flat", 2: "Hierarchical"}[
                             self.hierarchy_mode])

    def _snap_off_grid(self, sch: Schematic) -> list:
        """Move symbols whose pins are off the 50 mil (1.27 mm) grid onto it, with the wire ends, labels,
        junctions and no-connect flags on those pins.  KiCad accepts them (e.g. a TestPoint_Small on a
        wire at x = 100.3633 mm), Altium's ERC reports every one as an off-grid object.  Only a move that
        keeps each wire on its own line is made, so no connection changes; anything else stays put."""
        G = 1.27

        def on(v):
            return abs(v / G - round(v / G)) < 1e-4

        def key(p):
            return round(p[0], 4), round(p[1], 4)

        sym_pins, pins_at = [], {}
        for sym in sch.symbols:
            ls = self._lib_for(sch, sym)
            if ls is None:
                continue
            pts = [sym.to_sheet(p.pos) for p in ls.pins if p.unit in (0, sym.unit)
                   and p.style in (0, sym.body_style)]
            sym_pins.append((sym, pts))
            for q in pts:
                pins_at.setdefault(key(q), set()).add(id(sym))
        points = [q for _, pts in sym_pins for q in pts] + [q for w in sch.wires for q in w.pts] + \
            [lb.pos for lb in sch.labels] + [pos for pos, _ in sch.junctions] + list(sch.no_connects)
        blocked = set()
        for sh in sch.sheets:
            blocked.update(key(pn.pos) for pn in sh.pins)
        for pos, size in sch.bus_entries:
            blocked.update((key(pos), key((pos[0] + size[0], pos[1] + size[1]))))
        for bw in sch.buses:
            blocked.update(key(q) for q in bw.pts)
        done = []
        for sym, pts in sym_pins:
            if not pts and not (on(sym.pos[0]) and on(sym.pos[1])):
                # a symbol without pins (e.g. M.2 mechanical parts) connects nothing: its origin
                # goes onto the grid, its graphics and fields move with it
                d = (round(sym.pos[0] / G) * G - sym.pos[0], round(sym.pos[1] / G) * G - sym.pos[1])
                sym.pos = (sym.pos[0] + d[0], sym.pos[1] + d[1])
                for f in sym.fields:
                    f.pos = (f.pos[0] + d[0], f.pos[1] + d[1])
                done.append((sym.field("Reference") or sym.lib_id, math.hypot(*d)))
                continue
            if not pts or all(on(x) and on(y) for x, y in pts):
                continue
            d = (round(pts[0][0] / G) * G - pts[0][0], round(pts[0][1] / G) * G - pts[0][1])
            if any(not (on(x + d[0]) and on(y + d[1])) for x, y in pts):
                continue
            ok, ends = True, []
            olds = {key(q) for q in pts}
            for q in pts:
                k = key(q)
                if len(pins_at.get(k, ())) > 1 or k in blocked:
                    ok = False
                    break
                for w in sch.wires:
                    for i in range(len(w.pts) - 1):
                        a, b = w.pts[i], w.pts[i + 1]
                        vx, vy = b[0] - a[0], b[1] - a[1]
                        cross = (q[0] - a[0]) * vy - (q[1] - a[1]) * vx
                        if abs(cross) > 1e-6 * max(1.0, math.hypot(vx, vy)):
                            continue
                        t = ((q[0] - a[0]) * vx + (q[1] - a[1]) * vy) / max(vx * vx + vy * vy, 1e-12)
                        if t < -1e-6 or t > 1 + 1e-6:
                            continue
                        if abs(vx * d[1] - vy * d[0]) > 1e-9:
                            ok = False          # moving across the wire would leave it
                        if key(a) == k:
                            ends.append((w, i))
                        if key(b) == k:
                            ends.append((w, i + 1))
                # no other connection point may lie between the old and the new place
                n = (q[0] + d[0], q[1] + d[1])
                for r in points:
                    if key(r) in olds:
                        continue
                    if abs((r[0] - q[0]) * d[1] - (r[1] - q[1]) * d[0]) < 1e-9 and \
                            -1e-9 <= (r[0] - q[0]) * d[0] + (r[1] - q[1]) * d[1] <= d[0] ** 2 + d[1] ** 2 + 1e-9:
                        ok = False
                if not ok:
                    break
            if not ok:
                continue

            def mv(p):
                return p[0] + d[0], p[1] + d[1]

            seen = set()
            for w, i in ends:
                if (id(w), i) in seen:
                    continue
                seen.add((id(w), i))
                w.pts[i] = mv(w.pts[i])
            for lb in sch.labels:
                if key(lb.pos) in olds:
                    lb.pos = mv(lb.pos)
            sch.junctions = [(mv(pos) if key(pos) in olds else pos, dia) for pos, dia in sch.junctions]
            sch.no_connects = [mv(pos) if key(pos) in olds else pos for pos in sch.no_connects]
            sym.pos = mv(sym.pos)
            for f in sym.fields:
                f.pos = mv(f.pos)
            done.append((sym.field("Reference") or sym.lib_id, math.hypot(*d)))
        return done

    def plan_local_net_names(self, net_names) -> dict:
        """KiCad keeps two local nets with the same label apart by their sheet path ('/Left Board/SDA',
        '/Right Board/SDA'); Altium joins nets of the same name across sheets (seen on a two-board panel: 44 nets
        of the left and right board shorted, on the board too).  When the KiCad nets have such a collision,
        Altium's 'Append sheet number to local nets' keeps them apart ('SDA_3', 'SDA_6'); every local
        net then carries its sheet number, so it is only switched on when needed.  Verified in Altium
        2026-11-19 (Name nets hierarchically did not separate them)."""
        by = defaultdict(set)
        for n in net_names or ():
            if not n or n.startswith(("Net-(", "unconnected-(")):
                continue
            by[n.rstrip("/").split("/")[-1]].add(n)
        clash = {k: sorted(v) for k, v in by.items() if len(v) > 1}
        self.append_sheet_numbers = bool(clash)
        if clash:
            self.issues.info("sch", f"{len(clash)} net name(s) are used for different KiCad nets on different "
                                    "sheets (e.g. " + ", ".join(" / ".join(v) for v in list(clash.values())[:3])
                             + "): Altium would join them, so local nets get their sheet number appended "
                               "(Project Options: Append Sheet Numbers to Local Nets)")
        return clash

    def _sch(self, path: Path) -> Schematic:
        path = Path(path).resolve()
        if path not in self.cache:
            self.cache[path] = parse_schematic(path, self.issues)
        return self.cache[path]

    def _walk(self, inst: SheetInstance, depth: int):
        if depth > 32:
            self.issues.error("sch", "Sheet hierarchy is too deep (recursive sheets?)")
            return
        for sh in inst.sch.sheets:
            f = inst.sch.path.parent / sh.file
            if not f.is_file():
                self.issues.missing("sch", f"Sub-sheet file not found: {sh.file} (sheet '{sh.name}')", str(f))
                continue
            child_path = inst.path + (sh.uuid,)
            page = ""
            for p, pg in sh.instances:
                parts = [x for x in p.split("/") if x]
                if parts[1:] == list(inst.path) or not page:
                    page = pg
            child = SheetInstance(path=child_path, sch=self._sch(f), name=sh.name, page=page, sheet=sh, parent=inst)
            inst.children.append(child)
        # KiCad page order (then sheet name) defines the order of documents and sheet symbols
        inst.children.sort(key=lambda c: (_page_key(c.page), c.name.lower()))
        for child in inst.children:
            self.instances.append(child)
            self._walk(child, depth + 1)

    # ------------------------------------------------------------------ symbol instance data
    def _sym_ref_unit(self, inst: SheetInstance, sym: SymbolInstance) -> tuple[str, int]:
        """Reference and unit of a symbol in a given sheet instance."""
        want = "/" + "/".join((self.root.sch.uuid,) + inst.path) if self.root.sch.uuid else None
        cands = []
        for pth, ref, unit in sym.instances:
            cands.append((pth, ref, unit))
            parts = [p for p in pth.split("/") if p]
            if parts[1:] == list(inst.path) or (not inst.path and len(parts) <= 1):
                return ref, unit
        # legacy symbol_instances in root
        full = "/" + "/".join(list(inst.path) + [sym.uuid])
        for pth, ref, unit, *_ in self.root.sch.legacy_symbol_instances:
            if pth == full:
                return ref, unit
        if cands:
            return cands[0][1], cands[0][2]
        return sym.field("Reference", "?"), sym.unit

    @staticmethod
    def _is_legacy_power(ls: LibSymbol, ref: str) -> bool:
        """Old-style KiCad power symbols: '#' reference, only hidden power_in pins (global net by pin name)."""
        if not ref.startswith("#") or not ls.pins:
            return False
        return all(p.etype == "power_in" and (p.hidden or p.length == 0) for p in ls.pins)

    def _canonical_uuid(self, inst: SheetInstance, ref: str, sym: SymbolInstance) -> str:
        key = id(inst)
        cache = self.__dict__.setdefault("_canon", {})
        if key not in cache:
            best: dict[str, tuple] = {}
            for s in inst.sch.symbols:
                r, u = self._sym_ref_unit(inst, s)
                # the unit the KiCad footprint links to wins; otherwise the lowest unit
                linked = self._kicad_sym_path(inst, s) in self.footprint_of
                rank = (0 if linked else 1, u)
                if r not in best or rank < best[r][0]:
                    best[r] = (rank, s.uuid)
            cache[key] = {r: v[1] for r, v in best.items()}
        if ref.endswith("?") or ref.startswith("#"):
            return sym.uuid
        return cache[key].get(ref, sym.uuid)

    def _kicad_sym_path(self, inst: SheetInstance, sym: SymbolInstance) -> str:
        return "/" + "/".join(list(inst.path) + [sym.uuid])

    # ------------------------------------------------------------------ library symbols
    def _merge_split_units(self):
        """One library component for all units of a multi-unit part.  KiCad lets each unit of one
        reference point to its own embedded copy of the symbol (lib_name 'X_1', 'X_2' after a sheet-local
        edit; e.g. a three-unit controller with three identical copies named _1, _2 and plain).  Altium
        takes the parts of a component from one library component, so three copies are three components
        with one designator ('Duplicate part designators' + 'Unused sub-part').  Unit k of the merged symbol is unit k of the copy that unit k's
        instance uses; items common to all units come from the copy of the lowest unit."""
        groups: dict[str, list] = defaultdict(list)
        for inst in self.instances:
            for sym in inst.sch.symbols:
                ls = self._lib_for(inst.sch, sym)
                if ls is None or ls.unit_count < 2 or ls.power:
                    continue
                ref, unit = self._sym_ref_unit(inst, sym)
                if ref.startswith("#") or ref.endswith("?"):
                    continue
                groups[ref].append((unit, inst, sym, ls))
        cache, done, merged_refs = {}, {}, []
        for ref, items in groups.items():
            libs = {id(ls): ls for _, _, _, ls in items}
            if len(libs) < 2 or len({ls.unit_count for ls in libs.values()}) > 1 or \
                    len({sym.lib_id for _, _, sym, _ in items}) > 1:
                continue
            by_unit = {}
            for unit, _, _, ls in sorted(items, key=lambda t: t[0]):
                by_unit.setdefault(unit, ls)
            key = tuple(sorted((u, id(ls)) for u, ls in by_unit.items()))
            m = cache.get(key)
            if m is None:
                base = by_unit[min(by_unit)]
                m = copy.copy(base)
                m.shapes = [sh for sh in base.shapes if sh.unit == 0]
                m.pins = [p for p in base.pins if p.unit == 0]
                for u in range(1, base.unit_count + 1):
                    src = by_unit.get(u, base)
                    m.shapes += [sh for sh in src.shapes if sh.unit == u]
                    m.pins += [p for p in src.pins if p.unit == u]
                m.unit_names = dict(base.unit_names)
                for u, src in by_unit.items():
                    if u in src.unit_names:
                        m.unit_names[u] = src.unit_names[u]
                m.lib_id = items[0][2].lib_id
                cache[key] = m
            for _, inst, sym, _ in items:
                if id(sym) in done and done[id(sym)] != key:
                    continue          # same symbol of a repeated sheet merged differently elsewhere
                done[id(sym)] = key
                lk = next((k for k, v in inst.sch.lib_symbols.items() if v is m), None)
                if lk is None:
                    lk = f"{m.lib_id}#convi-units-{list(cache).index(key) + 1}"
                    inst.sch.lib_symbols[lk] = m
                sym.lib_name = lk
            if len({(ls.name, self._signature(ls)) for ls in libs.values()}) > 1:
                merged_refs.append(ref)       # Altium would have seen different library components
        if merged_refs:
            self.issues.info("sch", f"{len(merged_refs)} multi-unit part(s) had units drawn from different copies "
                                    f"of their symbol in KiCad ({', '.join(merged_refs[:6])}"
                                    f"{' ...' if len(merged_refs) > 6 else ''}): joined into one library "
                                    "component so each stays one Altium part")

    def _lib_for(self, sch: Schematic, sym: SymbolInstance) -> LibSymbol | None:
        return sch.lib_symbols.get(sym.lib_name) or sch.lib_symbols.get(sym.lib_id)

    def _signature(self, ls: LibSymbol) -> str:
        h = hashlib.sha1()
        for p in sorted(ls.pins, key=lambda p: (p.unit, p.style, p.number, p.pos)):
            h.update(repr((p.unit, p.style, p.number, p.name, p.etype, p.shape, p.pos, p.angle, p.length,
                           p.hidden)).encode())
        for s in ls.shapes:
            h.update(repr((s.kind, s.unit, s.style, s.pts, s.width, s.fill, s.text, s.radius)).encode())
        h.update(repr((ls.pin_names_hidden, ls.pin_numbers_hidden, ls.unit_count, ls.has_demorgan)).encode())
        return h.hexdigest()[:16]

    def symdef_for(self, ls: LibSymbol) -> SymDef:
        key = id(ls)
        if key in self.sym_of_key:
            return self.sym_of_key[key]
        sig = self._signature(ls)
        base = re.sub(r"[\t\r\n]", " ", ls.name).strip() or "SYMBOL"
        for d in self.symdefs.values():
            if d.signature == sig and (d.name == base or d.name.startswith(base + "_")):
                self.sym_of_key[key] = d
                return d
        name = base
        nick = ls.lib_id.split(":", 1)[0] if ":" in ls.lib_id else ""
        if name in self.symdefs:
            name = f"{base}_{nick}" if nick and f"{base}_{nick}" not in self.symdefs else base
            k = 2
            while name in self.symdefs:
                name = f"{base}_{k}"
                k += 1
        d = SymDef(name=name, lib=ls, signature=sig)
        self.symdefs[name] = d
        self.sym_of_key[key] = d
        return d

    # ------------------------------------------------------------------ text vars
    def subst(self, text: str, inst: SheetInstance | None = None, sym=None, ref=None) -> str:
        def rep(mt):
            k = mt.group(1)
            if sym is not None:
                if k == "REFERENCE":
                    return ref or sym.field("Reference")
                if k == "VALUE":
                    return sym.field("Value")
                v = sym.field(k, None)
                if v is not None:
                    return v
            tb = inst.sch.title_block if inst else {}
            mp = {"TITLE": "title", "REVISION": "rev", "ISSUE_DATE": "date", "COMPANY": "company"}
            if k in mp and mp[k] in tb:
                return tb[mp[k]]
            if k.startswith("COMMENT") and k[7:].isdigit():
                return tb.get(f"comment{k[7:]}", "")
            if k == "SHEETNAME" and inst:
                return inst.name
            if k in ("#", "SHEETNUMBER") and inst:
                return inst.page
            if k in ("##", "SHEETCOUNT"):
                return str(len(self.instances))
            if k == "FILENAME" and inst:
                return inst.out_name
            if k in self.text_vars:
                return self.text_vars[k]
            return mt.group(0)
        return overbar(TEXT_VAR_RE.sub(rep, text))

    # ------------------------------------------------------------------ emit helpers
    @staticmethod
    def _text_kw(e: TextEffects | None):
        return dict(size=font_pt(e), bold=bool(e and e.bold), italic=bool(e and e.italic),
                    font=(e.face if e and e.face else "Arial"))

    @staticmethod
    def _just(h: str, v: str) -> int:
        """Altium TTextJustification index (BottomLeft..TopRight)."""
        col = {"left": 0, "center": 1, "right": 2}.get(h, 1)
        row = {"bottom": 0, "center": 1, "top": 2}.get(v, 1)
        return row * 3 + col

    def _emit_lib_graphics(self, ir: IRWriter, ls: LibSymbol, X, part_map=None, owner=None):
        """Emit symbol graphics + pins through transform X(lib_pt)->(x_mil,y_mil).

        KiCad unit 0 = shared by all units.  Altium does not draw part-0 graphics, so shared
        graphics are emitted once per part; shared pins go to part 1 (single-part symbols)
        or stay on part 0 (Altium 'part zero' pins of multi-part symbols).
        """
        nunits = max(1, ls.unit_count)
        shapes = []
        for sh in ls.shapes:
            if sh.unit == 0:
                for u in range(1, nunits + 1):
                    shapes.append((u, sh))
            else:
                shapes.append((sh.unit, sh))
        for part, sh in shapes:
            mode = max(0, sh.style - 1) if sh.style else 0
            fill = sh.fill not in ("none", "")
            area = COL_FILL if sh.fill == "background" else COL_BODY
            if sh.fill == "color" and sh.fill_color:
                area = rgb(*sh.fill_color[:3])
            col = rgb(*sh.stroke.color[:3]) if sh.stroke and sh.stroke.color else COL_BODY
            common = dict(part=part, mode=mode, color=col, lw=line_size(sh.width))
            if sh.kind == "rectangle":
                a, b = X(sh.pts[0]), X(sh.pts[1])
                ir.cmd("S_RECT", x1=float(min(a[0], b[0])), y1=float(min(a[1], b[1])), x2=float(max(a[0], b[0])),
                       y2=float(max(a[1], b[1])), solid=fill, area=area, **common)
            elif sh.kind == "polyline":
                pts = [X(p) for p in sh.pts]
                if fill and len(pts) >= 3:
                    ir.cmd("S_POLYGON", pts=";".join(f"{x:.4f},{y:.4f}" for x, y in pts), solid=1, area=area, **common)
                else:
                    ir.cmd("S_POLYLINE", pts=";".join(f"{x:.4f},{y:.4f}" for x, y in pts), **common)
            elif sh.kind == "circle":
                c = X(sh.pts[0])
                ir.cmd("S_CIRCLE", x=float(c[0]), y=float(c[1]), r=float(m(sh.radius)), solid=fill, area=area, **common)
            elif sh.kind == "arc":
                s, mid, e = X(sh.pts[0]), X(sh.pts[1]), X(sh.pts[2])
                a = arc_from_3pts_yup(s, mid, e)
                if a:
                    (cx, cy), r, a1, a2 = a
                    ir.cmd("S_ARC", x=float(cx), y=float(cy), r=float(r), a1=float(a1), a2=float(a2), **common)
            elif sh.kind == "bezier" and len(sh.pts) == 4:
                pts = [X(p) for p in sh.pts]
                ir.cmd("S_BEZIER", pts=";".join(f"{x:.4f},{y:.4f}" for x, y in pts), **common)
            elif sh.kind == "text":
                p = X(sh.pts[0])
                ir.cmd("S_LABEL", x=float(p[0]), y=float(p[1]), text=overbar(plain_markup(sh.text)), orient=0,
                       just=self._just(sh.effects.hjustify, sh.effects.vjustify) if sh.effects else 4,
                       part=part, mode=mode, color=col, **self._text_kw(sh.effects))
            elif sh.kind == "textbox":
                a, b = X(sh.pts[0]), X(sh.pts[1])
                ir.cmd("S_TEXTFRAME", x1=float(min(a[0], b[0])), y1=float(min(a[1], b[1])),
                       x2=float(max(a[0], b[0])), y2=float(max(a[1], b[1])), text=frame_text(overbar(plain_markup(sh.text))), border=1,
                       part=part, mode=mode, **self._text_kw(sh.effects))
        # KiCad never connects a no_connect pin, even one stacked on other pins (ESP32-C6-MINI-1: seven NC
        # pins on one spot stay seven unconnected nets); Altium joins every pin on one spot. Such stacked NC
        # pins (all but the one shown) become Altium hidden pins without a net, which connect to nothing.
        stacks: dict = {}
        for pin in ls.pins:
            stacks.setdefault((round(pin.pos[0], 4), round(pin.pos[1], 4), pin.unit, pin.style), []).append(pin)
        isolate = set()
        for grp in stacks.values():
            if len(grp) < 2:
                continue
            keep = next((p for p in grp if not p.hidden), grp[0])
            apart = self._stacked_apart(ls, self._cur_pin_ctx[1] if self._cur_pin_ctx else None)
            isolate.update(id(p) for p in grp if p is not keep and (p.etype == "no_connect" or p.number in apart))
        for pin in ls.pins:
            self._emit_pin(ir, ls, pin, X, owner, isolate=id(pin) in isolate)

    def _stacked_apart(self, ls: LibSymbol, ref: str | None = None) -> set:
        """Numbers of stacked pins (same spot, unit and body style) that KiCad keeps apart: on the board each
        pad of the stack has its own 'unconnected-' net (e.g. a connector's 5V pins A21 / B1, or one of
        several ESD diodes whose other copies are wired).  Altium joins pins on one spot, which would make one net of them.
        With ref: that placed part (SchDoc); without: only what holds for every part of the symbol (SchLib)."""
        if self._apart is None:
            self._apart = {}
            pad_nets = getattr(self, "pad_nets", None) or {}
            uses: dict = {}
            for inst in self.instances:
                for sym in inst.sch.symbols:
                    lsx = self._lib_for(inst.sch, sym)
                    if lsx is None or lsx.power:
                        continue
                    r = self._sym_ref_unit(inst, sym)[0]
                    uses.setdefault(self.symdef_for(lsx).name, (lsx, set()))[1].add(r)
            for name, (lsx, refs) in uses.items():
                stacks: dict = {}
                for pin in lsx.pins:
                    stacks.setdefault((round(pin.pos[0], 4), round(pin.pos[1], 4), pin.unit, pin.style), []).append(pin)
                per_ref = {r: set() for r in refs}
                for grp in stacks.values():
                    if len(grp) < 2:
                        continue
                    keep = next((p for p in grp if not p.hidden), grp[0])
                    for p in grp:
                        if p is keep or "[" in (p.number or "") or "[" in (keep.number or ""):
                            continue
                        for r in refs:
                            a, b = pad_nets.get((r, keep.number)), pad_nets.get((r, p.number))
                            if a and b and a != b and a.startswith("unconnected-") and b.startswith("unconnected-"):
                                per_ref[r].add(p.number)
                all_refs = set.intersection(*per_ref.values()) if per_ref else set()
                self._apart[name] = (all_refs, {r: v for r, v in per_ref.items() if v})
        all_refs, per_ref = self._apart.get(self.symdef_for(ls).name, (set(), {}))
        return per_ref.get(ref, set()) if ref is not None else all_refs

    ETYPE = {"input": 0, "bidirectional": 1, "output": 2, "open_collector": 3, "passive": 4, "tri_state": 5,
             "open_emitter": 6, "power_in": 7, "power_out": 7, "free": 4, "unspecified": 4, "no_connect": 4}

    def _emit_pin(self, ir, ls: LibSymbol, pin: LibPin, X, owner=None, isolate=False):
        # KiCad: connection point at pos, pin body extends along angle a towards the symbol body
        a = math.radians(pin.angle)
        body_end = (pin.pos[0] + pin.length * math.cos(a), pin.pos[1] + pin.length * math.sin(a))
        # Altium: hot spot = Location + PinLength along Orientation (verified by compiling a test sheet)
        hot = X(pin.pos)
        loc = X(body_end)
        dx, dy = hot[0] - loc[0], hot[1] - loc[1]
        if abs(dx) >= abs(dy):
            orient = 0 if dx >= 0 else 2
        else:
            orient = 1 if dy >= 0 else 3
        numbers = [pin.number]
        mt = re.fullmatch(r"\[(.+)\]", pin.number or "")
        if mt:
            nums = []
            for tok in mt.group(1).split(","):
                tok = tok.strip()
                rg = re.fullmatch(r"(\D*)(\d+)-(\D*)(\d+)", tok)
                if rg and rg.group(1) == rg.group(3):
                    nums += [f"{rg.group(1)}{i}" for i in range(int(rg.group(2)), int(rg.group(4)) + 1)]
                elif tok:
                    nums.append(tok)
            numbers = nums or [pin.number]
        name = overbar(plain_markup(pin.name)) if pin.name not in ("~", "") else ""
        shape = pin.shape
        # Altium TIeeeSymbol: 1 Dot, 3 Clock, 4 ActiveLowInput, 6 NotLogicConnection, 17 ActiveLowOutput
        inner = {"clock": 3, "inverted_clock": 3, "clock_low": 3, "edge_clock_high": 3}.get(shape, 0)
        outer_edge = {"inverted": 1, "inverted_clock": 1, "input_low": 4, "clock_low": 4,
                      "output_low": 17}.get(shape, 0)
        outer = {"non_logic": 6}.get(shape, 0)
        part = pin.unit
        if part == 0 and max(1, ls.unit_count) == 1:
            part = 1
        # KiCad hidden pins still connect by position; Altium hidden pins never do.  Only hidden
        # power inputs keep Altium's hidden-pin behaviour (global net by name); other hidden pins
        # become normal pins without visible name/number (they normally sit on a visible pin).
        alt_hidden = (pin.hidden and pin.etype == "power_in" and not ls.power) or isolate
        show_texts = not pin.hidden and not isolate
        outside = ls.pin_names_offset <= 0 and show_texts
        if outside:
            # KiCad "pin name offset 0": name drawn outside, along the pin (name on one side of the
            # pin line, number on the other). Altium cannot do this natively -> draw them as labels.
            mx, my = (hot[0] + loc[0]) / 2.0, (hot[1] + loc[1]) / 2.0
            horiz = abs(hot[0] - loc[0]) >= abs(hot[1] - loc[1])
            gap = 15.0
            part_lbl = pin.unit if pin.unit else (1 if max(1, ls.unit_count) == 1 else 0)
            mode_lbl = max(0, pin.style - 1) if pin.style else 0
            if name and not ls.pin_names_hidden:
                kw = dict(x=mx, y=my + gap, orient=0, just=1) if horiz else dict(x=mx - gap, y=my, orient=1, just=1)
                ir.cmd("S_LABEL", text=name, part=part_lbl, mode=mode_lbl, color=COL_BODY,
                       size=font_pt(pin.name_effects), font="Arial", bold=0, italic=0, **kw)
            if not ls.pin_numbers_hidden:
                kw = dict(x=mx, y=my - gap, orient=0, just=7) if horiz else dict(x=mx + gap, y=my, orient=1, just=7)
                ir.cmd("S_LABEL", text=numbers[0], part=part_lbl, mode=mode_lbl, color=COL_BODY,
                       size=font_pt(pin.number_effects), font="Arial", bold=0, italic=0, **kw)
        if self._cur_pin_ctx is not None:
            doc, ref = self._cur_pin_ctx
            for num in numbers:
                self.pin_spots.setdefault((ref, num), (doc, hot))
        for i, num in enumerate(numbers):
            ir.cmd("S_PIN", x=float(loc[0]), y=float(loc[1]), orient=orient, len=float(m(pin.length)),
                   nsize=font_pt(pin.name_effects), dsize=font_pt(pin.number_effects),
                   margin=float(m(ls.pin_names_offset)) if ls.pin_names_offset > 0 else float(m(0.508)),
                   # an unnamed KiCad pin ('~', resistors, capacitors) gets its number as name, as in
                   # hand-drawn Altium libraries (Altium's library check reports empty pin names); the
                   # name stays hidden
                   des=num, name=name or num, etype=self.ETYPE.get(pin.etype, 4),
                   showname=(show_texts and not outside and not ls.pin_names_hidden and bool(name) and i == 0),
                   showdes=(show_texts and not outside and not ls.pin_numbers_hidden and i == 0), hidden=alt_hidden,
                   hiddennet=(pin.name if alt_hidden and not isolate else ""),
                   inner=inner, outeredge=outer_edge, outer=outer, part=part,
                   mode=max(0, pin.style - 1) if pin.style else 0)

    # ------------------------------------------------------------------ SchLib
    def build_schlib(self, ir: IRWriter, out: Path):
        ir.cmd("SCHLIB", path=str(out))
        for name, d in sorted(self.symdefs.items()):
            ls = d.lib
            if ls.power:
                continue
            units = max(1, ls.unit_count)
            ir.cmd("L_COMP", name=name, desc=ls.field_value("Description") or ls.field_value("ki_description"), des=ls.field_value("Reference", "U") + "?",
                   parts=units, modes=2 if ls.has_demorgan else 1, comment=ls.field_value("Value"))
            X = lambda p: (m(p[0]), m(p[1]))
            self._emit_lib_graphics(ir, ls, X)
            for fname, fld in ls.fields.items():
                if fname in ("Reference", "Value", "Footprint", "Datasheet", "Description", "ki_description", "ki_keywords",
                             "ki_fp_filters", "ki_locked"):
                    continue
                if fld.value:
                    ir.cmd("L_PARAM", name=fname, value=fld.value, hidden=1)
            ds = ls.field_value("Datasheet")
            if ds and ds != "~":
                ir.cmd("L_PARAM", name="Datasheet", value=ds, hidden=1)
            # footprints the placed parts use (most used first = current), so a part placed from
            # this library later gets a footprint too
            uses = self.lib_footprints.get(name, {})
            for k, fp in enumerate(sorted(uses, key=lambda f: (-uses[f], f))):
                ir.cmd("C_IMPL", model=fp, lib=self.pcblib_name, current=k == 0)
            ir.cmd("L_COMPEND")
        ir.cmd("SAVESCHLIB")

    # ------------------------------------------------------------------ SchDoc
    def build_schdoc(self, ir: IRWriter, inst: SheetInstance, out: Path):
        sch = inst.sch
        W, H = sch.paper_size
        Hm = page_height_mil(H)
        ir.cmd("SCHDOC", path=str(out), w=float(math.ceil(m(W) / 50.0) * 50.0), h=float(Hm))
        first_line = len(ir.lines)
        tb = sch.title_block
        params = {"Title": tb.get("title", ""), "Revision": tb.get("rev", ""), "Date": tb.get("date", ""),
                  "CompanyName": tb.get("company", ""), "SheetNumber": inst.page,
                  "SheetTotal": str(len(self.instances))}
        for i in range(1, 10):
            if tb.get(f"comment{i}"):
                params[f"Comment{i}"] = tb[f"comment{i}"]
        roles, total = self._wks_roles()
        if total:
            params["SheetTotal"] = total
        for role, n in roles.items():
            if tb.get(f"comment{n}"):
                params[role] = tb[f"comment{n}"]
        # a template's own parameters may stand for others ('Title' = '=TitleParameter', the field
        # showing '=TitleParameter'): those get the same values
        for t in self.template_family:
            for name, ref in getattr(t, "param_refs", {}).items():
                v = next((params[k] for k in params if k.lower() == name.lower()), None)
                if v and not any(k.lower() == ref.lower() for k in params):
                    params[ref] = v
        for k, v in params.items():
            if v:
                ir.cmd("DOCPARAM", name=k, value=self.subst(v, inst))

        def P(p):
            return float(m(p[0])), float(round(Hm - m(p[1]), 4))

        drop_wires, drop_syms = self._pwr_flag_islands(inst)
        if drop_wires or drop_syms:
            self.issues.info("sch", f"{inst.out_name}: removed {len(drop_wires)} wire(s) and {len(drop_syms)} "
                                    "power port(s) that only held a KiCad PWR_FLAG (Altium has no PWR_FLAG)")
        # KiCad paints shape fills and images on its background layers, under the symbols, wires and
        # texts; Altium paints in creation order, so those go first (seen on a test board: filled domain
        # frames covered every part, wire and label of the sheet)
        back = [g for g in sch.graphics if self._is_background(g)]
        for g in back:
            self._emit_graphic(ir, inst, g, P, out)
        for sym in sch.symbols:
            if id(sym) in drop_syms:
                continue
            self._emit_symbol(ir, inst, sym, P, H)
        # house style: wires in Altium's default colour and width.  Exception: a KiCad project that gives its
        # net classes schematic colours uses them as part of its content (e.g. a colour key on the root sheet), so its wires keep the class colours.
        wire_style = self._wire_styles(inst, sch, P, drop_wires, ir.lines[first_line:]) \
            if self.wire_class_colours else {}
        for w in sch.wires:
            if id(w) in drop_wires:
                continue
            col, lw = wire_style.get(id(w), (None, None))
            ir.cmd("WIRE", pts=";".join("%.4f,%.4f" % P(p) for p in w.pts), color=col, lw=lw)
        hp = self._harness_plan(sch)
        for bw in sch.buses:
            if id(bw) not in hp["skip_bus"]:
                ir.cmd("BUS", pts=";".join("%.4f,%.4f" % P(p) for p in bw.pts))
        for ei, (pos, size) in enumerate(sch.bus_entries):
            if ei in hp["skip_entry"]:
                continue
            a = P(pos)
            b = P((pos[0] + size[0], pos[1] + size[1]))
            ir.cmd("BUSENTRY", x1=a[0], y1=a[1], x2=b[0], y2=b[1])
        for a, b in hp["harness"]:
            ir.cmd("SIGHARNESS", pts="%.4f,%.4f;%.4f,%.4f" % (P(a) + P(b)))
        for c in hp["conns"]:
            self._emit_hconn(ir, c, P)
        for pos, dia in sch.junctions:
            if self._junction_needed(sch, pos):
                x, y = P(pos)
                ir.cmd("JUNCTION", x=x, y=y)
        for pos in sch.no_connects:
            x, y = P(pos)
            ir.cmd("NOERC", x=x, y=y)
        self._port_ctx = self._port_obstacles(inst, P)
        for lb in sch.labels:
            self._emit_label(ir, inst, lb, P)
        self._port_ctx = None
        for child in inst.children:
            self._emit_sheet(ir, inst, child.sheet, P)
        for g in sch.graphics:
            if not self._is_background(g):
                self._emit_graphic(ir, inst, g, P, out)
        for x, y, net in self.glue.get(inst.out_name, []):
            # connectivity glue: global power port carrying the KiCad net (see pipeline repair loop)
            ir.cmd("POWER", x=x, y=y, text=net, style="circle", orient=1, show=0, size=6, font="Arial",
                   bold=0, italic=0)
        if self.template_family:
            # template graphics, size and border; the drawing then moves up or down as a whole
            # (SHIFT, done by the builder) to sit inside the border and clear of the title block
            line, dy = self._template_line(inst.out_name, ir.lines[first_line:], Hm)
            if line:
                ir.lines.insert(first_line, line)
                if dy:
                    ir.cmd("SHIFT", dx=0.0, dy=float(dy))
        ir.cmd("SAVESCH")

    # coordinates in IR fields x/y/x1/... are written x1000 (IRWriter); 'pts' of wires are plain mil
    _FIT_SKIP = {"DOCPARAM", "C_PARAM", "L_PARAM", "SAVESCH", "TEMPLATE", "SHIFT"}

    @classmethod
    def _drawing(cls, lines: list[str]):
        """Everything visible a sheet draws (mil): anchor points, boxes and line segments."""
        from .sheet_template import Drawing

        def num(kv, k):
            return int(kv[k]) / 1000.0

        def plain_pts(v):
            out = []
            for pt in v.split(";"):
                try:
                    px, py = pt.split(",")
                    out.append((float(px), float(py)))
                except ValueError:
                    pass
            return out

        pts, boxes, segs = [], [], []
        for ln in lines:
            f = ln.split("\t")
            c = f[0]
            if c in cls._FIT_SKIP:
                continue
            kv = dict(p.split("=", 1) for p in f[1:] if "=" in p)
            if kv.get("hidden") == "1":
                continue
            try:
                if c in ("S_RECT", "IMAGE"):
                    boxes.append((num(kv, "x1"), num(kv, "y1"), num(kv, "x2"), num(kv, "y2")))
                elif c == "S_TEXTFRAME":
                    # the frame width is an estimate from the character count: count 70 % of it
                    x1, x2 = num(kv, "x1"), num(kv, "x2")
                    boxes.append((x1, num(kv, "y1"), x1 + 0.7 * (x2 - x1), num(kv, "y2")))
                elif c == "SHEETSYM":
                    x, y = num(kv, "x"), num(kv, "y")
                    boxes.append((x, y - num(kv, "h"), x + num(kv, "w"), y))
                elif c == "PORT":
                    x, y, w = num(kv, "x"), num(kv, "y"), num(kv, "w")
                    boxes.append((x, y, x, y + w) if kv.get("vertical") == "1" else (x, y, x + w, y))
                elif c in ("S_CIRCLE", "S_ARC"):
                    x, y, r = num(kv, "x"), num(kv, "y"), num(kv, "r")
                    boxes.append((x - r, y - r, x + r, y + r))
                elif c in ("WIRE", "BUS", "S_POLYLINE", "S_POLYGON", "S_BEZIER") and "pts" in kv:
                    p = plain_pts(kv["pts"])
                    segs.extend(zip(p, p[1:]))
                    pts.extend(p[:1])
                elif "x" in kv and "y" in kv:
                    pts.append((num(kv, "x"), num(kv, "y")))
            except (KeyError, ValueError):
                pass
        return Drawing(pts, boxes, segs)

    def _template_line(self, doc: str, lines: list[str], page_h: float) -> tuple[str | None, float]:
        """(TEMPLATE command, vertical offset in mil) for the sheet: the smallest template of the
        family the drawing fits on, and how far the drawing moves up to sit on it clear of the
        border and the title block (as close to the KiCad top margin as it can)."""
        from .sheet_template import choose
        t, dy, why = choose(self.template_family, self._drawing(lines), page_h, self.template_min)
        if t is None:
            return None, 0.0
        self.template_of[doc] = t
        if why and doc not in self._fit_checked:
            self.issues.warn("sch", f"{doc}: the drawing does not fit even on the largest template "
                                    f"({t.path.name}): " + ", ".join(why) + ". Move those objects in "
                                    "Altium (Convi does not scale drawings).")
        self._fit_checked.add(doc)
        dest = self.template_out / t.path.name
        src = self.template_work / f"template_{t.size_name}.SchDoc"
        if t.path not in self._tpl_copied:
            import shutil
            if t.path.resolve() != dest.resolve():
                shutil.copyfile(t.path, dest)
            shutil.copyfile(t.path, src)
            self._tpl_copied.add(t.path)
        w = IRWriter()
        w.cmd("TEMPLATE", path=str(dest), src=str(src))
        return w.lines[0], dy

    def _pwr_flag_islands(self, inst) -> tuple:
        """Wires and power symbols that exist only to hold a KiCad PWR_FLAG.

        Altium has no PWR_FLAG, so the flag itself is dropped (_emit_power).  A flag that KiCad
        drew on a short wire stub with its own power symbol (flag -- wire -- GND) then leaves that
        wire and power symbol connected to nothing: Altium ERC 'Floating power object'.  Such a
        stub is dropped too.  Only a 2-point wire with a flag at one end and, at the other end,
        power symbols and nothing else (no pin, label or other wire) qualifies; so does a power symbol
        whose pin only touches the flag.
        Returns (set of id(wire), set of id(symbol)).
        """
        sch = inst.sch

        def key(p):
            return round(p[0], 3), round(p[1], 3)

        flags, power_at, pins_at = set(), {}, set()
        for sym in sch.symbols:
            ls = self._lib_for(sch, sym)
            if ls is None:
                continue
            value = unescape_name(sym.field("Value") or ls.field_value("Value") or ls.name).upper()
            if ls.name.upper().startswith("PWR_FLAG") or value == "PWR_FLAG":
                flags.add(key(sym.pos))
                continue
            ref, _unit = self._sym_ref_unit(inst, sym)
            if ls.power or self._is_legacy_power(ls, ref):
                power_at.setdefault(key(sym.pos), []).append(id(sym))
                continue
            for p in ls.pins:
                if p.unit in (0, sym.unit) and p.style in (0, sym.body_style):
                    pins_at.add(key(sym.to_sheet(p.pos)))
        if not flags:
            return set(), set()
        labels_at = {key(lb.pos) for lb in sch.labels}

        def touches(w, pt):
            for a, b in zip(w.pts, w.pts[1:]):
                if key(a) == pt or key(b) == pt:
                    return True
                if abs(a[0] - b[0]) < 1e-3 and abs(a[0] - pt[0]) < 1e-3 and \
                        min(a[1], b[1]) - 1e-3 < pt[1] < max(a[1], b[1]) + 1e-3:
                    return True
                if abs(a[1] - b[1]) < 1e-3 and abs(a[1] - pt[1]) < 1e-3 and \
                        min(a[0], b[0]) - 1e-3 < pt[0] < max(a[0], b[0]) + 1e-3:
                    return True
            return False

        drop_w, drop_s = set(), set()
        for w in sch.wires:
            if len(w.pts) != 2:
                continue
            ends = [key(w.pts[0]), key(w.pts[1])]
            for f, o in (ends, ends[::-1]):
                if f not in flags or o not in power_at:
                    continue
                if o in pins_at or o in labels_at or o in flags:
                    continue
                if any(w2 is not w and (touches(w2, o) or touches(w2, f)) for w2 in sch.wires + sch.buses):
                    continue
                if f in pins_at or f in labels_at or f in power_at:
                    continue
                drop_w.add(id(w))
                drop_s.update(power_at[o])
                break
        # the flag sitting straight on a power symbol's pin, no wire (e.g. a row of rail symbols, each
        # with its PWR_FLAG): without the flag the power port touches nothing
        for f in flags:
            if f in power_at and f not in pins_at and f not in labels_at and \
                    not any(touches(w, f) for w in sch.wires + sch.buses):
                drop_s.update(power_at[f])
        return drop_w, drop_s

    @staticmethod
    def _junction_needed(sch, pos, tol=1e-3) -> bool:
        """Altium adds junctions automatically at T-joints; only wire crossings need a manual one."""
        interior = 0
        for w in sch.wires + sch.buses:
            for a, b in zip(w.pts, w.pts[1:]):
                if min(abs(a[0] - pos[0]) + abs(a[1] - pos[1]), abs(b[0] - pos[0]) + abs(b[1] - pos[1])) < tol:
                    continue
                if abs(a[0] - b[0]) < tol and abs(a[0] - pos[0]) < tol and min(a[1], b[1]) - tol < pos[1] < max(a[1], b[1]) + tol:
                    interior += 1
                elif abs(a[1] - b[1]) < tol and abs(a[1] - pos[1]) < tol and min(a[0], b[0]) - tol < pos[0] < max(a[0], b[0]) + tol:
                    interior += 1
        return interior >= 2

    # -- symbols
    def _emit_symbol(self, ir, inst, sym: SymbolInstance, P, H):
        ls = self._lib_for(inst.sch, sym)
        if ls is None:
            self.issues.error("sch", f"{inst.out_name}: symbol {sym.lib_id} has no cached library data; skipped")
            return
        ref, unit = self._sym_ref_unit(inst, sym)
        t = sym.transform

        def X(lp):
            sx, sy = sym.to_sheet(lp)
            return P((sx, sy))

        if ls.power or self._is_legacy_power(ls, ref):
            self._emit_power(ir, inst, sym, ls, X, P)
            return
        d = self.symdef_for(ls)
        kpath = self._kicad_sym_path(inst, sym)
        # unique per sheet instance (a repeated sheet contains the same KiCad symbol uuids) and per
        # part: Altium silently replaces duplicate unique ids.  The PCB link of multi-part components
        # is taken from Altium's own compile afterwards (pipeline).
        uid = altium_uid(*inst.path, sym.uuid)
        upath = "\\" + "\\".join(altium_uid(u) for u in inst.path) + ("\\" if inst.path else "") + uid
        self.uid_path_of[kpath] = upath
        self.libref_of[kpath] = d.name
        self.ref_of[kpath] = ref
        # Altium state: A = F*T*F must equal Mx^mir * R(o)
        A = (t[0], -t[1], -t[2], t[3])
        orient, mirror = 0, False
        for o in range(4):
            c, s = [(1, 0), (0, 1), (-1, 0), (0, -1)][o]
            R = (c, -s, s, c)  # matrix (a b; c d) for column vectors
            for mi in (False, True):
                M = (-R[0], -R[1], R[2], R[3]) if mi else R
                if M == (A[0], A[1], A[2], A[3]):
                    orient, mirror = o, mi
        fp = self.footprint_of.get(kpath)
        footprint = fp[0] if fp else (sym.field("Footprint").split(":", 1)[-1] if sym.field("Footprint") else "")
        kind = "standard"
        if not sym.on_board and not sym.in_bom:
            kind = "graphical"
        elif not sym.on_board:
            kind = "mechanical"          # in the BOM, not synchronised with the PCB
        elif not sym.in_bom:
            kind = "standard_nobom"
        if not sym.on_board:
            self.not_on_board.add(ref)
        mk = marker_kind(ref, sym.lib_id, d.name, sym.field("Footprint"))
        if mk and sym.on_board:
            # house rule: test points, mounting holes and fiducials are never assembled
            kind = "standard_nobom"
            self.markers[ref] = mk
        value = self.subst(sym.field("Value"), inst, sym, ref)
        ir.cmd("C_BEGIN", libref=d.name, lib=self.lib_name, uid=uid, des=ref, part=unit,
               parts=max(1, ls.unit_count), mode=max(0, sym.body_style - 1), modes=2 if ls.has_demorgan else 1,
               x=P(sym.pos)[0], y=P(sym.pos)[1], orient=orient, mirror=mirror,
               desc=sym.field("Description") or ls.field_value("Description") or ls.field_value("ki_description"),
               kind=kind, dnp=sym.dnp)
        self._cur_pin_ctx = (inst.out_name, ref)
        self._emit_lib_graphics(ir, ls, X)
        self._cur_pin_ctx = None
        # designator / comment / parameters
        bom_params: dict = {}
        # Altium parameter names ignore case: an empty 'ISat' next to 'Isat' = '8A' (seen on an inductor) would
        # blank the value, so of fields that differ only in case the empty ones are left out
        filled = {f.name.lower() for f in sym.fields if f.value}
        for f in sym.fields:
            if f.name in ("ki_keywords", "ki_fp_filters", "ki_description", "Sim.Enable"):
                continue
            if not f.value and f.name.lower() in filled and \
                    sum(1 for g in sym.fields if g.name.lower() == f.name.lower()) > 1:
                continue
            pos = P(f.pos)
            angle, hj, vj = self._field_draw(sym, f)
            kw = dict(x=pos[0], y=pos[1], orient=1 if angle == 90 else 0, just=self._just(hj, vj),
                      hidden=f.hidden or not f.value, **self._text_kw(f.effects))
            if f.name == "Reference":
                ir.cmd("C_DES", text=ref, **kw)
            elif f.name == "Value":
                ir.cmd("C_COMMENT", text=value, **kw)
                ir.cmd("C_PARAM", name="Value", value=value, **{**kw, "hidden": True})
            elif f.name == "Footprint":
                if footprint:
                    ir.cmd("C_PARAM", name="KiCad_Footprint", value=f.value, **{**kw, "hidden": True})
            elif f.name == "Datasheet":
                if f.value and f.value != "~":
                    ir.cmd("C_PARAM", name="Datasheet", value=f.value, **kw)
            elif f.name == "Description":
                continue
            else:
                ir.cmd("C_PARAM", name=f.name, value=self.subst(f.value, inst, sym, ref), shownm=f.show_name, **kw)
                if f.value:
                    bom_params[f.name] = self.subst(f.value, inst, sym, ref)
        # what the KiCad BOM says about this part, for the BOM check after the Altium compile
        self.bom_rows.setdefault(ref, {"value": value, "footprint": footprint if sym.on_board else "",
                                       "in_bom": sym.in_bom, "dnp": sym.dnp, "marker": self.markers.get(ref),
                                       "params": bom_params})
        if footprint and sym.on_board:
            ir.cmd("C_IMPL", model=footprint, lib=self.pcblib_name)
            uses = self.lib_footprints.setdefault(d.name, {})
            uses[footprint] = uses.get(footprint, 0) + 1
        ir.cmd("C_END", orient=orient, mirror=mirror)

    def _field_draw(self, sym: SymbolInstance, f) -> tuple[int, str, str]:
        """KiCad SCH_FIELD draw rotation and effective justification (sch_field.cpp)."""
        t = sym.transform
        ang = int(round(f.angle)) % 180
        draw = ang
        if t[1] != 0:   # rotated symbol
            draw = 0 if ang == 90 else 90
        hj, vj = f.effects.hjustify, f.effects.vjustify

        def tr(v):
            return t[0] * v[0] + t[1] * v[1], t[2] * v[0] + t[3] * v[1]

        rad = math.radians(ang)
        dirv = (math.cos(rad), -math.sin(rad))       # reading direction (Y down)
        downv = (math.sin(rad), math.cos(rad))       # text "down" direction
        # A justification is flipped when the symbol transform reverses the text's reading
        # (or "down") direction.  That depends only on the transform, not on which side the
        # text is justified to: KiCad's IsHorizJustifyFlipped() tests LEFT with
        # center < pos and RIGHT with center > pos, i.e. the same transformed direction.
        # (Testing RIGHT with the negated vector swapped every right/bottom-justified field
        # of an unrotated symbol, e.g. R706 drawn over its own body.)
        if hj in ("left", "right"):
            d = tr(dirv)
            flipped = (d[1] > 0) if draw == 90 else (d[0] < 0)
            if flipped:
                hj = "right" if hj == "left" else "left"
        if vj in ("top", "bottom"):
            d = tr(downv)
            flipped = (d[0] < 0) if draw == 90 else (d[1] < 0)
            if flipped:
                vj = "bottom" if vj == "top" else "top"
        return draw, hj, vj

    def _emit_power(self, ir, inst, sym, ls, X, P):
        value = unescape_name(sym.field("Value") or ls.field_value("Value") or ls.name)
        if ls.name.upper().startswith("PWR_FLAG") or value.upper() == "PWR_FLAG":
            return
        hot = P(sym.pos)
        # direction from the connection point towards the graphics
        pts = []
        for s in ls.shapes:
            pts.extend(X(p) for p in s.pts)
        if pts:
            cx = sum(p[0] for p in pts) / len(pts) - hot[0]
            cy = sum(p[1] for p in pts) / len(pts) - hot[1]
        else:
            cx, cy = 0.0, 1.0
        if abs(cx) > abs(cy):
            orient = 0 if cx > 0 else 2
        else:
            orient = 1 if cy > 0 else 3
        style = self._house_power_style(value)
        vf = sym.field_obj("Value")
        if ls.power == "local":
            ir.cmd("NETLABEL", x=hot[0], y=hot[1], text=self.subst(value, inst), orient=0, just=0,
                   **self._text_kw(vf.effects if vf else None))
            return
        ir.cmd("POWER", x=hot[0], y=hot[1], text=self.subst(value, inst), style=style, orient=orient,
               show=not (vf.hidden if vf else False), **self._text_kw(vf.effects if vf else None))

    @staticmethod
    def _house_power_style(value: str) -> str:
        """Power port style per the house style (as in hand-drawn Altium projects): every
        ground is Altium's power ground (three bars), protective earth / chassis the earth symbol,
        every supply the bar.  The look of the KiCad symbol is not copied."""
        up = value.upper()
        if "EARTH" in up or "CHASSIS" in up or up in ("PE", "FG", "PGND_PE"):
            return "earth"
        if "GND" in up or "VSS" in up or "GROUND" in up:
            return "gnd"
        return "bar"

    # -- labels
    def _emit_label(self, ir, inst, lb: Label, P):
        x, y = P(lb.pos)
        text = self.subst(unescape_name(lb.text), inst)
        a = int(round(lb.angle)) % 360
        tkw = self._text_kw(lb.effects)
        if lb.kind == "label":
            twin = self._port_twins(inst.sch).get(id(lb))
            if twin is not None:
                # KiCad joins a local label to a global / hierarchical label of the same name on its sheet
                # (its ERC only warns: same_local_global_label); an Altium net label never joins a port, so
                # it becomes a port as well (otherwise a pin can be left alone on its net)
                # a local label usually sits on the end of its wire with the text back over the wire; a port
                # drawn that way would lie on the wire and its pin: it points away from the wire instead
                d = {0: (1, 0), 180: (-1, 0), 90: (0, 1)}.get(a, (0, -1))
                for w in inst.sch.wires:
                    q = [P(pt) for pt in w.pts]
                    for u, v in list(zip(q, q[1:])) + list(zip(q[1:], q)):
                        if abs(u[0] - x) < 1 and abs(u[1] - y) < 1 and \
                                (v[0] - u[0]) * d[0] + (v[1] - u[1]) * d[1] > 0 and \
                                abs((v[0] - u[0]) * d[1] - (v[1] - u[1]) * d[0]) < 1:
                            a = (a + 180) % 360
                            break
                    else:
                        continue
                    break
                self._emit_port(ir, x, y, text, a, twin, tkw)
                return
            # KiCad label spin: 0 right, 90 up, 180 left, 270 down; anchor at text start on the wire
            orient, just = {0: (0, 0), 90: (1, 0), 180: (0, 2), 270: (1, 2)}.get(a, (0, 0))
            ir.cmd("NETLABEL", x=x, y=y, text=text, orient=orient, just=just, **tkw)
        elif lb.kind == "global_label":
            if self.global_labels == "port":
                self._emit_port(ir, x, y, text, a, lb.shape, tkw)
            else:
                # KiCad global label at 0 deg extends to the right of its connection point
                orient = {0: 0, 90: 1, 180: 2, 270: 3}.get(a, 0)
                ir.cmd("POWER", x=x, y=y, text=text, style="circle", orient=orient, show=1, **tkw)
        elif lb.kind == "hierarchical_label":
            hp = self._harness_plan(inst.sch)
            if id(lb) in hp["ports"]:
                # named like the member net labels' prefix ('PCIe_{x4}.TX0+'): Altium names a harness member
                # net <port>.<entry>, so the two agree (no 'Nets with multiple names')
                name, ht = hp["ports"][id(lb)]
                self._emit_port(ir, x, y, self.subst(unescape_name(name), inst), a, lb.shape, tkw, htype=ht)
            else:
                self._emit_port(ir, x, y, text, a, lb.shape, tkw)
        elif lb.kind == "directive_label":
            nc = next((f.value for f in lb.fields if f.name.lower() in ("netclass", "net class")), "")
            if nc:
                ir.cmd("PARAMSET", x=x, y=y, name="ClassName", value=nc, orient={0: 0, 90: 1, 180: 2, 270: 3}.get(a, 0))
            else:
                self.issues.info("sch", "Directive labels without a net class are not converted")

    def _port_twins(self, sch) -> dict:
        """id(local label) -> shape of a same-named label on the sheet that becomes a port, for local labels
        not already wired to such a label (e.g. a local label that sits on the global label's own
        wire stays a net label)."""
        cache = self.__dict__.setdefault("_twins", {})
        if id(sch) in cache:
            return cache[id(sch)]
        hp = self._harness_plan(sch)
        ports = {}
        for lb in sch.labels:
            if (lb.kind == "global_label" and self.global_labels == "port") or \
                    (lb.kind == "hierarchical_label" and id(lb) not in hp["ports"]):
                ports.setdefault(unescape_name(lb.text), []).append(lb)
        locs = [lb for lb in sch.labels if lb.kind == "label" and unescape_name(lb.text) in ports]
        out = {}
        if locs:
            segs = [(w, a, b) for w in sch.wires for a, b in zip(w.pts, w.pts[1:])]

            def on(pt, a, b, ends_only=False):
                if abs(pt[0] - a[0]) + abs(pt[1] - a[1]) < 1e-3 or abs(pt[0] - b[0]) + abs(pt[1] - b[1]) < 1e-3:
                    return True
                if ends_only:
                    return False
                vx, vy = b[0] - a[0], b[1] - a[1]
                if abs((pt[0] - a[0]) * vy - (pt[1] - a[1]) * vx) > 1e-3 * max(1.0, math.hypot(vx, vy)):
                    return False
                t = ((pt[0] - a[0]) * vx + (pt[1] - a[1]) * vy) / max(vx * vx + vy * vy, 1e-12)
                return -1e-6 <= t <= 1 + 1e-6

            parent = {id(w): id(w) for w in sch.wires}

            def find(k):
                while parent[k] != k:
                    parent[k] = parent[parent[k]]
                    k = parent[k]
                return k
            for i, (w1, a1, b1) in enumerate(segs):
                for w2, a2, b2 in segs[i + 1:]:
                    if w1 is not w2 and (on(a1, a2, b2) or on(b1, a2, b2) or on(a2, a1, b1) or on(b2, a1, b1)):
                        parent[find(id(w1))] = find(id(w2))

            def nets_of(lb):
                return {find(id(w)) for w, a, b in segs if on(lb.pos, a, b)}
            for lb in locs:
                twins = ports[unescape_name(lb.text)]
                if nets_of(lb) & set().union(*(nets_of(t) for t in twins)):
                    continue
                out[id(lb)] = twins[0].shape
        cache[id(sch)] = out
        if out:
            names = sorted({unescape_name(lb.text) for lb in locs if id(lb) in out})
            self.issues.info("sch", f"{sch.path.name}: {len(out)} local label(s) share the name of a global/"
                                    f"hierarchical label on the sheet ({', '.join(names[:6])}"
                                    f"{' ...' if len(names) > 6 else ''}): KiCad joins them, so they are ports "
                                    "in Altium too")
        return out

    PORT_IO = {"input": 2, "output": 1, "bidirectional": 3, "tri_state": 3, "passive": 0}

    def _port_obstacles(self, inst, P) -> dict:
        """Electrical points and wire segments of a sheet (Altium mil), for port sizing.

        An Altium port connects at BOTH ends, a KiCad label only at its anchor.  A port whose far
        end lands on a wire, pin, label or another port joins two KiCad nets (seen on a test
        board: USB_D_N joined VBUS, GREEN joined BLUE).  _emit_port sizes every port so that only its
        anchor end touches anything.
        """
        sch = inst.sch
        segs, pts = [], set()

        def k(p):
            return round(p[0], 1), round(p[1], 1)

        for w in sch.wires + sch.buses:
            q = [P(p) for p in w.pts]
            segs.extend(zip(q, q[1:]))
        for sym in sch.symbols:
            ls = self._lib_for(sch, sym)
            if ls is None:
                continue
            pts.add(k(P(sym.pos)))
            for pin in ls.pins:
                if pin.unit in (0, sym.unit) and pin.style in (0, sym.body_style):
                    pts.add(k(P(sym.to_sheet(pin.pos))))
        for lb in sch.labels:
            pts.add(k(P(lb.pos)))
        for pos, _dia in sch.junctions:
            pts.add(k(P(pos)))
        for pos in sch.no_connects:
            pts.add(k(P(pos)))
        for sh in sch.sheets:
            for pin in sh.pins:
                pts.add(k(P(pin.pos)))
        return {"segs": segs, "pts": pts, "ports": []}

    @staticmethod
    def _on_seg(pt, a, b, tol=0.5) -> bool:
        if abs(a[0] - b[0]) < tol and abs(pt[0] - a[0]) < tol:
            return min(a[1], b[1]) - tol <= pt[1] <= max(a[1], b[1]) + tol
        if abs(a[1] - b[1]) < tol and abs(pt[1] - a[1]) < tol:
            return min(a[0], b[0]) - tol <= pt[0] <= max(a[0], b[0]) + tol
        return False

    def _port_width(self, anchor, direction, wanted, text):
        """Longest width <= wanted (else the shortest longer one) whose port touches nothing but
        its anchor.  direction: unit vector from the anchor along the port body."""
        ctx = getattr(self, "_port_ctx", None)
        if not ctx:
            return wanted
        ax, ay = anchor

        def same(p, q):
            return abs(p[0] - q[0]) < 0.5 and abs(p[1] - q[1]) < 0.5

        def ok(w):
            far = (ax + direction[0] * w, ay + direction[1] * w)
            for a, b in ctx["segs"]:
                if self._on_seg(far, a, b):
                    return False
                for e in (a, b):           # a wire ending on the port body
                    if not same(e, anchor) and self._on_seg(e, anchor, far):
                        return False
            for p in ctx["pts"]:
                if not same(p, anchor) and self._on_seg(p, anchor, far):
                    return False
            for pa, pb in ctx["ports"]:
                if self._on_seg(far, pa, pb) or self._on_seg(pa, anchor, far) or self._on_seg(pb, anchor, far):
                    return False
            return True

        cands = list(range(int(wanted), 99, -50)) + list(range(int(wanted) + 50, int(wanted) + 2001, 50))
        for w in cands:
            if ok(w):
                if w != wanted:
                    self.issues.info("sch", f"Port '{text}' resized {wanted:.0f} -> {w:.0f} mil so that only its "
                                            "connection end touches other objects")
                ctx["ports"].append(((ax, ay), (ax + direction[0] * w, ay + direction[1] * w)))
                return float(w)
        self.issues.warn("sch", f"Port '{text}': no length found whose far end is free; check it in Altium")
        ctx["ports"].append(((ax, ay), (ax + direction[0] * wanted, ay + direction[1] * wanted)))
        return float(wanted)

    def _emit_port(self, ir, x, y, text, angle, shape, tkw, htype=None):
        # port length: text width estimate + arrow, rounded up to 100 mil
        width = max(300, int(math.ceil((len(text) * tkw["size"] * 9 + 150) / 100.0) * 100))
        direction = {0: (1, 0), 180: (-1, 0), 90: (0, 1)}.get(angle, (0, -1))
        width = self._port_width((x, y), direction, width, text)
        io = self.PORT_IO.get(shape, 0)
        # KiCad: label anchor is the connection point, text extends away from it (angle 0 -> text at right?)
        # KiCad hierarchical/global label spin 0 = pointing left with connection at the left end... text right.
        if angle in (0, 180):
            if angle == 0:
                ir.cmd("PORT", x=x, y=y, w=float(width), text=text, io=io, style="left", vertical=0, **tkw, htype=htype)
            else:
                ir.cmd("PORT", x=x - width, y=y, w=float(width), text=text, io=io, style="right", vertical=0, **tkw, htype=htype)
        else:
            if angle == 90:
                ir.cmd("PORT", x=x, y=y, w=float(width), text=text, io=io, style="bottom", vertical=1, **tkw, htype=htype)
            else:
                ir.cmd("PORT", x=x, y=y - width, w=float(width), text=text, io=io, style="top", vertical=1, **tkw, htype=htype)

    def _emit_hconn(self, ir, c, P):
        """Harness connector over the bus run: entries on the member wires' side (Altium: at the box edge,
        the box spans the bus entry width), its harness connection on the bus line (the far edge)."""
        pad = 1.27
        top_k, bot_k = c["ytop"] - pad, c["ybot"] + pad
        x_entries = c["xb"] + c["dx"]
        left_k = min(c["xb"], x_entries)
        (lx, top), (_, bot) = P((left_k, top_k)), P((left_k, bot_k))
        w = abs(m(c["dx"]))
        tipy = P(c["tip"])[1]
        ir.cmd("HCONN", x=lx, y=top, w=float(w), h=float(top - bot), side="right" if c["dx"] > 0 else "left",
               tip=float(top - tipy), type=c["type"], tx=lx, ty=bot - 100.0)
        for yk, name in c["members"]:
            ir.cmd("HENTRY", name=overbar(name), dist=float(top - P((x_entries, yk))[1]))
        ir.cmd("HCONNEND")

    # -- sheets
    def _emit_sheet(self, ir, inst, sh: Sheet, P):
        child = next((c for c in inst.children if c.sheet is sh), None)
        if child is None:
            return
        x, y = P(sh.pos)
        if not sh.pins:
            # a sheet symbol without sheet pins carries no connection: KiCad lets it sit anywhere (e.g. a
            # top sheet at 22 mm steps), Altium's ERC reports it as an off-grid object -> nearest 50 mil
            x, y = round(x / 50.0) * 50.0, round(y / 50.0) * 50.0
        fill = rgb(*sh.fill_color[:3]) if sh.fill_color and (sh.fill_color[3] if len(sh.fill_color) > 3 else 1) > 0 else -1
        # sheet name / file name in the same font as every other converted text (Arial unless
        # the KiCad field names a face); Altium's default would be the document font, Times New Roman
        fx = {getattr(f, "name", ""): f for f in sh.fields}
        nf = fx.get("Sheetname") or fx.get("Sheet name")
        ff = fx.get("Sheetfile") or fx.get("Sheet file")
        nkw = self._text_kw(nf.effects if nf else None)
        fkw = self._text_kw(ff.effects if ff else None)
        ir.cmd("SHEETSYM", x=x, y=y, w=float(m(sh.size[0])), h=float(m(sh.size[1])),
               name=sh.name, file=child.out_name, uid=altium_uid(sh.uuid), fill=fill,
               nsize=nkw["size"], nfont=nkw["font"], nbold=int(nkw["bold"]), nitalic=int(nkw["italic"]),
               fsize=fkw["size"], ffont=fkw["font"], fbold=int(fkw["bold"]), fitalic=int(fkw["italic"]))
        for pin in sh.pins:
            px, py = pin.pos
            if abs(px - sh.pos[0]) < 1e-3:
                side, dist = "left", py - sh.pos[1]
            elif abs(px - (sh.pos[0] + sh.size[0])) < 1e-3:
                side, dist = "right", py - sh.pos[1]
            elif abs(py - sh.pos[1]) < 1e-3:
                side, dist = "top", px - sh.pos[0]
            else:
                side, dist = "bottom", px - sh.pos[0]
            ename, ht = self._harness_plan(inst.sch)["entries"].get((id(sh), pin.name), (pin.name, None))
            ename = self.subst(unescape_name(ename), inst) if ht is not None else self.subst(pin.name, inst)
            cport = next((v for lb in child.sch.labels if lb.kind == "hierarchical_label" and lb.text == pin.name
                          for v in [self._harness_plan(child.sch)["ports"].get(id(lb))] if v), None)
            if (ht is not None) != (cport is not None):
                self.issues.warn("sch", f"{sh.name}: group bus '{pin.name}' is a signal harness on one side of the "
                                        "sheet symbol and a bus on the other; check that connection in Altium")
            ir.cmd("SHEETENTRY", name=ename, side=side, dist=float(m(dist)),
                   io=self.PORT_IO.get(pin.etype, 0), htype=ht, **self._text_kw(pin.effects))
        ir.cmd("SHEETSYMEND")

    # -- group buses -> signal harnesses
    def _harness_plan(self, sch) -> dict:
        """KiCad group buses ('Ethernet_P0{10GbE}', members joined by name across sheets) as Altium signal
        harnesses (Altium buses only bundle numbered nets).  Per bus network that carries a group
        hierarchical label (sub-sheet) or only group sheet pins (parent sheet):
          * the vertical bus run with the bus entries becomes a harness connector: its entries sit on the
            member wires' ends (the bus entries' far ends), named after the member labels on those wires
            ('Ethernet_P0.D1+' -> 'D1+'), its harness connection on the bus run where the rest of the bus
            leaves; the other bus segments become signal harness lines;
          * the label becomes a port named after the group prefix, the sheet pins sheet entries of that
            name, both of the harness type (the bus alias).
        Anything that does not fit (horizontal runs, entries on both sides, members without labels) keeps
        the bus as before.  Cached per schematic file."""
        cache = self.__dict__.setdefault("_hplans", {})
        if sch.path in cache:
            return cache[sch.path]
        plan = {"skip_bus": set(), "skip_entry": set(), "harness": [], "conns": [], "ports": {}, "entries": {}}
        cache[sch.path] = plan
        segs = [(id(b), b.pts[i], b.pts[i + 1]) for b in sch.buses for i in range(len(b.pts) - 1)]
        if not segs:
            return plan
        aliases = {}
        # KiCad <= 8 keeps bus aliases in the schematic files, KiCad 9+ in the project (schematic.bus_aliases)
        pdata = (getattr(getattr(self, "project", None), "data", None) or {})
        aliases.update(((pdata.get("schematic") or {}).get("bus_aliases") or {}))
        for sc in self.cache.values():
            aliases.update(getattr(sc, "bus_aliases", {}) or {})

        def k(p):
            return round(p[0], 3), round(p[1], 3)

        def on(p, a, b, tol=1e-3):
            cr = (p[0] - a[0]) * (b[1] - a[1]) - (p[1] - a[1]) * (b[0] - a[0])
            if abs(cr) > tol * max(1.0, abs(b[0] - a[0]) + abs(b[1] - a[1])):
                return False
            return (min(a[0], b[0]) - tol <= p[0] <= max(a[0], b[0]) + tol and
                    min(a[1], b[1]) - tol <= p[1] <= max(a[1], b[1]) + tol)

        n = len(segs)
        parent = list(range(n))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for i in range(n):
            for j in range(i + 1, n):
                _, a1, b1 = segs[i]
                _, a2, b2 = segs[j]
                if on(a1, a2, b2) or on(b1, a2, b2) or on(a2, a1, b1) or on(b2, a1, b1):
                    parent[find(i)] = find(j)
        groups: dict = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(segs[i])
        wsegs = [(w.pts[i], w.pts[i + 1]) for w in sch.wires for i in range(len(w.pts) - 1)]

        def wire_labels(p0):
            seen, todo, found = set(), [p0], []
            while todo:
                p = todo.pop()
                if k(p) in seen:
                    continue
                seen.add(k(p))
                for a, b in wsegs:
                    if on(p, a, b):
                        todo += [a, b]
                        found += [lb.text for lb in sch.labels if lb.kind == "label" and on(lb.pos, a, b)]
                found += [lb.text for lb in sch.labels if lb.kind == "label" and k(lb.pos) == k(p)]
            return found

        def htype(inner):
            return inner if inner in aliases else ""

        for net in groups.values():
            lbls = [lb for lb in sch.labels if any(on(lb.pos, a, b) for _, a, b in net)]
            pins = [(sh, pn) for sh in sch.sheets for pn in sh.pins if any(on(pn.pos, a, b) for _, a, b in net)]
            hier = [lb for lb in lbls if lb.kind == "hierarchical_label" and split_group_bus(lb.text)]
            gpins = [(sh, pn) for sh, pn in pins if split_group_bus(pn.name)]
            ents = []
            for ei, (pos, size) in enumerate(sch.bus_entries):
                e = (pos[0] + size[0], pos[1] + size[1])
                if any(on(pos, a, b) for _, a, b in net):
                    ents.append((ei, pos, e))
                elif any(on(e, a, b) for _, a, b in net):
                    ents.append((ei, e, pos))
            if not ents and gpins and len(gpins) == len(pins) and not lbls:
                # parent sheet: a bus between group sheet pins only -> one harness line
                for sh, pn in gpins:
                    pre, inner = split_group_bus(pn.name)
                    plan["entries"][(id(sh), pn.name)] = (pre, htype(inner))
                plan["skip_bus"].update(bid for bid, _, _ in net)
                plan["harness"] += [(a, b) for _, a, b in net]
                continue
            if len(hier) != 1 or not ents or pins:
                continue
            h = hier[0]
            pre, inner = split_group_bus(h.text)
            xs = {round(q[0], 3) for _, q, _ in ents}
            dxs = {round(w[0] - q[0], 3) for _, q, w in ents}
            if len(xs) != 1 or len(dxs) != 1:
                self.issues.info("sch", f"{sch.path.name}: group bus '{h.text}' is not one vertical bus with its "
                                        "entries on one side; kept as an Altium bus")
                continue
            xb, dx = xs.pop(), dxs.pop()
            members = []
            for ei, q, w in ents:
                names = [t for t in wire_labels(w) if t.startswith(pre + ".")]
                if not names:
                    members = None
                    break
                members.append((ei, w, names[0][len(pre) + 1:]))
            if not members:
                self.issues.info("sch", f"{sch.path.name}: group bus '{h.text}' has a member wire without a "
                                        f"'{pre}.<member>' label; kept as an Altium bus")
                continue
            run = [(bid, a, b) for bid, a, b in net if abs(a[0] - xb) < 1e-3 and abs(b[0] - xb) < 1e-3]
            rest = [(bid, a, b) for bid, a, b in net if (bid, a, b) not in run]
            run_pts = [p for _, a, b in run for p in (a, b)]
            tips = [p for p in run_pts if any(k(p) in (k(a), k(b)) for _, a, b in rest)]
            if tips:
                tip = tips[0]
            else:
                tip = min(run_pts, key=lambda p: math.hypot(p[0] - h.pos[0], p[1] - h.pos[1]))
                if not on(h.pos, (xb, min(p[1] for p in run_pts)), (xb, max(p[1] for p in run_pts))):
                    rest.append((None, tip, h.pos))
            ys = [w[1] for _, w, _ in members] + [tip[1]]
            plan["skip_bus"].update(bid for bid, _, _ in net)
            plan["skip_entry"].update(ei for ei, _, _ in members)
            plan["harness"] += [(a, b) for _, a, b in rest]
            plan["conns"].append({"xb": xb, "dx": dx, "ytop": min(ys), "ybot": max(ys), "tip": tip,
                                  "members": sorted(((w[1], nm) for _, w, nm in members)), "type": htype(inner)})
            plan["ports"][id(h)] = (pre, htype(inner))
        return plan

    def _wire_styles(self, inst, sch, P, drop_wires, lines) -> dict:
        """KiCad draws a wire in its net class's schematic colour and width (or the wire's own stroke).
        id(wire) -> (Altium colour or None, Altium line width 0..3 or None), the net found from the pins
        (the board's pad nets), power ports and labels the wire's connected group touches."""
        out = {}
        ps = self.project
        wires = [w for w in sch.wires if id(w) not in drop_wires]
        if not wires:
            return out
        segs = [[P(p) for p in w.pts] for w in wires]
        n = len(wires)
        parent = list(range(n))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def on(pt, pts):
            return any(self._on_seg(pt, a, b) for a, b in zip(pts, pts[1:]))

        for i in range(n):
            ends = (segs[i][0], segs[i][-1])
            for j in range(n):
                if i != j and find(i) != find(j) and any(on(e, segs[j]) for e in ends):
                    parent[find(i)] = find(j)
        nets_of: dict = {}

        def add(pt, net, rank):
            for i in range(n):
                if on(pt, segs[i]):
                    nets_of.setdefault(find(i), []).append((rank, net))
                    break

        pad_nets = getattr(self, "pad_nets", {}) or {}
        for (ref, num), (doc, hot) in self.pin_spots.items():
            if doc == inst.out_name and (ref, num) in pad_nets:
                add(hot, pad_nets[(ref, num)], 0)
        for ln in lines:
            if ln.startswith("POWER\t"):
                kv = dict(f.split("=", 1) for f in ln.split("\t")[1:] if "=" in f)
                try:
                    add((int(kv["x"]) / 1000.0, int(kv["y"]) / 1000.0), kv.get("text", ""), 1)
                except (KeyError, ValueError):
                    pass
        for lb in sch.labels:
            add(P(lb.pos), self.subst(lb.text, inst), 2)
        classes = {c.name: c for c in (ps.netclasses if ps else [])}
        for i, w in enumerate(wires):
            st = w.stroke
            col = rgb(*st.color[:3]) if st and st.color and (len(st.color) < 4 or st.color[3] > 0) else None
            lw = None
            if st and st.width:
                lw = 2 if st.width / MIL > 10.0 else 1
            if (col is None or lw is None) and ps:
                cands = sorted(nets_of.get(find(i), []))
                cls = None
                for _, net in cands:
                    names = ps.classes_of(net)
                    if not names and "/" not in net:
                        names = next((ps.classes_of(bn) for bn in getattr(self, "board_nets", ())
                                      if bn.split("/")[-1] == net and ps.classes_of(bn)), [])
                    cls = next((classes[c] for c in names if c in classes), None)
                    if cls is not None or net:
                        break
                cls = cls or classes.get("Default")
                if cls is not None:
                    if col is None and cls.sch_color:
                        col = rgb(*cls.sch_color)
                    if lw is None and cls.wire_width and cls.wire_width > 10.0:
                        lw = 2
            if col is not None or lw is not None:
                out[id(w)] = (col, lw)
        return out

    _WKS_ROLES = {"drawn by": "DrawnBy", "designed by": "DrawnBy", "drawn": "DrawnBy", "designer": "DrawnBy",
                  "author": "Author", "engineer": "Engineer", "checked by": "CheckedBy", "checked": "CheckedBy",
                  "approved by": "ApprovedBy", "approved": "ApprovedBy"}

    def _wks_roles(self):
        """From the project's KiCad page layout (.kicad_wks): which ${COMMENTn} sits under a 'Drawn by',
        'Checked by' or 'Approved by' caption ({'DrawnBy': 1, ...}), and a sheet total written into it
        ('${#} / 18'; None when it uses ${##})."""
        if hasattr(self, "_wks_cache"):
            return self._wks_cache
        roles, total = {}, None
        name = ((self.project.data if self.project else {}) or {}).get("schematic", {}).get("page_layout_descr_file")
        path = (self.root_path.parent / name) if name else None
        try:
            txt = path.read_text(encoding="utf-8", errors="replace") if path and path.is_file() else ""
        except OSError:
            txt = ""
        items = [(t, float(x), float(y)) for t, x, y in
                 re.findall(r'\(tbtext\s+"((?:[^"\\]|\\.)*)"[^()]*(?:\([^()]*\)\s*)*?\(pos\s+([-\d.]+)\s+([-\d.]+)', txt)]
        for t, x, y in items:
            mt = re.fullmatch(r"\$\{COMMENT(\d)\}", t.strip())
            if mt:
                caps = [(cy - y, c) for c, cx, cy in items if abs(cx - x) < 0.6 and 0 < cy - y < 8 and "${" not in c]
                if caps:
                    role = self._WKS_ROLES.get(min(caps)[1].strip().lower().rstrip(":"))
                    if role:
                        roles.setdefault(role, int(mt.group(1)))
            mt = re.search(r"\$\{#\}\s*(?:/|of)\s*(\d+)", t)
            if mt:
                total = mt.group(1)
        self._wks_cache = (roles, total)
        return self._wks_cache

    # -- graphics
    @staticmethod
    def _is_background(g) -> bool:
        """A filled shape or an image: drawn under everything else, as KiCad draws it."""
        if g.kind == "image":
            return True
        return g.kind in ("rectangle", "circle", "polyline", "arc") and g.fill not in ("none", "", None)

    def _emit_graphic(self, ir, inst, g, P, out: Path):
        col = rgb(*g.stroke.color[:3]) if g.stroke and g.stroke.color else rgb(0, 0, 132)
        lw = line_size(g.stroke.width if g.stroke else None)
        style = {"dash": 1, "dot": 2, "dash_dot": 3, "dash_dot_dot": 3}.get(g.stroke.style if g.stroke else "", 0)
        fill = g.fill not in ("none", "")
        area = rgb(*g.fill_color[:3]) if g.fill_color else col
        common = dict(part=0, mode=0, color=col, lw=lw, style=style)
        if g.kind == "polyline":
            ir.cmd("S_POLYLINE", pts=";".join("%.4f,%.4f" % P(p) for p in g.pts), **common)
        elif g.kind == "rectangle":
            a, b = P(g.pts[0]), P(g.pts[1])
            ir.cmd("S_RECT", x1=min(a[0], b[0]), y1=min(a[1], b[1]), x2=max(a[0], b[0]), y2=max(a[1], b[1]),
                   solid=fill, area=area, **common)
        elif g.kind == "circle":
            c = P(g.pts[0])
            ir.cmd("S_CIRCLE", x=c[0], y=c[1], r=float(m(g.radius)), solid=fill, area=area, **common)
        elif g.kind == "arc":
            s, mid, e = P(g.pts[0]), P(g.pts[1]), P(g.pts[2])
            a = arc_from_3pts_yup(s, mid, e)
            if a:
                (cx, cy), r, a1, a2 = a
                ir.cmd("S_ARC", x=float(cx), y=float(cy), r=float(r), a1=float(a1), a2=float(a2), **common)
        elif g.kind == "bezier" and len(g.pts) == 4:
            ir.cmd("S_BEZIER", pts=";".join("%.4f,%.4f" % P(p) for p in g.pts), **common)
        elif g.kind == "text":
            x, y = P(g.pts[0])
            e = g.effects
            text = plain_markup(self.subst(g.text, inst))
            orient = 1 if int(round(g.angle)) % 180 == 90 else 0
            if "\n" in text:
                lines = text.split("\n")
                h = font_pt(e) * 14.0 * len(lines)
                w = max(len(s) for s in lines) * font_pt(e) * 8.0
                # place the frame on the anchor the way KiCad justifies the whole text block
                hj = e.hjustify if e else "left"
                vj = e.vjustify if e else "bottom"
                x1 = x - w if hj == "right" else (x - w / 2 if hj == "center" else x)
                y2 = y if vj == "top" else (y + h / 2 if vj == "center" else y + h)
                ir.cmd("S_TEXTFRAME", x1=x1, y1=y2 - h, x2=x1 + w, y2=y2, text=frame_text(text), border=0, part=0,
                       mode=0, **self._text_kw(e))
            else:
                ir.cmd("S_LABEL", x=x, y=y, text=text, orient=orient,
                       just=self._just(e.hjustify, e.vjustify) if e else 0, part=0, mode=0, color=col,
                       **self._text_kw(e))
        elif g.kind == "textbox":
            a, b = P(g.pts[0]), P(g.pts[1])
            ir.cmd("S_TEXTFRAME", x1=min(a[0], b[0]), y1=min(a[1], b[1]), x2=max(a[0], b[0]), y2=max(a[1], b[1]),
                   text=frame_text(plain_markup(self.subst(g.text, inst))), border=1 if (g.stroke and (g.stroke.width or 0) >= 0) else 0,
                   part=0, mode=0, **self._text_kw(g.effects))
        elif g.kind == "image" and g.data:
            self._emit_image(ir, g, P, out)
        elif g.kind == "table":
            self._emit_table(ir, inst, g, P)

    def _emit_table(self, ir, inst, g, P):
        """KiCad schematic table: the grid lines KiCad draws and each cell's text in a borderless text frame
        (same grid logic as board tables: merged cells keep no inner line)."""
        from ..kicad.pcb import parse_table
        cells, lines = parse_table(g.raw)
        bcol = rgb(0, 0, 132)
        for a, b, w in lines:
            pa, pb = P(a), P(b)
            ir.cmd("S_POLYLINE", pts="%.4f,%.4f;%.4f,%.4f" % (pa[0], pa[1], pb[0], pb[1]), part=0, mode=0,
                   color=bcol, lw=line_size(w), style=0)
        for c in cells:
            if not c.text.strip() or c.name.split(",")[0] == "0":
                continue
            xs = [P(q)[0] for q in c.box]
            ys = [P(q)[1] for q in c.box]
            mg = c.margins or (0.9525,) * 4
            hj = (c.effects.hjustify if c.effects else "") or "left"
            ir.cmd("S_TEXTFRAME", x1=min(xs) + m(mg[0]), y1=min(ys) + m(mg[3]), x2=max(xs) - m(mg[2]),
                   y2=max(ys) - m(mg[1]), text=frame_text(plain_markup(self.subst(c.text, inst))), border=0, part=0,
                   mode=0, align={"center": 1, "right": 2}.get(hj, 0), **self._text_kw(c.effects))

    def _emit_image(self, ir, g, P, out: Path):
        try:
            data = base64.b64decode(g.data)
        except Exception:
            return
        img_dir = out.parent / "Images"
        img_dir.mkdir(parents=True, exist_ok=True)
        fn = img_dir / f"img_{hashlib.sha1(data).hexdigest()[:10]}.png"
        fn.write_bytes(data)
        try:
            from PIL import Image
            with Image.open(fn) as im:
                wpx, hpx = im.size
                dpi = im.info.get("dpi", (300, 300))[0] or 300
        except Exception:
            wpx, hpx, dpi = 300, 300, 300
        wmm = wpx / dpi * 25.4 * g.scale
        hmm = hpx / dpi * 25.4 * g.scale
        cx, cy = g.pts[0]
        a = P((cx - wmm / 2, cy + hmm / 2))
        b = P((cx + wmm / 2, cy - hmm / 2))
        ir.cmd("IMAGE", x1=a[0], y1=a[1], x2=b[0], y2=b[1], file=str(fn))


def plan_glue(conv: "SchConverter", comparison) -> int:
    """Add glue power ports for KiCad nets that Altium compiled into several pieces.

    Returns the number of glue ports added.
    """
    added = 0
    split = sorted({kn for _d, _p, kn, _a in comparison.mismatched_pins})
    for kn in split:
        pins = comparison.kicad_nets.get(kn, set())
        pieces: dict = {}
        for p in pins:
            pieces.setdefault(comparison.altium_pin_net.get(p), []).append(p)
        # global KiCad nets keep their own name (joins the existing power ports / global labels)
        glue_name = kn
        for _an, plist in pieces.items():
            for p in sorted(plist):
                spot = conv.pin_spots.get(p)
                if spot:
                    doc, (x, y) = spot
                    lst = conv.glue.setdefault(doc, [])
                    if (x, y, glue_name) not in lst:
                        lst.append((x, y, glue_name))
                        added += 1
                    break
    return added
