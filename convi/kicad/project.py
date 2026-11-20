"""KiCad project (.kicad_pro) discovery and settings."""
from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .pcb import NetClass


@dataclass
class ProjectFiles:
    folder: Path
    pro: Path | None = None
    root_sch: Path | None = None
    pcb: Path | None = None
    sym_lib_table: Path | None = None
    fp_lib_table: Path | None = None
    dru: Path | None = None
    wks: list = field(default_factory=list)


def discover(path: Path) -> ProjectFiles:
    """Accept a folder, a .kicad_pro, .kicad_sch or .kicad_pcb and find the project files."""
    path = Path(path).resolve()
    if path.is_dir() and not any(path.glob("*.kicad_pro")) and not any(path.glob("*.kicad_sch")) \
            and not any(path.glob("*.kicad_pcb")):
        # a repository root etc.: use the project below it when there is exactly one
        found = [p for p in path.rglob("*.kicad_pro") if "-backups" not in str(p)]
        if len(found) == 1:
            path = found[0]
    folder = path if path.is_dir() else path.parent
    pf = ProjectFiles(folder=folder)
    stem = None
    if path.is_file():
        stem = path.stem
        if path.suffix == ".kicad_pro":
            pf.pro = path
    if pf.pro is None:
        pros = sorted(folder.glob("*.kicad_pro"))
        if stem:
            pros = [p for p in pros if p.stem == stem] or pros
        if pros:
            pf.pro = pros[0]
    stem = pf.pro.stem if pf.pro else stem
    if stem:
        sch = folder / f"{stem}.kicad_sch"
        pcb = folder / f"{stem}.kicad_pcb"
        pf.root_sch = sch if sch.is_file() else None
        pf.pcb = pcb if pcb.is_file() else None
        dru = folder / f"{stem}.kicad_dru"
        pf.dru = dru if dru.is_file() else None
    if pf.pcb is None:
        pcbs = sorted(folder.glob("*.kicad_pcb"))
        pf.pcb = pcbs[0] if len(pcbs) == 1 else None
    if pf.root_sch is None and not pf.pro:
        schs = sorted(folder.glob("*.kicad_sch"))
        pf.root_sch = schs[0] if len(schs) == 1 else None
    for n, attr in (("sym-lib-table", "sym_lib_table"), ("fp-lib-table", "fp_lib_table")):
        p = folder / n
        if p.is_file():
            setattr(pf, attr, p)
    pf.wks = sorted(folder.glob("*.kicad_wks"))
    return pf


@dataclass
class ProjectSettings:
    data: dict = field(default_factory=dict)
    text_vars: dict = field(default_factory=dict)
    netclasses: list = field(default_factory=list)       # NetClass (effective values filled from Default)
    assignments: dict = field(default_factory=dict)      # net name -> [class names]
    patterns: list = field(default_factory=list)         # (pattern, class)
    rules: dict = field(default_factory=dict)            # board.design_settings.rules
    sheet_names: dict = field(default_factory=dict)      # uuid -> name

    def classes_of(self, net: str) -> list[str]:
        out = list(self.assignments.get(net, []))
        short = net.split("/")[-1]
        for pat, cls in self.patterns:
            if cls in out:
                continue
            if _match(pat, net) or _match(pat, short):
                out.append(cls)
        return out

    def netclass(self, name: str) -> NetClass | None:
        for nc in self.netclasses:
            if nc.name == name:
                return nc
        return None


def _match(pat: str, s: str) -> bool:
    if len(pat) > 2 and pat.startswith("/") and pat.endswith("/"):
        try:
            return re.search(pat[1:-1], s) is not None
        except re.error:
            return False
    return fnmatch.fnmatchcase(s, pat)


def load_settings(pro: Path | None) -> ProjectSettings:
    ps = ProjectSettings()
    if pro is None or not Path(pro).is_file():
        return ps
    try:
        d = json.loads(Path(pro).read_text(encoding="utf-8"))
    except Exception:
        return ps
    ps.data = d
    ps.text_vars = {k: str(v) for k, v in (d.get("text_variables") or {}).items()}
    ns = d.get("net_settings") or {}
    raw = ns.get("classes") or []
    default = next((c for c in raw if c.get("name") == "Default"), {})

    def g(c, k):
        v = c.get(k)
        if v is None:
            v = default.get(k)
        return float(v) if isinstance(v, (int, float)) else None

    for c in raw:
        nc = NetClass(name=c.get("name", ""), clearance=g(c, "clearance"), track_width=g(c, "track_width"),
                      via_dia=g(c, "via_diameter"), via_drill=g(c, "via_drill"),
                      uvia_dia=g(c, "microvia_diameter"), uvia_drill=g(c, "microvia_drill"),
                      dp_width=g(c, "diff_pair_width"), dp_gap=g(c, "diff_pair_gap"),
                      dp_via_gap=g(c, "diff_pair_via_gap"),
                      priority=int(c.get("priority", 0 if c.get("name") != "Default" else 2 ** 31 - 1)))
        # remember which values were explicitly defined (KiCad 9+ partial classes)
        mt = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+))?", str(c.get("schematic_color") or ""))
        if mt and (mt.group(4) is None or float(mt.group(4)) > 0):
            nc.sch_color = (int(mt.group(1)), int(mt.group(2)), int(mt.group(3)))
        if isinstance(c.get("wire_width"), (int, float)):
            nc.wire_width = float(c["wire_width"])
        nc.description = ",".join(k for k in ("clearance", "track_width", "via_diameter", "via_drill",
                                              "diff_pair_width", "diff_pair_gap") if k in c)
        ps.netclasses.append(nc)
    for net, classes in (ns.get("netclass_assignments") or {}).items():
        if isinstance(classes, str):
            classes = [classes]
        ps.assignments[net] = list(classes)
    for p in ns.get("netclass_patterns") or []:
        if p.get("pattern") and p.get("netclass"):
            ps.patterns.append((p["pattern"], p["netclass"]))
    board = d.get("board") or {}
    ps.rules = ((board.get("design_settings") or {}).get("rules") or {})
    for uuid, name in d.get("sheets") or []:
        ps.sheet_names[uuid] = name
    return ps
