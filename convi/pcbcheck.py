"""Read back the converted PcbDoc from Altium and compare its copper geometry with KiCad.

Checks every pad (position, net), track, arc and via, and summarises Altium DRC violations.
"""
from __future__ import annotations

import json
import re
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .altium.ir import mm
from .altium.runner import run_script

HERE = Path(__file__).resolve().parent / "altium"
TOL = mm(0.005)          # 5 um



NO_NET = "(no net)"   # un-routed DRC entries for dead copper that has no net

@dataclass
class PcbCheck:
    pads_total: int = 0
    pads_ok: int = 0
    pad_errors: list = field(default_factory=list)
    pad_net_errors: list = field(default_factory=list)
    tracks_kicad: int = 0
    tracks_altium: int = 0
    tracks_missing: list = field(default_factory=list)
    arcs_kicad: int = 0
    arcs_altium: int = 0
    arcs_missing: list = field(default_factory=list)
    vias_kicad: int = 0
    vias_altium: int = 0
    vias_missing: list = field(default_factory=list)
    polygons: list = field(default_factory=list)
    poly_copper: dict = field(default_factory=dict)   # polygon name -> poured copper mm2 (solid pours)
    violations: Counter = field(default_factory=Counter)
    drc_details: list = field(default_factory=list)   # "Rule: description" lines from the DRC report
    unrouted_nets: set = field(default_factory=set)   # nets named in Altium's un-routed net violations
    unrouted_count: Counter = field(default_factory=Counter)   # Altium net -> un-routed connections
    log: str = ""

    def summary(self) -> str:
        v = ", ".join(f"{k}: {n}" for k, n in self.violations.most_common()) or "none"
        return (f"pads {self.pads_ok}/{self.pads_total} at exact position (net errors {len(self.pad_net_errors)}), "
                f"tracks {self.tracks_kicad - len(self.tracks_missing)}/{self.tracks_kicad}, "
                f"arcs {self.arcs_kicad - len(self.arcs_missing)}/{self.arcs_kicad}, "
                f"vias {self.vias_kicad - len(self.vias_missing)}/{self.vias_kicad}, DRC: {v}")


def _run_pas(template: str, work: Path, stem: str, x2=None, timeout=3600, **repl) -> dict:
    """Run altium/<template> with %PLACEHOLDERS% filled; returns the paths used."""
    work.mkdir(parents=True, exist_ok=True)
    paths = {k: work / f"{stem}.{ext}" for k, ext in
             (("OUT", "txt"), ("LOG", "log"), ("DONE", "done"), ("STARTED", "started"))}
    for f in paths.values():
        f.unlink(missing_ok=True)
    src = (HERE / template).read_text(encoding="utf-8")
    for k, v in {**paths, **repl}.items():
        src = src.replace(f"%{k}%", str(v))
    script = work / f"{stem}_run.pas"
    script.write_text(src, encoding="utf-8")
    run_script(script, "Main", paths["DONE"], timeout=timeout, x2=x2, started=paths["STARTED"])
    return paths


def repour(pcb: Path, work: Path, x2=None, skip=(), noneck=(), neckon=(), necklow=(), neckhalf=()) -> str:
    """Pour all polygons of a built PcbDoc (priority order) in a separate pass and save it.
    noneck: polygons poured without neck removal; neckon: neck removal restored at the minimum width;
    necklow: neck removal at 0.9 x the minimum width; neckhalf: at 0.5 x the minimum width."""
    lst = lambda xs: "|" + "|".join(xs) + "|"
    paths = _run_pas("repour.pas", work, "repour", x2=x2, PCB=Path(pcb).resolve(),
                     SKIP=lst(skip), NONECK=lst(noneck), NECKON=lst(neckon), NECKLOW=lst(necklow),
                     NECKHALF=lst(neckhalf))
    return paths["LOG"].read_text(encoding="utf-8", errors="replace") if paths["LOG"].exists() else ""


def poured_areas(log: str) -> dict:
    """Polygon name -> poured copper mm2 from a repour() log (AREA lines; -1 = unreadable is left out)."""
    out = {}
    for ln in (log or "").splitlines():
        f = ln.split("\t")
        if len(f) == 3 and f[0] == "AREA" and f[2].strip().lstrip("-").isdigit() and int(f[2]) >= 0:
            out[f[1]] = int(f[2]) / 1000.0
    return out


