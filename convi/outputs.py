"""Manufacturing outputs: a house OutJob (and Draftsman template) retargeted to the converted board.

The templates are an Altium Output Job (.OutJob) and, optionally, a Draftsman template (.DwfDot or
.PCBDwf), both made for some other board (the "template board").  Convi copies them into the converted
project and adapts everything that belongs to the template board:

* paths and the project name (``<old>.PcbDoc`` -> ``<new>.PcbDoc``, output folders),
* the variant (the converted project's DNP variant for every output),
* mechanical layers, matched by what a layer is for (assembly, courtyard, 3D body, ... read from the
  layer names of the template board, which the Draftsman template carries), so a print or Gerber
  that plots the template's "Top Assembly" plots the converted board's assembly layer,
* drill drawing / drill guide: every drill pair the board uses,
* Draftsman views: the scale that fits the view's frame on the sheet, centred in it.

Altium then generates the selected containers (``altium/outputs.pas``).  The OutJob keeps all its
containers, so the rest can still be run from Altium.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

OUTPUT_TEMPLATES_ENV = "CONVI_OUTPUT_TEMPLATES"

# Altium's 'layer hash' id of Mechanical N in Plot.Set (Gerber) settings
PLOT_MECH0 = 16908288
PLOT_TOP, PLOT_BOTTOM = 16777217, 16842751   # copper: Mid N = PLOT_TOP + N
# Draftsman (V7) layer id of Mechanical N
V7_MECH_LOW = 56            # Mechanical 1..16: 57..72
V7_MECH_HIGH = 67108864     # Mechanical 17..: 67108881..

OUTPUT_LABELS = {
    "Gerber": "Gerber", "NC Drill": "NC drill", "ODB": "ODB++", "IPC2581": "IPC-2581",
    "BOM_PartType": "BOM", "Pick Place": "pick and place", "ExportSTEP": "3D STEP",
    "PCBDrawing": "Draftsman drawing", "Assembly": "assembly drawings", "Schematic Print": "schematic PDF",
    "PCB 3D Print": "3D print", "PDF3D": "3D PDF", "Protel2Netlist": "netlist", "WireListNetlist": "netlist",
    "PCB Print": "PCB print", "Final": "fabrication drawing", "Composite": "composite drawing",
    "IPC356D": "IPC-D-356 netlist", "Test Points": "test point report",
}


# ---------------------------------------------------------------------------------------------- templates
@dataclass
class OutputTemplates:
    outjob: Path | None = None
    draftsman: Path | None = None
    searched: list = field(default_factory=list)


def find_output_templates(*starts: Path) -> OutputTemplates:
    """The house OutJob and Draftsman template: in CONVI_OUTPUT_TEMPLATES, else the sheet templates folder,
    else the CONVI workspace root.  The first folder that holds an .OutJob wins."""
    from .convert.sheet_template import find_templates_dir
    from .layout import find_workspace
    dirs: list[Path] = []
    env = os.environ.get(OUTPUT_TEMPLATES_ENV, "").strip()
    if env:
        dirs.append(Path(env))
    td = find_templates_dir(*starts, Path(__file__).parent)
    if td:
        dirs.append(td)
    for s in (*starts, None):
        ws = find_workspace(s)
        if ws and ws not in dirs:
            dirs.append(ws)
    res = OutputTemplates(searched=dirs)
    for d in dirs:
        if not d.is_dir():
            continue
        jobs = sorted(d.glob("*.OutJob"))
        if not jobs:
            continue
        res.outjob = jobs[0]
        dw = sorted(d.glob("*.DwfDot")) or sorted(d.glob("*.PCBDwf"))
        res.draftsman = dw[0] if dw else None
        break
    return res


# ---------------------------------------------------------------------------------------------- OutJob
@dataclass
class Container:
    index: int            # OutputMediumN
    name: str
    kind: str             # 'publish' (PDF) or 'generate' (files)
    outputs: list         # [(output type, output name)]

    @property
    def label(self) -> str:
        kinds = []
        for t, _ in self.outputs:
            k = OUTPUT_LABELS.get(t, t)
            if k not in kinds:
                kinds.append(k)
        return f"{self.name} ({', '.join(kinds)})"

    @property
    def is_draftsman(self) -> bool:
        return any(t == "PCBDrawing" for t, _ in self.outputs)


def _ini(text: str) -> dict:
    d = {}
    for ln in text.splitlines():
        if "=" in ln and not ln.startswith("["):
            k, v = ln.split("=", 1)
            d.setdefault(k, v)
    return d


def read_containers(text: str) -> list[Container]:
    """Output containers of an OutJob that hold at least one output (Print Job / Video are left out)."""
    d = _ini(text)
    media, kinds = {}, {}
    for k, v in d.items():
        m = re.fullmatch(r"OutputMedium(\d+)", k)
        if m:
            media[int(m.group(1))] = v
            kinds[int(m.group(1))] = d.get(f"OutputMedium{m.group(1)}_Type", "")
    outs: dict[int, list] = {i: [] for i in media}
    for k, v in d.items():
        m = re.fullmatch(r"OutputType(\d+)", k)
        if not m:
            continue
        n = m.group(1)
        for i in media:
            if d.get(f"OutputEnabled{n}_OutputMedium{i}", "0") not in ("0", ""):
                outs[i].append((v, d.get(f"OutputName{n}", v)))
    res = []
    for i in sorted(media):
        kind = {"Publish": "publish", "GeneratedFiles": "generate"}.get(kinds[i])
        if kind and outs[i]:
            res.append(Container(i, media[i], kind, outs[i]))
    return res


def template_dir_of(text: str, old_name: str) -> str | None:
    """Folder of the template project as the OutJob stores it (absolute paths of its PcbDoc)."""
    m = re.search(r"([A-Za-z]:\\[^|\r\n=]*?\\)" + re.escape(old_name) + r"\.PcbDoc", text)
    return m.group(1) if m else None


def retarget_outjob(text: str, old_name: str, new_name: str, out_dir: Path, variant: str | None,
                    mech_map: dict[int, int | None], log=None, copper_layers: int | None = None) -> str:
    """The OutJob for the converted project (see the module notes).  copper_layers: the board's copper
    layer count; Gerber plots of the template's inner layers become plots of every inner layer of this
    board (an 8-layer board would otherwise lose In3..In6 with a 4-layer template)."""
    log = log or (lambda m: None)
    nl = "\r\n" if "\r\n" in text else "\n"
    old_dir = template_dir_of(text, old_name)
    new_dir = str(Path(out_dir)).rstrip("\\/") + "\\"
    if old_dir:
        text = text.replace(old_dir, new_dir)
    text = text.replace(old_name, new_name)
    var = variant or "[No Variations]"
    lines = text.split(nl)
    out = []
    for ln in lines:
        k, _, v = ln.partition("=")
        if re.fullmatch(r"OutputVariantName\d+", k) or k == "VariantName":
            ln = f"{k}={var}"
        elif re.fullmatch(r"(OpenOutputs?|PromptOverwrite)\d+", k):
            ln = f"{k}=0"
        elif k.startswith("Configuration") and "_Item" in k:
            ln = _retarget_item(ln, mech_map, log, copper_layers)
        out.append(ln)
    out = _retarget_prints(out, mech_map)
    return nl.join(out)


def _retarget_item(ln: str, mech_map: dict, log, copper_layers: int | None = None) -> str:
    key, _, body = ln.partition("=")
    return key + "=" + _retarget_fields(body, mech_map, copper_layers)


def _retarget_fields(body: str, mech_map: dict, copper_layers: int | None = None) -> str:
    parts = body.split("|")
    if any(p.startswith("Plot.Set=SerializeLayerHash") for p in parts):
        res = []
        for p in parts:
            if p.startswith(("Plot.Set=", "Mirror.Set=")):
                head, *ids = p.split(",")
                keep = [e for e in ids[:1]] if ids and not re.fullmatch(r"\d+~\d+", ids[0]) else []
                rest = ids[len(keep):]
                for e in rest:
                    if not re.fullmatch(r"\d+~\d+", e):
                        keep.append(e)
                        continue
                    lid, val = e.split("~")
                    n = int(lid) - PLOT_MECH0
                    if 1 <= n <= 1024:
                        to = mech_map.get(n)
                        if to is None:
                            continue
                        e = f"{PLOT_MECH0 + to}~{val}"
                    if e not in keep:
                        keep.append(e)
                if copper_layers and p.startswith("Plot.Set="):
                    keep = _plot_inner_layers(keep, copper_layers)
                p = ",".join([head] + keep)
            res.append(p)
        parts = res
    if any(p.startswith("PlotUsedDrillDrawingLayerPairs=") for p in parts):
        # the template board's drill pairs are named after its layers: plot every pair this board uses
        parts = [re.sub(r"^(PlotUsedDrill(?:Drawing|Guide)LayerPairs)=False$", r"\1=True", p) for p in parts]
    if any(re.match(r"PlotMechanical\d+Plot=", p) for p in parts):
        parts = _retarget_odb(parts, mech_map)
    return "|".join(parts)


def _plot_inner_layers(ids: list[str], copper_layers: int) -> list[str]:
    """Plot.Set entries 'id~1': inner copper as the board has it (Mid 1 .. Mid n-2) when the template
    plots inner layers at all; the template's inner layers beyond the board's are dropped."""
    mids = [e for e in ids if re.fullmatch(r"\d+~\d+", e) and 1 <= int(e.split("~")[0]) - PLOT_TOP <= 30]
    if not mids:
        return ids
    val = mids[0].split("~")[1]
    n_mid = max(0, copper_layers - 2)
    res, done = [], False
    for e in ids:
        if e in mids:
            if not done:
                res += [f"{PLOT_TOP + k}~{val}" for k in range(1, n_mid + 1)]
                done = True
            continue
        res.append(e)
    return res


def _retarget_odb(parts: list[str], mech_map: dict) -> list[str]:
    """ODB++ settings: PlotMechanicalNPlot (1..16) and Plot_V7_k / Plot_V7_k_name ('Mechanical Layer N')."""
    want = set()
    for p in parts:
        m = re.fullmatch(r"PlotMechanical(\d+)Plot=True", p)
        if m and mech_map.get(int(m.group(1))):
            want.add(mech_map[int(m.group(1))])
    v7 = {}
    for p in parts:
        m = re.fullmatch(r"Plot_V7_(\d+)(_name)?=(.*)", p)
        if m:
            v7.setdefault(m.group(1), {})["name" if m.group(2) else "on"] = m.group(3)
    for e in v7.values():
        m = re.fullmatch(r"Mechanical Layer (\d+)", e.get("name", ""))
        if m and e.get("on") == "True" and mech_map.get(int(m.group(1))):
            want.add(mech_map[int(m.group(1))])
    res = []
    for p in parts:
        m = re.fullmatch(r"PlotMechanical(\d+)Plot=(True|False)", p)
        if m:
            res.append(f"PlotMechanical{m.group(1)}Plot={'True' if int(m.group(1)) in want else 'False'}")
        elif re.fullmatch(r"Plot_V7_\d+(_name)?=.*", p):
            continue
        else:
            res.append(p)
    high = sorted(n for n in want if n > 16)
    if high:
        i = next((k for k, p in enumerate(res) if p.startswith("PlotBackDrillingPairs=")), len(res))
        extra = []
        for k, n in enumerate(high, 1):
            extra += [f"Plot_V7_{k}=True", f"Plot_V7_{k}_name=Mechanical Layer {n}"]
        res[i:i] = extra
    return res


def _retarget_prints(lines: list[str], mech_map: dict) -> list[str]:
    """PCB prints (assembly drawings ...): each printout lists its layers as items 'Layer=MechanicalN';
    a layer with no counterpart is dropped, two template layers that land on one board layer print once,
    and the items of the configuration are numbered again."""
    res: list[str] = []
    i = 0
    while i < len(lines):
        m = re.match(r"^Configuration(\d+)_Name1=", lines[i])
        if not m:
            res.append(lines[i])
            i += 1
            continue
        cfg = m.group(1)
        items = []
        while i + 1 < len(lines):
            mn = re.match(rf"^Configuration{cfg}_Name(\d+)=(.*)$", lines[i])
            mi = re.match(rf"^Configuration{cfg}_Item(\d+)=(.*)$", lines[i + 1]) if mn else None
            if not (mn and mi and mn.group(1) == mi.group(1)):
                break
            items.append((mn.group(2), mi.group(2)))
            i += 2
        if not any("Record=PcbPrintLayer" in it for _, it in items):
            for k, (nm, it) in enumerate(items, 1):
                res += [f"Configuration{cfg}_Name{k}={nm}", f"Configuration{cfg}_Item{k}={it}"]
            continue
        kept, seen = [], set()
        for nm, it in items:
            if "Record=PcbPrintOut" in it:
                seen = set()
            lm = re.search(r"\|Layer=Mechanical(\d+)\|", "|" + it + "|")
            if lm and "Record=PcbPrintLayer" in it:
                to = mech_map.get(int(lm.group(1)))
                if to is None or to in seen:
                    continue
                seen.add(to)
                it = re.sub(r"(^|\|)Layer=Mechanical\d+(\||$)", rf"\1Layer=Mechanical{to}\2", it, count=1)
            kept.append((nm, it))
        for k, (nm, it) in enumerate(kept, 1):
            nm = re.sub(r"OutputConfigurationParameter\d+$", f"OutputConfigurationParameter{k}", nm)
            res += [f"Configuration{cfg}_Name{k}={nm}", f"Configuration{cfg}_Item{k}={it}"]
    return res


# ---------------------------------------------------------------------------------------------- layers
def layer_role(name: str, number: int) -> tuple[str, str] | None:
    """What a mechanical layer is for, from its name: (role, 'top' / 'bottom' / '')."""
    n = name.lower()
    side = ""
    if re.search(r"\btop\b|^f\.|\(f\.", n):
        side = "top"
    elif re.search(r"\bbottom\b|\bbot\b|^b\.|\(b\.", n):
        side = "bottom"
    if "courtyard" in n or "crtyd" in n:
        role = "courtyard"
    elif "3d" in n and "body" in n:
        role = "3dbody"
    elif "designator" in n:
        role = "designator"
    elif "center" in n or "centre" in n or "centroid" in n:
        role = "center"
    elif "component outline" in n or "assembly" in n or ".fab" in n or n.endswith("fab"):
        role = "assembly"          # KiCad's fab layers hold the part outlines
    elif ("board" in n and ("outline" in n or "shape" in n)) or "edge.cuts" in n or "profile" in n:
        role = "board"
    elif "dimension" in n or "drawing" in n or "fab note" in n or "dwgs" in n:
        role = "drawing"
    elif "glue" in n or "adhes" in n:
        role = "glue"
    elif re.fullmatch(r"mechanical ?\d+", n):
        # an unnamed layer: Mechanical 1 is where Altium boards keep the outline by convention
        return ("board", "") if number == 1 else None
    else:
        return None
    if role in ("board", "drawing"):
        side = ""
    return role, side


KIND_ROLE = {
    "Board": ("board", ""), "AssemblyTop": ("assembly", "top"), "AssemblyBottom": ("assembly", "bottom"),
    "CourtyardTop": ("courtyard", "top"), "CourtyardBottom": ("courtyard", "bottom"),
    "3DBodyTop": ("3dbody", "top"), "3DBodyBottom": ("3dbody", "bottom"), "FabNotes": ("drawing", ""),
    "GluePointsTop": ("glue", "top"), "GluePointsBottom": ("glue", "bottom"),
}


def board_roles(mech_layers) -> dict:
    """(role, side) -> mechanical layer number of the converted board (Convi's LayerMap.used_mech())."""
    res = {}
    for ml in mech_layers:
        r = KIND_ROLE.get(ml.kind) or layer_role(ml.name, ml.number)
        if r and r not in res:
            res[r] = ml.number
    return res


def mech_map_for(template_layers: dict[int, str], board: dict) -> dict[int, int | None]:
    """Template mechanical layer number -> board layer number (None: nothing on this board for it)."""
    res = {}
    for n, name in template_layers.items():
        r = layer_role(name, n)
        res[n] = board.get(r) if r else None
    return res


# ---------------------------------------------------------------------------------------------- Draftsman
def _varint(b: bytes, i: int):
    r = s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        s += 7
        if c < 0x80:
            return r, i


def _enc_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        c = n & 0x7F
        n >>= 7
        out.append(c | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _lz4_block_decompress(src: bytes, size: int) -> bytes:
    """LZ4 block format (pure Python; used when the lz4 package is not installed)."""
    dst = bytearray()
    i, n = 0, len(src)
    while i < n:
        tok = src[i]
        i += 1
        lit = tok >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        dst += src[i:i + lit]
        i += lit
        if i >= n:
            break
        off = src[i] | (src[i + 1] << 8)
        i += 2
        ml = tok & 15
        if ml == 15:
            while True:
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        start = len(dst) - off
        if off >= ml:
            dst += dst[start:start + ml]
        else:
            for k in range(ml):
                dst.append(dst[start + k])
    return bytes(dst[:size])


def _lz4_block_literals(src: bytes) -> bytes:
    """A valid LZ4 block holding `src` as literals only (no compression), for when lz4 is missing."""
    n = len(src)
    out = bytearray()
    if n >= 15:
        out.append(0xF0)
        r = n - 15
        while r >= 255:
            out.append(255)
            r -= 255
        out.append(r)
    else:
        out.append(n << 4)
    out += src
    return bytes(out)


def dwf_read(path: Path) -> str:
    """Draftsman document (.PCBDwf / .DwfDot): chunks [flag u8][varint raw][varint stored][data],
    flag 1 = LZ4 block, holding DataContract XML."""
    try:
        import lz4.block as lb
    except ImportError:
        lb = None
    b = Path(path).read_bytes()
    i, out = 0, []
    while i < len(b):
        flag = b[i]
        i += 1
        us, i = _varint(b, i)
        cs, i = _varint(b, i)
        d = b[i:i + cs]
        i += cs
        if flag == 1:
            out.append(lb.decompress(d, uncompressed_size=us) if lb else _lz4_block_decompress(d, us))
        else:
            out.append(d)
    return b"".join(out).decode("utf-8")


def dwf_write(path: Path, xml: str, chunk: int = 0x100000) -> None:
    try:
        import lz4.block as lb
    except ImportError:
        lb = None
    raw = xml.encode("utf-8")
    out = bytearray()
    for k in range(0, len(raw), chunk):
        part = raw[k:k + chunk]
        c = lb.compress(part, store_size=False) if lb else _lz4_block_literals(part)
        out += b"\x01" + _enc_varint(len(part)) + _enc_varint(len(c)) + c
    Path(path).write_bytes(bytes(out))


def dwf_mech_layers(xml: str) -> dict[int, str]:
    """Mechanical layers of the template board (number -> name), from the Draftsman cache."""
    res = {}
    for m in re.finditer(r"<Layer><Color>(?:(?!</Layer>).)*?<IsMechanical>true</IsMechanical>(?:(?!</Layer>).)*?"
                         r"<Name>([^<]*)</Name>(?:(?!</Layer>).)*?<V7LayerId>(\d+)</V7LayerId></Layer>", xml, re.S):
        v7 = int(m.group(2))
        n = v7 - V7_MECH_HIGH if v7 > V7_MECH_HIGH else v7 - V7_MECH_LOW
        if 1 <= n <= 1024:
            res.setdefault(n, m.group(1))
    return res


def dwf_board_size(xml: str) -> tuple[float, float] | None:
    """Template board size (mm) from its outline in the Draftsman cache (sheet units: 1/96 inch)."""
    import math
    a = xml.find("<BoardFabricationInformation>")
    b = xml.find("</BoardOutline>", a)
    if a < 0 or b < 0:
        return None
    xs, ys = [], []
    for m in re.finditer(r"<Segment>(.*?)</Segment>", xml[a:b], re.S):
        s = m.group(1)
        p = re.search(r"<Point><X>([-\d.E]+)</X><Y>([-\d.E]+)</Y>", s)
        if p:
            xs.append(float(p.group(1)))
            ys.append(float(p.group(2)))
        if "<Kind>Arc" in s:
            c = re.search(r"<Center><X>([-\d.E]+)</X><Y>([-\d.E]+)</Y>", s)
            r = float(re.search(r"<Radius>([-\d.E]+)", s).group(1))
            a1 = float(re.search(r"<Angle1>([-\d.E]+)", s).group(1))
            a2 = float(re.search(r"<Angle2>([-\d.E]+)", s).group(1))
            for ang in [a1, a2] + [k * 90 for k in range(9) if a1 <= k * 90 <= a2]:
                xs.append(float(c.group(1)) + r * math.cos(math.radians(ang)))
                ys.append(float(c.group(2)) + r * math.sin(math.radians(ang)))
    if not xs:
        return None
    k = 25.4 / 96
    return (max(xs) - min(xs)) * k, (max(ys) - min(ys)) * k


SCALES = [Fraction(n, d) for n, d in [(10, 1), (5, 1), (4, 1), (3, 1), (5, 2), (2, 1), (3, 2), (1, 1), (2, 3),
                                      (1, 2), (1, 3), (1, 4), (1, 5), (1, 10)]]
PX_PER_MM = 96 / 25.4
FIT_TYPES = ("DrillViewView", "BoardFabricationViewData", "BoardAssemblyViewData")
REALISTIC = "BoardRealisticViewData"
PAGE_EDGE = 30.0        # page frame and zone ruler (px)
GAP = 12.0              # clearance to other items (px)
TITLE = 26.0            # a view's title above it (px)


def drill_table_rows(board) -> int:
    """Rows of Altium's drill table for a KiCad board: one per hole size, plating and round / slot
    (checked against Altium on a 4-layer and an 8-layer board)."""
    keys = set()
    for fp in board.footprints:
        for pd in fp.pads:
            d = pd.drill
            if d is None or d.w <= 0:
                continue
            slot = bool(d.h) and abs(d.h - d.w) > 1e-6
            keys.add((round(min(d.w, d.h or d.w), 3), pd.type != "np_thru_hole", slot))
    for v in board.vias:
        keys.add((round(v.drill, 3), True, False))
    return len(keys)


def dwf_drill_rows(xml: str) -> int:
    """Rows of the drill table cached in a Draftsman document (the template board's)."""
    a = xml.find("<DrillTableInformation>")
    b = xml.find("</DrillTableInformation>", a)
    keys = set()
    for m in re.finditer(r"<LayerStackHole>(.*?)</LayerStackHole>", xml[a:b] if a >= 0 else "", re.S):
        h = m.group(1)
        size = re.search(r"<HoleSize>([-\d.E]+)</HoleSize>", h)
        plated = re.search(r"<Plated>(\w+)</Plated>", h)
        length = re.search(r"<Lenght>([-\d.E]+)</Lenght>", h)
        if size:
            keys.add((round(float(size.group(1)) / PX_PER_MM, 3), plated and plated.group(1),
                      bool(length and float(length.group(1)) > 0)))
    return len(keys)


def _num(item: str, path: str):
    m = re.search(path, item)
    return float(m.group(1)) if m else None


def _items(page: str):
    """Top-level items of a page: (type, start, end) in the page text."""
    starts = [(m.start(), m.group(1)) for m in re.finditer(r'<Item i:type="(\w+)"><Id>', page)]
    end_items = page.find("</Items>")
    res = []
    for k, (st, typ) in enumerate(starts):
        en = starts[k + 1][0] if k + 1 < len(starts) else end_items
        res.append((typ, st, en))
    return res


def _layout_page(page: str, old_wh, new_wh, tpl_rows: int, new_rows: int, log) -> str:
    """Fit the board views of one sheet: each view gets the part of the sheet that is its own (split
    halfway between views, kept clear of texts and tables), the largest standard scale at which the board
    fits there, centred.  Realistic views keep their aspect.  A drill table keeps its top edge (it grows
    upwards from its location)."""
    pw = _num(page, r"<Size><Height>[-\d.E]+</Height><Width>([-\d.E]+)</Width></Size><StandardSizeName>")
    ph = _num(page, r"<Size><Height>([-\d.E]+)</Height><Width>[-\d.E]+</Width></Size><StandardSizeName>")
    if not pw or not ph:
        return page
    k = PX_PER_MM
    ow, oh = old_wh
    nw, nh = new_wh
    views, obstacles, table = [], [], None
    for typ, st, en in _items(page):
        it = page[st:en]
        loc = re.search(r"<Location><X>([-\d.E]+)</X><Y>([-\d.E]+)</Y></Location>", it)
        if typ in FIT_TYPES and loc:
            sm = re.search(r"<Scale><Denominator>(\d+)</Denominator><Numenator>(\d+)</Numenator></Scale>", it)
            s = Fraction(int(sm.group(2)), int(sm.group(1))) if sm else Fraction(1)
            x, y = float(loc.group(1)), float(loc.group(2))
            views.append([typ, st, en, (x, y, x + ow * s * k, y + oh * s * k)])
        elif typ == REALISTIC and loc:
            x, y = float(loc.group(1)), float(loc.group(2))
            w = _num(it, r"<Width>([-\d.E]+)</Width></Item>$") or _num(it, r"</Settings><Width>([-\d.E]+)</Width>")
            h = _num(it, r"<VerticalAlignment>\d+</VerticalAlignment><Height>([-\d.E]+)</Height>")
            if w and h:
                views.append([typ, st, en, (x, y, x + w, y + h)])
        elif typ == "Text":
            r = re.search(r"<Rect><Height>([-\d.E]+)</Height><Width>([-\d.E]+)</Width><X>([-\d.E]+)</X>"
                          r"<Y>([-\d.E]+)</Y></Rect>", it)
            if r and re.search(r"<Text>\s*\S", it):
                h, w, x, y = (float(g) for g in r.groups())
                obstacles.append((x, y, x + w, y + h))
        elif loc:
            sz = re.search(r"<Size><Height>([-\d.E]+)</Height><Width>([-\d.E]+)</Width></Size>", it)
            if sz and float(sz.group(2)) > 0:
                x, y = float(loc.group(1)), float(loc.group(2))
                h, w = float(sz.group(1)), float(sz.group(2))
                if typ == "DrillTableBoardView" and tpl_rows and new_rows:
                    row = h / (tpl_rows + 2)
                    top = y + h
                    h = (new_rows + 2) * row
                    ny = max(PAGE_EDGE, top - h)
                    table = (st, en, loc.group(0), ny)
                    y = ny
                obstacles.append((x, y, x + w, y + h))
    if not views:
        return page
    regions = []
    for v in views:
        typ, st, en, (x0, y0, x1, y1) = v
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        R = [PAGE_EDGE, PAGE_EDGE, pw - PAGE_EDGE, ph - PAGE_EDGE]
        for o in views:
            if o is v:
                continue
            ox0, oy0, ox1, oy1 = o[3]
            ocx, ocy = (ox0 + ox1) / 2, (oy0 + oy1) / 2
            if abs(ocx - cx) >= abs(ocy - cy):
                if ocx > cx:
                    R[2] = min(R[2], (x1 + ox0) / 2 - GAP / 2)
                else:
                    R[0] = max(R[0], (ox1 + x0) / 2 + GAP / 2)
            else:
                if ocy > cy:
                    R[3] = min(R[3], (y1 + oy0) / 2 - GAP / 2)
                else:
                    R[1] = max(R[1], (oy1 + y0) / 2 + GAP / 2)
        for ox0, oy0, ox1, oy1 in obstacles:
            if ox0 <= cx <= ox1 and oy0 <= cy <= oy1:
                continue
            sep = {"r": ox0 - x1, "l": x0 - ox1, "u": oy0 - y1, "d": y0 - oy1}
            side = max(sep, key=sep.get)
            if side == "r":
                R[2] = min(R[2], ox0 - GAP)
            elif side == "l":
                R[0] = max(R[0], ox1 + GAP)
            elif side == "u":
                R[3] = min(R[3], oy0 - GAP)
            else:
                R[1] = max(R[1], oy1 + GAP)
        R[3] -= TITLE
        regions.append(R)
    # board views on one sheet share a scale (a top / bottom or layer pair reads as a pair)
    best = [next((s for s in SCALES if nw * s * k <= R[2] - R[0] and nh * s * k <= R[3] - R[1]), SCALES[-1])
            for R in regions]
    common = min((b for v, b in zip(views, best) if v[0] != REALISTIC), default=None)
    edits = []
    for v, R in zip(views, regions):
        typ, st, en, _ = v
        rw, rh = R[2] - R[0], R[3] - R[1]
        if rw <= 0 or rh <= 0:
            continue
        it = page[st:en]
        if typ == REALISTIC:
            f = min(rw / nw, rh / nh)
            w, h = nw * f, nh * f
            nx, ny = R[0] + (rw - w) / 2, R[1] + (rh - h) / 2
            it = re.sub(r"<Location><X>[-\d.E]+</X><Y>[-\d.E]+</Y></Location>",
                        f"<Location><X>{nx!r}</X><Y>{ny!r}</Y></Location>", it, count=1)
            it = re.sub(r"(<VerticalAlignment>\d+</VerticalAlignment><Height>)[-\d.E]+(</Height>)",
                        rf"\g<1>{h!r}\2", it, count=1)
            it = re.sub(r"(</Settings><Width>)[-\d.E]+(</Width>)", rf"\g<1>{w!r}\2", it, count=1)
            log(f"{typ}: {w / k:.0f} x {h / k:.0f} mm")
        else:
            new_s = common
            w, h = float(nw * new_s * k), float(nh * new_s * k)
            nx, ny = R[0] + (rw - w) / 2, R[1] + (rh - h) / 2
            it = re.sub(r"<Scale><Denominator>\d+</Denominator><Numenator>\d+</Numenator></Scale>",
                        f"<Scale><Denominator>{new_s.denominator}</Denominator>"
                        f"<Numenator>{new_s.numerator}</Numenator></Scale>", it, count=1)
            it = re.sub(r"<Location><X>[-\d.E]+</X><Y>[-\d.E]+</Y></Location>",
                        f"<Location><X>{nx!r}</X><Y>{ny!r}</Y></Location>", it, count=1)
            log(f"{typ}: scale {new_s.numerator}:{new_s.denominator}")
        edits.append((st, en, it))
    if table:
        st, en, old_loc, ny = table
        it = page[st:en]
        x = re.search(r"<Location><X>([-\d.E]+)</X>", old_loc).group(1)
        edits.append((st, en, it.replace(old_loc, f"<Location><X>{x}</X><Y>{ny!r}</Y></Location>", 1)))
    for st, en, it in sorted(edits, reverse=True):
        page = page[:st] + it + page[en:]
    return page


def _copper_v7(i: int, n: int) -> int:
    """V7 (= V6) layer id of the i-th copper layer of an n-layer board: Top 1, Mid k 1+k, Bottom 32."""
    return 1 if i == 0 else 32 if i == n - 1 else 1 + i


_ENTRY = r"<BoardLayerDisplayData>(?:(?!</BoardLayerDisplayData>).)*?<V7LayerId>{}</V7LayerId><Visible>(?:true|false)</Visible></BoardLayerDisplayData>"


def _set_view_layer(item: str, lid: int, inner_entries: list[str] | None = None) -> str:
    """Point a single-layer board view at copper layer `lid`: LayerV7Id, and in ShowedLayers (one entry per
    layer, each with the layer's colour) only that layer visible.  A layer the template board did not have
    gets an entry coloured like one of the template's inner layers.  LayerV6Id stays (1 in every template
    view, whatever the layer)."""
    item = re.sub(r"<LayerV7Id>\d+</LayerV7Id>", f"<LayerV7Id>{lid}</LayerV7Id>", item, count=1)
    item = item.replace("<Visible>true</Visible>", "<Visible>false</Visible>")
    m = re.search(_ENTRY.format(lid), item, re.S)
    if m:
        item = item[:m.start()] + m.group(0).replace("<Visible>false</Visible>", "<Visible>true</Visible>") + \
            item[m.end():]
        return item
    pool = inner_entries or re.findall(_ENTRY.format(r"\d+"), item, re.S)[:1]
    if not pool:
        return item
    ent = pool[lid % len(pool)]
    ent = re.sub(r"<V7LayerId>\d+</V7LayerId><Visible>\w+</Visible>",
                 f"<V7LayerId>{lid}</V7LayerId><Visible>true</Visible>", ent)
    return item.replace("<ShowedLayers>", "<ShowedLayers>" + ent, 1)


def expand_layer_pages(xml: str, copper_layers: int, log=None) -> str:
    """One drawing per copper layer of the board, laid out like the template's layer sheets.

    Layer sheets are the sheets that hold nothing but single-layer board views (e.g. a 4-layer template:
    two sheets, F.Cu + In1 and In2 + B.Cu).  They are replaced by as many sheets as the board needs, each
    built from the first layer sheet (same number of views, same frames; _layout_page fits them later)
    with fresh sheet and item ids.  A view of the top, an inner or the bottom layer is copied from the
    template's view of that kind of layer."""
    log = log or (lambda m: None)
    if not copper_layers or copper_layers < 2:
        return xml
    pages = list(re.finditer(r"<Page>.*?</Page>", xml, re.S))
    layer_pages = []
    for m in pages:
        items = _items(m.group(0))
        if items and all(t == "BoardFabricationViewData" for t, _, _ in items) and \
                all(re.search(r"<LayerV7Id>\d+</LayerV7Id>", m.group(0)[st:en]) for _, st, en in items):
            layer_pages.append(m)
    if not layer_pages:
        return xml
    models = {}
    for m in layer_pages:
        pg = m.group(0)
        for _, st, en in _items(pg):
            it = pg[st:en]
            lid = int(re.search(r"<LayerV7Id>(\d+)</LayerV7Id>", it).group(1))
            kind = "top" if lid == 1 else "bottom" if lid == 32 else "inner"
            models.setdefault(kind, it)
    inner_entries = []
    for it in models.values():
        for lid in range(2, 31):
            m = re.search(_ENTRY.format(lid), it, re.S)
            if m and m.group(0) not in inner_entries:
                inner_entries.append(m.group(0))
    proto = layer_pages[0].group(0)
    slots = [proto[st:en] for _, st, en in _items(proto)]
    per_page = len(slots)
    ids = [int(x) for x in re.findall(r"<(?:Page|Item i:type=\"\w+\")><Id>(\d+)</Id>", xml)]
    next_id = max(ids, default=0) + 1
    i0 = proto.find("<Items>") + len("<Items>")
    i1 = proto.find("</Items>")
    new_pages = []
    layers = [_copper_v7(i, copper_layers) for i in range(copper_layers)]
    for p0 in range(0, len(layers), per_page):
        items = []
        for j, lid in enumerate(layers[p0:p0 + per_page]):
            kind = "top" if lid == 1 else "bottom" if lid == 32 else "inner"
            it = models.get(kind) or models.get("inner") or slots[j]
            loc = re.search(r"<Location>.*?</Location>", slots[j]).group(0)
            sc = re.search(r"<Scale>.*?</Scale>", slots[j]).group(0)
            it = re.sub(r"<Location>.*?</Location>", loc, it, count=1)
            it = re.sub(r"<Scale>.*?</Scale>", sc, it, count=1)
            it = re.sub(r"<Id>\d+</Id>", f"<Id>{next_id}</Id>", _set_view_layer(it, lid, inner_entries), count=1)
            next_id += 1
            items.append(it)
        pg = proto[:i0] + "".join(items) + proto[i1:]
        pg = re.sub(r"^<Page><Id>\d+</Id>", f"<Page><Id>{next_id}</Id>", pg, count=1)
        next_id += 1
        new_pages.append(pg)
    log(f"Layer sheets: {len(layer_pages)} in the template -> {len(new_pages)} for {copper_layers} copper layers")
    a, b = layer_pages[0].start(), layer_pages[0].end()
    out = xml[:a] + "".join(new_pages) + xml[b:]
    for m in reversed(layer_pages[1:]):
        shift = len("".join(new_pages)) - (b - a)
        out = out[:m.start() + shift] + out[m.end() + shift:]
    return out


def retarget_dwf(xml: str, new_pcb: Path, old_name: str, new_name: str, old_wh, new_wh, log=None,
                 new_drill_rows: int = 0, copper_layers: int | None = None) -> str:
    """Draftsman template -> the converted board's drawing.

    Every PcbDoc reference points to the new board; texts that are the old project name become the new
    one (and are no longer clipped to their old frame).  Board views and realistic views are fitted to
    the new board on their sheet (_layout_page); the drill table keeps its top edge.  Draftsman
    refreshes the board data itself when the document is first published."""
    log = log or (lambda m: None)
    tpl_rows = dwf_drill_rows(xml)
    for p in sorted(set(re.findall(r">([A-Za-z]:\\[^<>]*\.PcbDoc)<", xml)), key=len, reverse=True):
        xml = xml.replace(">" + p + "<", ">" + str(new_pcb) + "<")
    xml = xml.replace(f">{old_name}.PcbDoc<", f">{new_name}.PcbDoc<")
    xml = xml.replace(f"<Text>{old_name}</Text>", f"<Text>{new_name}</Text>")

    def unclip(m):
        it = m.group(0)
        if f"<Text>{new_name}</Text>" in it:
            it = it.replace("<ClipToBounds>true</ClipToBounds>", "<ClipToBounds>false</ClipToBounds>")
        return it
    xml = re.sub(r'<Item i:type="Text">.*?</Item>', unclip, xml, flags=re.S)
    if copper_layers:
        xml = expand_layer_pages(xml, copper_layers, log)
    if not old_wh or not new_wh:
        return xml
    out, pos = [], 0
    for m in re.finditer(r"<Page>.*?</Page>", xml, re.S):
        out.append(xml[pos:m.start()])
        out.append(_layout_page(m.group(0), old_wh, new_wh, tpl_rows, new_drill_rows, log))
        pos = m.end()
    out.append(xml[pos:])
    return "".join(out)


# ---------------------------------------------------------------------------------------------- plan
@dataclass
class OutputPlan:
    outjob: Path
    draftsman: Path | None
    containers: list            # [Container] to run
    notes: list = field(default_factory=list)


def prepare(templates: OutputTemplates, out_dir: Path, name: str, pcb_path: Path, mech_layers,
            board_size, variant: str | None, selected: list[str] | None, issues=None,
            drill_rows: int = 0, copper_layers: int | None = None) -> OutputPlan | None:
    """Write <name>.OutJob (and <name>.PCBDwf) into the output folder.  selected: container names to run
    (None: all)."""
    def info(m):
        if issues:
            issues.info("outputs", m)
    if templates.outjob is None:
        if issues:
            issues.warn("outputs", "Manufacturing outputs skipped: no OutJob template found (looked in "
                        + ", ".join(str(d) for d in templates.searched) + ")")
        return None
    out_dir = Path(out_dir)
    raw = templates.outjob.read_bytes()
    text = raw.decode("utf-8-sig", errors="replace")
    old_name = templates.outjob.stem
    dwf_xml = dwf_read(templates.draftsman) if templates.draftsman else None
    tpl_layers = dwf_mech_layers(dwf_xml) if dwf_xml else {}
    board = board_roles(mech_layers)
    if tpl_layers:
        mm = mech_map_for(tpl_layers, board)
    else:
        own = {ml.number for ml in mech_layers}
        mm = {n: (n if n in own else None) for n in range(1, 65)}
        if issues:
            issues.warn("outputs", "No Draftsman template next to the OutJob: its mechanical layers are kept by "
                                   "number (the template board's layer names are unknown)")
    if tpl_layers:
        pairs = [f"{n} {tpl_layers[n]} -> {mm[n]}" for n in sorted(mm) if mm[n]]
        skipped = [f"{n} {tpl_layers[n]}" for n in sorted(mm) if not mm[n]]
        info("Output layers (template Mechanical -> board Mechanical): " + ", ".join(pairs) +
             ("; nothing on this board for: " + ", ".join(skipped) if skipped else ""))
    new = retarget_outjob(text, old_name, name, out_dir, variant, mm, copper_layers=copper_layers)
    conts = read_containers(new)
    dwf_path = None
    if dwf_xml is not None:
        dwf_path = out_dir / f"{name}.PCBDwf"
        logs = []
        x = retarget_dwf(dwf_xml, pcb_path, old_name, name, dwf_board_size(dwf_xml), board_size, logs.append,
                         new_drill_rows=drill_rows, copper_layers=copper_layers)
        dwf_write(dwf_path, x)
        if logs:
            info("Draftsman views fitted to the board: " + "; ".join(logs))
        # the Draftsman output documents the template's drawing: point it at this one
        new = re.sub(r"(OutputDocumentPath\d+=)[^\r\n]*\.PCBDwf", rf"\g<1>{name}.PCBDwf", new)
    else:
        conts = [c for c in conts if not c.is_draftsman]
    job = out_dir / f"{name}.OutJob"
    job.write_bytes(new.encode("utf-8"))
    if selected is not None:
        want = {s.lower() for s in selected}
        conts = [c for c in conts if c.name.lower() in want]
    return OutputPlan(job, dwf_path, conts)


def list_template_containers(templates: OutputTemplates) -> list[Container]:
    """Containers the wizard offers (Draftsman only with a Draftsman template)."""
    if templates.outjob is None:
        return []
    text = templates.outjob.read_bytes().decode("utf-8-sig", errors="replace")
    return [c for c in read_containers(text) if templates.draftsman or not c.is_draftsman]


# ---------------------------------------------------------------------------------------------- run
def run_outputs(plan: OutputPlan, prj: Path, out_dir: Path, work: Path, x2=None, progress=None,
                timeout: float = 7200) -> dict:
    """Generate the plan's containers in Altium (altium/outputs.pas) and report what was written."""
    import time
    from .altium.runner import run_script
    say = progress or (lambda m: None)
    work = Path(work)
    lst = []
    dw = next((c for c in plan.containers if c.is_draftsman), None)
    if dw is not None and plan.draftsman is not None:
        lst.append(f"refresh\t{dw.name}")
    lst += [f"{c.kind}\t{c.name}" for c in plan.containers]
    (work / "outputs_list.txt").write_text("\r\n".join(lst) + "\r\n", encoding="utf-8")
    log, done, started = work / "outputs.log", work / "outputs.done", work / "outputs.started"
    for f in (log, done):
        f.unlink(missing_ok=True)
    tpl = (Path(__file__).resolve().parent / "altium" / "outputs.pas").read_text(encoding="utf-8")
    scr = work / "outputs.pas"
    scr.write_text(tpl.replace("%PRJ%", str(prj)).replace("%OUTJOB%", str(plan.outjob))
                   .replace("%DWF%", str(plan.draftsman or "")).replace("%LIST%", str(work / "outputs_list.txt"))
                   .replace("%LOG%", str(log)).replace("%DONE%", str(done)).replace("%STARTED%", str(started)),
                   encoding="utf-8")
    t0 = time.time()

    def on_poll(_elapsed):
        try:
            last = [ln for ln in log.read_text(encoding="utf-8", errors="ignore").splitlines()
                    if ln.startswith("START")]
            if last:
                say(f"Altium: generating manufacturing outputs ({last[-1].split(chr(9))[-1]})")
        except OSError:
            pass
    run_script(scr, "Main", done, timeout=timeout, x2=x2, started=started, on_poll=on_poll)
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    ended = {ln.split("\t")[2] for ln in text.splitlines() if ln.startswith("END\t")}
    failed = [c.name for c in plan.containers if c.name not in ended]
    by_folder: dict[str, int] = {}
    skip = {"_convi"}
    for p in Path(out_dir).rglob("*"):
        if not p.is_file() or p.parts[len(Path(out_dir).parts)] in skip:
            continue
        try:
            if p.stat().st_mtime < t0 - 2:
                continue
        except OSError:
            continue
        rel = p.relative_to(out_dir)
        if len(rel.parts) < 2:
            continue                       # project documents saved on the way (the drawing)
        key = str(Path(*rel.parts[:2])) if rel.parts[0].startswith("Project Outputs") and len(rel.parts) > 2 \
            else str(rel.parent)
        by_folder[key] = by_folder.get(key, 0) + 1
    n = sum(by_folder.values())
    summary = (f"Manufacturing outputs: {len(plan.containers) - len(failed)} of {len(plan.containers)} "
               f"container(s) generated, {n} file(s) in " + ", ".join(f"{k} ({v})" for k, v in sorted(by_folder.items())))
    return {"summary": summary, "files": n, "by_folder": by_folder, "failed": failed, "seconds": time.time() - t0}
