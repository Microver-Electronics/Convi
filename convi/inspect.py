"""Inspect a KiCad project before conversion and list what is missing."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .kicad.common import Issues
from .kicad.pcb import Board, parse_board
from .kicad.project import ProjectFiles, ProjectSettings, discover, load_settings
from .kicad.sch import parse_schematic
from .models3d import ModelResolver

KICAD10_PCB = 20260206
KICAD10_SCH = 20260306


@dataclass
class ModelNeed:
    raw: str                      # path as written in KiCad
    status: str                   # missing / unresolved_var / wrl_only
    note: str
    refs: list = field(default_factory=list)   # footprint references using it


@dataclass
class Inspection:
    files: ProjectFiles
    settings: ProjectSettings
    issues: Issues
    board: Board | None = None
    sheet_files: list = field(default_factory=list)
    missing_sheets: list = field(default_factory=list)
    model_stats: dict = field(default_factory=dict)
    model_needs: list = field(default_factory=list)
    undefined_vars: set = field(default_factory=set)
    counts: dict = field(default_factory=dict)

    @property
    def blocking(self) -> list[str]:
        out = []
        if self.files.root_sch is None and self.files.pcb is None:
            out.append("No KiCad schematic or board found")
        out += [f"Missing sub-sheet: {s}" for s in self.missing_sheets]
        return out


def inspect_project(path: Path, resolver: ModelResolver | None = None, progress=None) -> Inspection:
    pf = discover(Path(path))
    st = load_settings(pf.pro)
    iss = Issues()
    ins = Inspection(files=pf, settings=st, issues=iss)
    if pf.pro is None:
        iss.warn("project", "No .kicad_pro file found; net classes and design rules will use defaults")
    if pf.root_sch is None:
        iss.warn("project", "No root schematic (.kicad_sch) found; only the PCB will be converted")
    if pf.pcb is None:
        iss.warn("project", "No board (.kicad_pcb) found; only the schematics will be converted")
    if pf.dru is not None:
        iss.info("rules", f"Custom KiCad DRC rules found ({pf.dru.name}); clearance, creepage, width, via and "
                          "hole constraints with net-class/net/type conditions are converted, others are listed")

    # ---------------- schematics
    if pf.root_sch is not None:
        seen = set()
        stack = [pf.root_sch.resolve()]
        while stack:
            f = stack.pop()
            if f in seen:
                continue
            seen.add(f)
            if progress:
                progress(f"Reading {f.name}")
            try:
                sch = parse_schematic(f, iss)
            except Exception as e:
                iss.error("sch", f"Cannot read {f.name}: {e}")
                continue
            ins.sheet_files.append(f)
            if sch.version and sch.version > KICAD10_SCH:
                iss.info("sch", f"{f.name} was saved by a newer KiCad ({sch.version}); unknown items are reported")
            for k, n in sch.unsupported.items():
                iss.info("sch", f"{f.name}: {n} '{k}' item(s) are not converted")
            ins.counts["symbols"] = ins.counts.get("symbols", 0) + len(sch.symbols)
            for sh in sch.sheets:
                cf = (f.parent / sh.file).resolve()
                if not cf.is_file():
                    ins.missing_sheets.append(sh.file)
                    iss.missing("sch", f"Sub-sheet file not found: {sh.file}", str(cf))
                else:
                    stack.append(cf)
        ins.counts["sheets"] = len(ins.sheet_files)

    # ---------------- board + 3D models
    if pf.pcb is not None:
        if progress:
            progress(f"Reading {pf.pcb.name}")
        b = parse_board(pf.pcb, iss)
        ins.board = b
        if b.version and b.version > KICAD10_PCB:
            iss.info("pcb", f"{pf.pcb.name} was saved by a newer KiCad ({b.version}); unknown items are reported")
        for k, n in b.unsupported.items():
            iss.info("pcb", f"{n} '{k}' item(s) on the board are not converted")
        ins.counts.update(footprints=len(b.footprints), tracks=len(b.tracks) + len(b.arcs), vias=len(b.vias),
                          zones=len(b.zones), nets=len(b.nets) - 1, copper_layers=len(b.copper_layers))
        resolver = resolver or ModelResolver(pf.folder)
        needs: dict[str, ModelNeed] = {}
        stats: dict[str, int] = {}
        total = sum(len(fp.models) for fp in b.footprints)
        done = 0
        for fp in b.footprints:
            for m in fp.models:
                done += 1
                if not m.show:
                    continue
                if progress and done % 25 == 0:
                    progress(f"Resolving 3D models {done}/{total}")
                ref = resolver.resolve(m.path, [fp.embedded, b.embedded])
                stats[ref.status] = stats.get(ref.status, 0) + 1
                if ref.step is None and ref.status != "not_in_library":
                    n = needs.setdefault(m.path, ModelNeed(m.path, ref.status, ref.note))
                    n.refs.append(fp.ref)
                    if ref.status == "unresolved_var":
                        for part in ref.note.split(":", 1)[-1].split(","):
                            ins.undefined_vars.add(part.strip())
        ins.model_stats = stats
        ins.model_needs = sorted(needs.values(), key=lambda n: (-len(n.refs), n.raw))
        nofp = [fp.ref for fp in b.footprints if not fp.models and "board_only" not in fp.attrs
                and fp.pads and not fp.ref.startswith(("H", "TP", "MH", "FID"))]
        if nofp:
            iss.info("3d", f"{len(nofp)} footprint(s) have no 3D model in KiCad: " + ", ".join(nofp[:15])
                     + (" ..." if len(nofp) > 15 else ""))
    return ins


def format_report(ins: Inspection) -> str:
    L = []
    pf = ins.files
    L.append(f"Project folder : {pf.folder}")
    L.append(f"Project file   : {pf.pro.name if pf.pro else '-'}")
    L.append(f"Root schematic : {pf.root_sch.name if pf.root_sch else '-'}")
    L.append(f"Board          : {pf.pcb.name if pf.pcb else '-'}")
    if ins.counts:
        L.append("Contents       : " + ", ".join(f"{k}={v}" for k, v in ins.counts.items()))
    if ins.model_stats:
        lab = {"ok": "found locally", "embedded": "embedded in KiCad file", "downloaded": "KiCad library (downloaded)",
               "by_name": "KiCad library by file name (path variable not defined here)",
               "missing": "MISSING", "unresolved_var": "MISSING (undefined path variable)",
               "wrl_only": "VRML only (no STEP)", "not_in_library": "not in KiCad library (no model in KiCad either)"}
        L.append("3D models      : " + ", ".join(f"{lab.get(k, k)}: {v}" for k, v in ins.model_stats.items()))
    if ins.blocking:
        L.append("")
        L.append("BLOCKING:")
        L += [f"  - {b}" for b in ins.blocking]
    if ins.model_needs:
        L.append("")
        L.append("3D models that need your input (STEP files):")
        for n in ins.model_needs:
            L.append(f"  - {n.raw}")
            L.append(f"      used by {', '.join(n.refs[:10])}{' ...' if len(n.refs) > 10 else ''}  [{n.note}]")
    if ins.undefined_vars:
        L.append("")
        L.append("Undefined KiCad path variables: " + ", ".join(sorted(ins.undefined_vars)))
    other = [i for i in ins.issues.items if i.level in ("error", "warning", "missing")]
    if other:
        L.append("")
        L.append("Problems:")
        L += [f"  [{i.level}] {i.message}" for i in other]
    info = [i for i in ins.issues.items if i.level == "info"]
    if info:
        L.append("")
        L.append("Notes:")
        L += [f"  - {i.message}" for i in info]
    return "\n".join(L)