def remove_unused_pads(pcb: Path, work: Path, x2=None, preserve=True) -> str:
    """Altium's Remove Unused Pad Shapes on every via (KiCad 'remove unused layers'); the dialog is answered by
    runner.UNUSED_PADS (set here).  Returns the script log; 'answered' in the result says the dialog was
    driven as asked."""
    from .altium import runner
    runner.UNUSED_PADS.clear()
    runner.UNUSED_PADS.update(preserve=bool(preserve), result="")
    paths = _run_pas("unusedpads.pas", work, "unusedpads", x2=x2, PCB=Path(pcb).resolve(),
                     PRESERVE="1" if preserve else "0")
    log = paths["LOG"].read_text(encoding="utf-8", errors="replace") if paths["LOG"].exists() else ""
    return log + "\n" + (runner.UNUSED_PADS.get("result") or "dialog not seen")


def dump_pcb(pcb: Path, work: Path, drc=True, x2=None, timeout=3600, unrouted=True) -> list[list[str]]:
    paths = _run_pas("pcbcheck.pas", work, "pcbcheck", x2=x2, timeout=timeout, PCB=Path(pcb).resolve(),
                     DRC="1" if drc else "0", DRCREPORT=work / "drc_report.txt",
                     NOUNROUTED="0" if unrouted else "1")
    out = paths["OUT"]
    if not out.exists():
        return []
    return [ln.split("	") for ln in out.read_text(encoding="utf-8", errors="replace").splitlines()]


def _layer_int(code: str) -> int:
    if code == "T":
        return 1
    if code == "B":
        return 32
    if code.startswith("M") and not code.startswith("MECH"):
        return 1 + int(code[1:])
    return -1


def check(conv, rows, ref_of=None) -> PcbCheck:
    """conv: a PcbConverter built for the same board (same offset); rows: dump_pcb output."""
    res = PcbCheck()
    ref_of = ref_of or {}
    # ---------------- Altium side
    apads = defaultdict(list)
    atracks = Counter()
    aarcs = []
    avias = Counter()
    for r in rows:
        k = r[0]
        if k == "P" and len(r) >= 9:
            apads[(r[1], r[2])].append((int(r[3]), int(r[4]), r[7]))
        elif k == "T" and len(r) >= 8:
            a = (round(int(r[1]) / 100), round(int(r[2]) / 100))
            b = (round(int(r[3]) / 100), round(int(r[4]) / 100))
            atracks[(min(a, b), max(a, b), round(int(r[5]) / 100), int(r[6]))] += 1
            res.tracks_altium += 1
        elif k == "A" and len(r) >= 9:
            aarcs.append((int(r[1]), int(r[2]), int(r[3]), int(r[7])))
            res.arcs_altium += 1
        elif k == "V" and len(r) >= 8:
            avias[(round(int(r[1]) / 100), round(int(r[2]) / 100), round(int(r[3]) / 100))] += 1
            res.vias_altium += 1
        elif k == "G" and len(r) >= 5:
            res.polygons.append((r[1], r[3], r[4]))
            if len(r) >= 6 and r[5].strip().lstrip("-").isdigit() and int(r[5]) >= 0:
                res.poly_copper[r[1]] = int(r[5]) / 1000.0
        elif k == "X":
            res.violations[r[1] if len(r) > 1 else "?"] += 1
            if len(r) > 3 and (r[2] == "16" or "UnRouted" in r[1]):
                m = re.search(r"Net\s+(\S+)\s+Between", r[3]) or re.search(r":\s*Net\s+(\S+)", r[3])
                res.unrouted_nets.add(m.group(1) if m else "?")
                res.unrouted_count[m.group(1) if m else "?"] += 1
    # ---------------- pads
    for fp in conv.b.footprints:
        des = conv.designator(fp)
        for pd in fp.pads:
            cu = [l for l in pd.layers if l.endswith(".Cu")]
            if not cu and pd.type != "np_thru_hole":
                continue
            res.pads_total += 1
            ex, ey = conv.X(pd.pos[0]), conv.Y(pd.pos[1])
            cands = apads.get((des, pd.number), [])
            if not cands:
                res.pad_errors.append((des, pd.number, "missing"))
                continue
            best = min(cands, key=lambda c: abs(c[0] - ex) + abs(c[1] - ey))
            d = math.hypot(best[0] - ex, best[1] - ey)
            if d <= TOL:
                res.pads_ok += 1
            else:
                res.pad_errors.append((des, pd.number, f"off by {d / mm(1):.3f} mm"))
            want = conv.altium_net_name(pd.net) or ""
            if pd.net and want != best[2]:
                res.pad_net_errors.append((des, pd.number, pd.net, best[2]))
    # ---------------- tracks
    for t in conv.b.tracks:
        code = conv.lm.get(t.layer)
        if not code or math.dist(t.start, t.end) < 0.001:     # Altium drops sub-micron stubs
            continue
        res.tracks_kicad += 1
        a = (round(conv.X(t.start[0]) / 100), round(conv.Y(t.start[1]) / 100))
        b = (round(conv.X(t.end[0]) / 100), round(conv.Y(t.end[1]) / 100))
        key = (min(a, b), max(a, b), round(mm(t.width) / 100), _layer_int(code))
        if atracks.get(key, 0) > 0:
            atracks[key] -= 1
        else:
            res.tracks_missing.append((t.layer, t.start, t.end, t.net))
    # ---------------- arcs (center + radius + layer)
    for t in conv.b.arcs:
        code = conv.lm.get(t.layer)
        if not code:
            continue
        res.arcs_kicad += 1
        ar = conv._arc_alt(t.start, t.mid, t.end)
        if ar is None:
            continue
        (cx, cy), r, _, _ = ar
        hit = next((i for i, a in enumerate(aarcs) if abs(a[0] - cx) <= 2 * TOL and abs(a[1] - cy) <= 2 * TOL
                    and abs(a[2] - r) <= 2 * TOL and a[3] == _layer_int(code)), None)
        if hit is None:
            res.arcs_missing.append((t.layer, t.start, t.end, t.net))
        else:
            aarcs.pop(hit)
    # ---------------- vias
    for v in conv.b.vias:
        res.vias_kicad += 1
        key = (round(conv.X(v.pos[0]) / 100), round(conv.Y(v.pos[1]) / 100), round(mm(v.size) / 100))
        if avias.get(key, 0) > 0:
            avias[key] -= 1
        else:
            res.vias_missing.append((v.pos, v.size, v.net))
    return res


def check_converted(board, conv, pcb: Path, work: Path, x2=None, drc=True) -> PcbCheck:
    # a board without routing: skip Altium's un-routed check (it would hide everything else)
    rows = dump_pcb(pcb, work, drc=drc, x2=x2, unrouted=bool(board.tracks or board.arcs or board.vias))
    res = check(conv, rows)
    rep = work / "drc_report.txt"
    if rep.exists():
        for ln in rep.read_text(encoding="utf-8", errors="replace").splitlines():
            if "|||" in ln:
                res.drc_details.append(ln.split("|||", 1)[1].strip())
    # the dump's violation rows carry no description in AD24: name the un-routed nets from the report
    if drc and res.unrouted_count.get("?"):
        named = Counter()
        for d in res.drc_details:
            m = re.match(r"Un-Routed Net Constraint:\s*Net\s+(\S+)\s+Between", d)
            if m:
                named[m.group(1)] += 1
            elif d.startswith("Un-Routed Net Constraint:") and "Net Not Assigned" in d:
                # copper without a net (a KiCad zone / track on an unconnected pin's own net, e.g. an
                # exposed GL pin): not a connection the pour misses, so no reason to re-pour anything
                named[NO_NET] += 1
        if sum(named.values()) == sum(res.unrouted_count.values()):
            res.unrouted_count = named
            res.unrouted_nets = set(named)
    log = work / "pcbcheck.log"
    res.log = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    (work / "pcbcheck.json").write_text(json.dumps({
        "summary": res.summary(), "pad_errors": res.pad_errors[:200], "pad_net_errors": res.pad_net_errors[:200],
        "tracks_missing": [str(x) for x in res.tracks_missing[:200]], "arcs_missing": [str(x) for x in res.arcs_missing[:200]],
        "vias_missing": [str(x) for x in res.vias_missing[:200]], "polygons": res.polygons,
        "violations": dict(res.violations), "drc": res.drc_details}, indent=1), encoding="utf-8")
    return res
