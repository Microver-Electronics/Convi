"""End-to-end conversion: KiCad project -> Altium project (via Altium itself)."""
from __future__ import annotations

import json
from collections import Counter
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .altium.build import run_builder
from .altium.ir import IRWriter
from .convert.pcb_convert import Options as PcbOptions
from .convert.pcb_convert import PcbConverter
from .convert.project_file import kicad_pin_map, write_prjpcb
from .convert.sch_convert import SchConverter, check_house_style
from .convert.sheet_template import auto_customer, family, find_templates_dir, parse_name
from .inspect import Inspection
from .kicad.common import Issues
from .models3d import ModelResolver


@dataclass
class ConvertOptions:
    global_labels: str = "auto"      # auto / power / port (auto: ports unless hierarchical sheet pins)
    sheet_template: str = "auto"     # auto (customer named in the project name) / none / customer / .SchDot
    teardrops: str = "regions"
    origin: str = "auto"
    placeholder_bodies: bool = False
    open_in_altium: bool = True
    keep_ir: bool = True
    x2: str | None = None
    make_outputs: bool = False       # manufacturing outputs from the house OutJob template (convi/outputs.py)
    outputs: list | None = None      # container names to generate (None: every container of the template)


@dataclass
class ConvertResult:
    out_dir: Path
    project: Path | None = None
    files: list = field(default_factory=list)
    issues: Issues = field(default_factory=Issues)
    build_logs: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    seconds: float = 0.0


def resolve_sheet_templates(choice: str, project_name: str, project_dir: Path, iss: Issues):
    """(template family, smallest size) for the sheets.

    choice: 'none'; 'auto' (the customer in the templates folder whose name appears in the project
    name); a customer name ('Acme'); or a .SchDot path ('A3 - template - X.SchDot': that
    customer's family, starting at that size).  Each sheet then gets the smallest size of the
    family it fits on."""
    choice = (choice or "auto").strip().strip('"')
    if choice.lower() == "none":
        return [], "A3"
    folder = find_templates_dir(project_dir, Path(__file__).parent)
    minimum = "A3"
    if choice.lower().endswith(".schdot"):
        p = Path(choice)
        parsed = parse_name(p)
        if not p.is_file() or not parsed:
            iss.error("sch", f"Sheet template not usable: {choice} (expected an existing "
                             "'<A0..A4> - template - <customer>.SchDot'). The sheets keep the KiCad paper size.")
            return [], minimum
        minimum, customer = parsed
        folder = p.parent
    elif choice.lower() == "auto":
        customer = auto_customer(project_name, folder)
        if customer is None:
            iss.info("sch", f"No customer template in {folder} matches '{project_name}'; the sheets keep the "
                            "KiCad paper size (choose one with --sheet-template or in the GUI).")
            return [], minimum
    else:
        customer = choice
    fam = family(folder, customer)
    if not fam:
        iss.error("sch", f"No '<size> - template - {customer}.SchDot' in {folder}; the sheets keep the KiCad "
                         "paper size.")
    return fam, minimum


def _natural(s: str) -> list:
    """Sort key: TP2 before TP10."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def write_bom_check(path: Path, kicad_rows: dict, nl, bom) -> None:
    """Side-by-side KiCad / Altium BOM data for every designator."""
    import csv
    diff = {}
    for d, w, k, a in bom.differences:
        diff.setdefault(d, []).append(f"{w}: '{k}' -> '{a}'")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Status", "KiCad value", "Altium comment", "KiCad footprint", "Altium footprint",
                    "KiCad MPN", "Altium MPN", "Altium kind", "Notes"])
        kinds = {"0": "Standard", "1": "Mechanical", "2": "Graphical", "3": "Net Tie (BOM)", "4": "Net Tie (no BOM)",
                 "5": "Standard (No BOM)", "6": "Jumper"}
        for des in sorted(set(kicad_rows) | set(nl.bom), key=_natural):
            k = kicad_rows.get(des, {})
            a = nl.bom.get(des, {})
            if des in bom.excluded:
                status = "out of BOM (" + bom.excluded[des] + ")" + (" - BUT IN ALTIUM BOM" if des in bom.only_altium else "")
            elif des in bom.only_kicad:
                status = "MISSING IN ALTIUM BOM"
            elif des in bom.only_altium:
                status = "ONLY IN ALTIUM BOM"
            elif des in diff:
                status = "DIFFERENT"
            elif des in nl.bom and des in kicad_rows:
                status = "ok"
            else:
                status = "-"
            w.writerow([des, status, k.get("value", ""), a.get("comment", ""), k.get("footprint", ""),
                        a.get("footprint", ""), (k.get("params") or {}).get("MPN", ""),
                        (a.get("params") or {}).get("MPN", ""), kinds.get(a.get("kind", ""), a.get("kind", "")),
                        "; ".join(diff.get(des, []))])


def relink_markers(board, schc, footprint_of: dict, pcbc, iss: Issues) -> int:
    """Link board-only test points / fiducials / mounting holes to their schematic symbols.

    KiCad boards often carry test pads that were placed or renamed on the board ('VBUS', 'GND')
    while the schematic has TP301, TP801 ... with no footprint.  Altium's Update PCB would then
    add the schematic parts a second time.  A schematic marker without footprint and a board
    marker without symbol are the same part when footprint and value match; identical ones (five
    'GND' pads) are interchangeable and are paired in order.  The connectivity check afterwards
    confirms every pairing pin by pin."""
    from .convert.markers import marker_kind
    natural = _natural

    def fp_key(name, value):
        return (name.split(":", 1)[-1].strip().lower(), (value or "").strip().lower())

    sym_paths, sch_free = set(), {}
    for inst in schc.instances:
        for sym in inst.sch.symbols:
            kpath = schc._kicad_sym_path(inst, sym)
            sym_paths.add(kpath)
    linked = {fp.path for fp in board.footprints if fp.path in sym_paths}
    seen = set()
    for inst in schc.instances:
        for sym in inst.sch.symbols:
            kpath = schc._kicad_sym_path(inst, sym)
            ref, _ = schc._sym_ref_unit(inst, sym)
            if kpath in linked or kpath in seen or ref.startswith("#") or not sym.on_board:
                continue
            seen.add(kpath)
            if not marker_kind(ref, sym.lib_id, sym.field("Footprint")):
                continue
            sch_free.setdefault(fp_key(sym.field("Footprint"), sym.field("Value")), []).append((ref, kpath))
    board_free = {}
    for fp in board.footprints:
        if fp.path in sym_paths or not marker_kind(fp.ref, fp.lib_id):
            continue
        board_free.setdefault(fp_key(fp.lib_id, fp.value), []).append(fp)
    n, pairs = 0, []
    for key, syms in sch_free.items():
        fps = board_free.get(key, [])
        syms = sorted(syms, key=lambda r: natural(r[0]))
        fps = sorted(fps, key=lambda f: (round(f.pos[1], 3), round(f.pos[0], 3)))
        for (ref, kpath), fp in zip(syms, fps):
            pairs.append(f"{fp.ref}->{ref}")
            fp.path = kpath
            pcbc.relinked.add(id(fp))
            footprint_of[kpath] = (pcbc.fp_name_of[id(fp)], fp.ref)
            n += 1
    if n:
        iss.info("link", f"{n} board-only test point(s)/marker(s) linked to their schematic symbols by footprint "
                         f"and value; the board keeps the KiCad labels: " + ", ".join(pairs[:30])
                 + (" ..." if len(pairs) > 30 else ""))
    return n


def _kicad_fill_area(z, lay) -> float:
    tot = 0.0
    for poly in z.filled_polys.get(lay, []):
        tot += abs(sum(poly[i - 1][0] * poly[i][1] - poly[i][0] * poly[i - 1][1] for i in range(len(poly)))) / 2
    return tot


# KiCad DRC checks whose excluded markers can stand for an Altium copper DRC violation between two parts
_EXCL_COPPER = {"clearance", "hole_clearance", "shorting_items", "hole_to_hole", "copper_edge_clearance",
                "solder_mask_bridge"}


def _lost_layers(before: list, after: list):
    """Copper layers of the un-routed connections ``after`` has and ``before`` had not, read from the pads, tracks
    and arcs Altium names ('Between Pad C8-2(x,y) on B.Cu And Via (x,y) from F.Cu to B.Cu' -> B.Cu); None when one
    of them names no single-layer object (two vias, a through-hole pad: any layer may make it)."""
    def items(d):
        m = re.search(r"\bBetween (.*)$", d.strip())
        return tuple(sorted(x.strip() for x in m.group(1).split(" And "))) if m else None
    had = {items(d) for d in before if d.startswith("Un-Routed")}
    lays = set()
    for d in after:
        if not d.startswith("Un-Routed"):
            continue
        its = items(d)
        if its in had:
            continue
        found = {m.group(1) for it in (its or ()) for m in [re.match(r"(?:Pad|Track|Arc)\b.* on (\S+)$", it)]
                 if m and m.group(1) != "Multi-Layer"}
        if not found:
            return None
        lays |= found
    return lays or None


def _drc_pad_keys(detail: str) -> set:
    """'designator-pad' of the pads an Altium DRC line names ('Pad J2-(x,y)' is J2's unnamed NPTH pad)."""
    return {f"{r}-{m}" for r, m in re.findall(r"\bPad (\S+?)-([^\s(]*)\(", detail)}


def _kicad_excluded_pairs(pcbc) -> set:
    """Parts (Altium designators) between which the KiCad designer excluded a copper DRC marker
    (board.design_settings.drc_exclusions 'type|x|y|uuid1|uuid2'): e.g. a mounting hole through a pad of
    the part on the other side, deliberately waived in KiCad."""
    data = getattr(getattr(pcbc, "project", None), "data", None) or {}
    excl = ((data.get("board") or {}).get("design_settings") or {}).get("drc_exclusions") or []
    owner = {}
    for fp in pcbc.b.footprints:
        des = (getattr(pcbc, "des_override", None) or {}).get(id(fp)) or \
            (pcbc.symbol_of.get(fp.path, {}) or {}).get("ref") or fp.ref
        owner[fp.uuid] = des
        for pd in fp.pads:
            owner[pd.uuid] = des
    out = set()
    for e in excl:
        s = e[0] if isinstance(e, list) and e else e
        parts = str(s).split("|")
        if len(parts) >= 5 and parts[0] in _EXCL_COPPER:
            a, b = owner.get(parts[3]), owner.get(parts[4])
            if a and b and a != b:
                out.add(frozenset((a, b)))
    return out


def _flip_harness_connectors(cvi: Path, iss) -> None:
    """Altium's script API cannot put a harness connector's entries on its right side; the builder makes
    them all with the default (entries left) and the saved, closed sheets are patched here."""
    from .altium.schdoc_patch import set_harness_sides
    want: dict = {}
    doc = None
    from urllib.parse import unquote
    for ln in cvi.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        f = ln.split("\t")
        kv = dict(x.split("=", 1) for x in f[1:] if "=" in x)
        if f[0] == "SCHDOC":
            doc = unquote(kv.get("path", ""))
        elif f[0] == "HCONN" and kv.get("side") == "right" and doc:
            want.setdefault(doc, []).append((float(kv["x"]) / 1000.0, float(kv["y"]) / 1000.0))
    for d, flips in want.items():
        try:
            n = set_harness_sides(Path(d), flips)
        except Exception as e:      # noqa: BLE001 - the sheet stays usable, only drawn mirrored
            iss.warn("sch", f"{Path(d).name}: harness connector side not set ({e}); its entries face the wrong way")
            continue
        if n != len(flips):
            iss.warn("sch", f"{Path(d).name}: {len(flips) - n} of {len(flips)} harness connector(s) could not be "
                            "turned to face right; check them in Altium")


def _unused_via_pads(ins, pcbc, pcb_path, work, opts, iss, say):
    """KiCad 'remove unused layers' on vias (often on every via): no ring where nothing connects.  Altium's
    own Tools > Remove Unused Pad Shapes does the same (verified on a test board: 118 clearance violations ->
    0, DRC equal to KiCad's), after the pour and followed by a re-pour.  Only when every via of the board
    asks for it with one keep-end setting: the tool works on all vias or on a selection."""
    vias = list(ins.board.vias)
    if not vias or not any(v.remove_unused_layers for v in vias):
        return
    keep = {v.keep_end_layers for v in vias if v.remove_unused_layers}
    if not all(v.remove_unused_layers for v in vias) or len(keep) != 1:
        iss.warn("pcb", f"{sum(v.remove_unused_layers for v in vias)} of {len(vias)} via(s) have no ring on "
                        "unconnected layers in KiCad (remove unused layers), the rest keep it: Altium keeps every "
                        "ring - use Tools > Remove Unused Pad Shapes on the selected vias by hand")
        return
    from .pcbcheck import remove_unused_pads, repour
    say("Altium: removing unused via pad shapes (KiCad 'remove unused layers')")
    log = remove_unused_pads(pcb_path, work, x2=opts.x2, preserve=keep.pop())
    if "FATAL" in log or "answered" not in log:
        iss.warn("pcb", f"{len(vias)} via(s) have no ring on unconnected layers in KiCad, but Altium's Remove "
                        f"Unused Pad Shapes did not run ({log.strip().splitlines()[-1]}): Altium keeps the "
                        "rings, so tracks next to them can show clearance violations KiCad does not report")
        return
    if ins.board.zones:
        skip = [nm for z, nm in pcbc.polygon_names if not z.filled]
        log = repour(pcb_path, work, x2=opts.x2, skip=skip)
        if "FATAL" in log:
            iss.warn("altium", "Polygon re-pour failed: " + log.strip())
    iss.info("pcb", f"{len(vias)} via(s) have no ring on unconnected layers in KiCad (remove unused layers): "
                    "Altium's Remove Unused Pad Shapes did the same on every via, then the polygons were poured "
                    "again")


def _refine_pours(ins, pcbc, chk, pcb_path, work, opts, iss, say, extra_unrouted):
    """Two corrections of Altium's pour against KiCad's fill, tried in ONE re-pour + check (done one after the other
    they took 4 re-pours and 4 board dumps on a large test board, ~200 s of a 356 s run):
    * neck removal (KiCad min_thickness) can cut pads off a pour that KiCad connects: the polygons of nets
      that Altium leaves un-routed beyond KiCad are poured without it, kept only where that connects something
      (elsewhere it lets copper through gaps narrower than KiCad's minimum width, so islands KiCad removes
      survive and lower-priority zones lose area);
    * KiCad's min_thickness pruning keeps a short pinch between two round clearances down to ~0.9 x min_thickness
      while Altium cuts every neck under its limit (41 mm2 lost on one test board's inner layer): a solid
      polygon pouring more than max(1 mm2, 0.1 %) less than KiCad's fill gets the limit at 0.9 x, kept where it
      recovers at least a quarter of the shortfall and 1 mm2 (not where it only adds thin tapers KiCad prunes:
      +2 mm2 per layer on one test board).
    What did not help and pours more copper than KiCad is restored in a second re-pour, then checked once more."""
    from .pcbcheck import check_converted, poured_areas, repour
    if not pcbc.polygon_names:
        return chk
    filled = [(z, nm) for z, nm in pcbc.polygon_names if z.filled]
    skip = [nm for z, nm in pcbc.polygon_names if not z.filled]
    extra = extra_unrouted(chk)
    nets = set(extra)
    pnet = {nm: (pcbc.altium_net_name(z.net) or "") for z, nm in filled}
    noneck = [nm for z, nm in filled if "?" in nets or pnet[nm] in nets]
    karea = {nm: _kicad_fill_area(z, pcbc.polygon_layer.get(nm, "")) for z, nm in filled
             if z.fill_mode != "hatch" and nm not in noneck}
    low = [nm for nm, ka in karea.items()
           if nm in chk.poly_copper and ka - chk.poly_copper[nm] > max(1.0, 0.001 * ka)]
    if not noneck and not low:
        return chk
    # a changed neck limit that pours no more copper than KiCad's fill harms nothing, so only what overshoots KiCad
    # is poured again at KiCad's minimum width (on a large board that last re-pour + board check took ~55 s)
    kall = {nm: _kicad_fill_area(z, pcbc.polygon_layer.get(nm, "")) for z, nm in filled if z.fill_mode != "hatch"}

    def over(nm, cu):
        return nm not in kall or nm not in cu or cu[nm] > kall[nm] + max(1.0, 0.001 * kall[nm])
    say("Altium: re-pouring polygons (neck limit) to match KiCad's fill")
    before_n = sum(extra.values())
    before_cu = {nm: chk.poly_copper[nm] for nm in low}
    log = repour(pcb_path, work, x2=opts.x2, skip=skip, noneck=noneck, necklow=low)
    if "FATAL" in log:
        iss.warn("altium", "Polygon re-pour failed: " + log.strip())
        return chk
    first = chk
    areas = poured_areas(log)
    if not noneck and all(nm in areas for nm in low):
        # only neck limits changed: the re-pour reports the poured copper itself, so the board needs no dump and
        # DRC here - the one after the final pour decides (one dump + DRC less, ~40 s on a large board)
        cu = dict(chk.poly_copper)
        cu.update(areas)
        better = [nm for nm in low if cu[nm] - before_cu[nm] >= max(1.0, 0.25 * (karea[nm] - before_cu[nm]))]
        worse = [nm for nm in low if nm not in better and over(nm, cu)]
        if worse:
            log = repour(pcb_path, work, x2=opts.x2, skip=skip, neckon=worse)
            if "FATAL" in log:
                iss.warn("altium", "Polygon re-pour failed: " + log.strip())
        chk = check_converted(ins.board, pcbc, pcb_path, work, x2=opts.x2)
        if "drc: not run" in (chk.log or ""):
            chk.violations, chk.drc_details = first.violations, first.drc_details
            chk.unrouted_count, chk.unrouted_nets = first.unrouted_count, first.unrouted_nets
            iss.warn("drc", "Altium did not run the DRC again after the re-pour: the DRC figures are those of the "
                            "first pour (run Tools > Design Rule Check in Altium to refresh them)")
        if better:
            iss.info("pcb", "Polygons poured with the neck limit at 0.9 x KiCad's minimum width, which keeps copper "
                            "KiCad keeps behind short pinches: " +
                     ", ".join(f"{nm} ({before_cu[nm]:.1f} -> {chk.poly_copper.get(nm, cu[nm]):.1f} mm2, KiCad "
                               f"{karea[nm]:.1f})" for nm in better))
        return chk
    chk = check_converted(ins.board, pcbc, pcb_path, work, x2=opts.x2)
    if "drc: not run" in (chk.log or ""):
        # no DRC after the re-pour: nothing tells whether the neck-free pour connected anything, so it goes back
        chk.violations, chk.drc_details = first.violations, first.drc_details
        chk.unrouted_count, chk.unrouted_nets = first.unrouted_count, first.unrouted_nets
        helped = set()
    else:
        after = extra_unrouted(chk)
        helped = {n for n in nets if after.get(n, 0) < extra[n]}
    keep = [nm for nm in noneck if "?" in helped or pnet[nm] in helped]
    # a kept neck-free pour also keeps islands KiCad removes behind pinches far under its minimum width (+6 mm2
    # on one test board, +16..107 mm2 per layer on another). Where it pours more than KiCad, a neck limit of half
    # the minimum width is tried (still connects the track KiCad connects, 409 mm2 against KiCad's 412); if
    # that loses a connection, those polygons are poured neck-free again
    half = [nm for nm in keep if nm in kall and nm in chk.poly_copper and over(nm, chk.poly_copper)]
    better = [nm for nm in low if nm in chk.poly_copper and
              chk.poly_copper[nm] - before_cu[nm] >= max(1.0, 0.25 * (karea[nm] - before_cu[nm]))]
    gained = {nm: chk.poly_copper.get(nm, 0) for nm in better}
    revert = [nm for nm in noneck if nm not in keep and over(nm, chk.poly_copper)] + \
        [nm for nm in low if nm not in better and over(nm, chk.poly_copper)]
    half_cu = {nm: chk.poly_copper.get(nm, 0.0) for nm in half}

    def recheck(prev):
        c = check_converted(ins.board, pcbc, pcb_path, work, x2=opts.x2)
        if "drc: not run" in (c.log or ""):
            c.violations, c.drc_details = prev.violations, prev.drc_details
            c.unrouted_count, c.unrouted_nets = prev.unrouted_count, prev.unrouted_nets
            iss.warn("drc", "Altium did not run the DRC again after the re-pour: the DRC figures are those of the "
                            "previous pour (run Tools > Design Rule Check in Altium to refresh them)")
        return c
    if revert or half:
        log = repour(pcb_path, work, x2=opts.x2, skip=skip, neckon=revert, neckhalf=half)
        if "FATAL" in log:
            iss.warn("altium", "Polygon re-pour failed: " + log.strip())
        prev = chk
        chk = recheck(prev)
        n_prev = sum(extra_unrouted(prev).values())
        if half and ("drc: not run" in (chk.log or "") or sum(extra_unrouted(chk).values()) > n_prev):
            # half the minimum width lost a connection (or nothing tells): first only the polygons on the layer of
            # the lost connections' pads / tracks go back to neck-free, then, if that is not enough, all of them
            lays = None if "drc: not run" in (chk.log or "") else _lost_layers(prev.drc_details, chk.drc_details)
            back = [nm for nm in half if pcbc.polygon_layer.get(nm) in lays] if lays else []
            done = False
            if back and len(back) < len(half):
                log = repour(pcb_path, work, x2=opts.x2, skip=skip, noneck=back)
                if "FATAL" in log:
                    iss.warn("altium", "Polygon re-pour failed: " + log.strip())
                else:
                    trial = recheck(chk)
                    if "drc: not run" not in (trial.log or "") and sum(extra_unrouted(trial).values()) <= n_prev:
                        chk, half, done = trial, [nm for nm in half if nm not in back], True
                    else:
                        chk = trial
            if not done:
                log = repour(pcb_path, work, x2=opts.x2, skip=skip, noneck=half)
                if "FATAL" in log:
                    iss.warn("altium", "Polygon re-pour failed: " + log.strip())
                chk = recheck(chk)
                half = []
        keep = [nm for nm in keep if nm not in half]
    if half:
        iss.info("pcb", "Polygons poured with the neck limit at half KiCad's minimum width, which still makes the "
                        "connections the neck-free pour made without the islands KiCad removes: " +
                 ", ".join(f"{nm} ({half_cu[nm]:.1f} -> {chk.poly_copper.get(nm, 0.0):.1f} mm2, KiCad "
                           f"{kall.get(nm, 0.0):.1f})" for nm in half))
    if noneck:
        if keep or half:
            then = f" (then at half the minimum width: {', '.join(half)})" if half else ""
            iss.info("pcb", f"Polygons {', '.join(keep + half)} poured without neck removal{then}: with KiCad's "
                            f"minimum width as neck limit Altium left {before_n} connection(s) unrouted that KiCad "
                            f"connects (now {sum(extra_unrouted(chk).values())})")
        else:
            iss.info("pcb", f"Pouring without neck removal did not connect any of the {before_n} connection(s) "
                            "Altium leaves unrouted; KiCad's minimum width is the neck limit again wherever the "
                            "neck-free pour gave more copper than KiCad's fill")
    if better:
        iss.info("pcb", "Polygons poured with the neck limit at 0.9 x KiCad's minimum width, which keeps copper "
                        "KiCad keeps behind short pinches: " +
                 ", ".join(f"{nm} ({before_cu[nm]:.1f} -> {chk.poly_copper.get(nm, gained[nm]):.1f} mm2, KiCad "
                           f"{karea[nm]:.1f})" for nm in better))
    return chk


def convert(ins: Inspection, out_dir: Path, resolver: ModelResolver, opts: ConvertOptions | None = None,
            progress=None) -> ConvertResult:
    opts = opts or ConvertOptions()
    t0 = time.time()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "_convi"
    work.mkdir(exist_ok=True)
    _close_output_projects(out_dir, work, opts.x2)
    res = ConvertResult(out_dir=out_dir)
    iss = res.issues
    iss.extend(ins.issues)
    pf = ins.files
    name = (pf.pro or pf.root_sch or pf.pcb).stem
    schlib_name = f"{name}.SchLib"
    pcblib_name = f"{name}.PcbLib"

    def say(msg):
        if progress:
            progress(msg)

    # ------------------------------------------------------------ PCB model (footprint names first)
    pcbc = None
    if ins.board is not None:
        pcbc = PcbConverter(ins.board, resolver, iss, lib_name=pcblib_name, sch_lib_name=schlib_name,
                            project=ins.settings, text_vars=ins.settings.text_vars,
                            options=PcbOptions(teardrops=opts.teardrops, origin=opts.origin,
                                               placeholder_bodies=opts.placeholder_bodies))
        pcbc.dru_path = ins.files.dru
        pcbc._prepare_footprint_defs()
        pcbc._prepare_footprint_defs = lambda: None

    # ------------------------------------------------------------ schematic
    schc = None
    sch_docs: list[str] = []
    if pf.root_sch is not None:
        say("Converting schematics")
        footprint_of = {}
        if pcbc is not None:
            for fp in ins.board.footprints:
                if fp.path:
                    footprint_of[fp.path] = (pcbc.fp_name_of[id(fp)], fp.ref)
        schc = SchConverter(pf.root_sch, iss, project=ins.settings, footprint_of=footprint_of,
                            lib_name=schlib_name, pcblib_name=pcblib_name, global_labels=opts.global_labels)
        schc.load()
        if ins.board is not None:
            # wires of projects with net class colours: the nets come from the board's pads
            schc.pad_nets = {(fp.ref, pd.number): pd.net for fp in ins.board.footprints for pd in fp.pads
                             if pd.net and pd.number}
            schc.board_nets = list(ins.board.nets)
            schc.plan_local_net_names(schc.board_nets)
        if (((ins.settings.data or {}).get("erc") or {}).get("rule_severities") or {}).get("pin_to_pin") == "ignore":
            iss.info("sch", "KiCad's pin conflict check (pin_to_pin) is set to ignore in this project: Altium's "
                            "ERC connection matrix reports no pin-to-pin conflict either")
        if pcbc is not None:
            relink_markers(ins.board, schc, footprint_of, pcbc, iss)
        schc.template_family, schc.template_min = resolve_sheet_templates(opts.sheet_template, name,
                                                                          pf.root_sch.parent, iss)
        schc.template_out, schc.template_work = out_dir, work
        ir = IRWriter()
        for inst in schc.instances:
            schc.build_schdoc(ir, inst, out_dir / inst.out_name)
            sch_docs.append(inst.out_name)
        check_house_style(ir.lines, iss, schc.wire_class_colours)
        if schc.template_of:
            by_size: dict = {}
            for doc, t in schc.template_of.items():
                by_size.setdefault(t.size_name, []).append(doc)
            cust = next(iter(schc.template_of.values())).customer
            iss.info("sch", f"Sheet templates ({cust}): " + "; ".join(
                f"{size} x{len(docs)}" + (f" ({', '.join(docs)})" if size != schc.template_min else "")
                for size, docs in sorted(by_size.items(), reverse=True)))
        # mark docs to close after save (keeps Altium memory low on big projects)
        ir.lines = [ln + "\tclose=1" if ln.startswith("SAVESCH") else ln for ln in ir.lines]
        ir.save(work / "sch.cvi")
        libir = IRWriter()
        schc.build_schlib(libir, out_dir / schlib_name)
        libir.lines = [ln + "\tclose=1" if ln.startswith("SAVESCHLIB") else ln for ln in libir.lines]
        libir.save(work / "schlib.cvi")

    def run_jobs(jobs):
        for label, tpl, stem in jobs:
            say(f"Altium: building {label}")
            r = run_builder(tpl, work / f"{stem}.cvi", work, x2=opts.x2,
                            progress=lambda p, e, lb=label: say(f"Altium: building {lb} ({p})"))
            res.build_logs[label] = r.log
            res.stats[label] = r.stats
            if r.errors:
                iss.warn("altium", f"{label}: {r.errors} object(s) could not be created (see _convi/{stem}.log)")
            if stem == "sch":
                _flip_harness_connectors(work / "sch.cvi", iss)
            for ln in r.log.splitlines():
                if ln.startswith("PADCHECK"):
                    iss.error("pcb", "Placement check failed: " + ln[9:])

    prj = out_dir / f"{name}.PrjPcb"
    comparison = None
    compiled_uid: dict = {}
    if schc is not None:
        run_jobs([("SchLib", "builder_sch.pas", "schlib"), ("Schematics", "builder_sch.pas", "sch")])
        # compile the schematics in Altium and learn the net names Altium will use
        write_prjpcb(prj, sch_docs, None, schlib_name, None, parameters=ins.settings.text_vars,
                     hierarchy_mode=schc.hierarchy_mode, allow_port_net_names=schc.allow_port_net_names,
                     erc_pin_map=kicad_pin_map(ins.settings.data),
                     append_sheet_numbers=schc.append_sheet_numbers)
        if ins.board is not None:
            from .convert.sch_convert import plan_glue
            from .verify import compare, compile_and_dump
            for attempt in range(3):
                say("Altium: compiling schematics" + (" (after connectivity repair)" if attempt else ""))
                try:
                    nl = compile_and_dump(prj, work, x2=opts.x2)
                except Exception as e:
                    iss.warn("verify", f"Could not compile the project in Altium: {e}")
                    break
                comparison = compare(ins.board, nl, schc.ref_of, sheet_suffix=schc.append_sheet_numbers)
                if not comparison.mismatched_pins or attempt == 2:
                    break
                # KiCad joined these pins through features Altium does not have (bus aliases, group
                # buses, ...): glue the pieces with power ports named after the KiCad net.
                n = plan_glue(schc, comparison)
                if n == 0:
                    break
                iss.info("sch", f"Connectivity repair: added {n} net-tie power port(s) for KiCad nets joined "
                                "through bus aliases / group buses")
                ir = IRWriter()
                for inst in schc.instances:
                    schc.build_schdoc(ir, inst, out_dir / inst.out_name)
                ir.lines = [ln + "\tclose=1" if ln.startswith("SAVESCH") else ln for ln in ir.lines]
                ir.save(work / "sch.cvi")
                run_jobs([("Schematics", "builder_sch.pas", "sch")])
            if comparison is not None:
                res.stats["connectivity"] = comparison.summary()
                if comparison.renamed:
                    iss.warn("verify", f"{len(comparison.renamed)} net name(s) from KiCad are different in Altium: "
                             + ", ".join(f"{k} -> {v}" for k, v in list(comparison.renamed.items())[:10]))
                pcbc.netmap = dict(comparison.net_map)
                compiled_uid = {des: v[0] for des, v in nl.components.items()}
                if comparison.mismatched_pins:
                    iss.warn("verify", f"{len(comparison.mismatched_pins)} pin(s) are connected differently in the "
                                       "Altium schematic than on the KiCad board (see convi_report.json)")
                if comparison.shorts:
                    iss.error("verify", f"{len(comparison.shorts)} Altium net(s) join several KiCad nets (short in "
                                        "the schematic): " + "; ".join(
                                            f"{an} = {' + '.join(kns)}" for an, kns in list(comparison.shorts.items())[:8]))
                po, so = comparison.pcb_only_components, comparison.sch_only_components
                if po and so:
                    iss.warn("link", f"{len(so)} schematic part(s) have no footprint and {len(po)} footprint(s) have "
                                     "no schematic part (references renamed on the KiCad board?). Do NOT run "
                                     "Design > Update PCB before linking them: Altium would add the schematic "
                                     f"parts again. Schematic only: {', '.join(so[:10])}; board only: "
                                     f"{', '.join(po[:10])}")

    # ------------------------------------------------------------ PCB
    if pcbc is not None:
        say("Converting board")
        if schc is not None:
            sym_of = {}
            for kpath, upath in schc.uid_path_of.items():
                ref = schc.ref_of.get(kpath, "")
                # Altium's compiled link path is authoritative (multi-part components)
                upath = compiled_uid.get(ref, upath)
                sym_of[kpath] = {"uid": upath, "libref": schc.libref_of.get(kpath, ""), "ref": ref}
            pcbc.symbol_of = sym_of
            unlinked = [fp.ref for fp in ins.board.footprints if fp.path and fp.path not in sym_of
                        and "board_only" not in fp.attrs]
            if unlinked:
                iss.warn("link", f"{len(unlinked)} footprint(s) have no matching schematic symbol: "
                                 + ", ".join(unlinked[:15]))
        ir = IRWriter()
        pcbc.build_pcb(ir, out_dir / f"{name}.PcbDoc")
        mech = IRWriter()
        pcbc.emit_mech(mech)
        idx = next((i for i, l in enumerate(ir.lines) if l.startswith(("NET\t", "OUTLINE"))), 3)
        ir.lines[idx:idx] = mech.lines
        ir.lines = [ln + "\tclose=1" if ln.startswith("SAVE") and not ln.startswith("SAVELIB") else ln
                    for ln in ir.lines]
        ir.save(work / "pcb.cvi")
        lib = IRWriter()
        pcbc.build_pcblib(lib, out_dir / pcblib_name)
        lib.lines[2:2] = mech.lines
        lib.lines = [ln + "\tclose=1" if ln.startswith("SAVELIB") else ln for ln in lib.lines]
        lib.save(work / "pcblib.cvi")
        if pcbc.unreached_polygons:
            iss.info("pcb", "Polygons that no pad, via or track of their net reaches yet keep all their copper, as "
                            "KiCad keeps such a fill (Altium would remove it as dead copper): " +
                            ", ".join(pcbc.unreached_polygons))
        res.stats["bodies"] = pcbc.stats["bodies"]
        res.stats["bodies_missing"] = pcbc.stats["bodies_missing"]

    if pcbc is not None:
        run_jobs([("PcbLib", "builder_pcb.pas", "pcblib"), ("PcbDoc", "builder_pcb.pas", "pcb")])
        # creepage distance: not settable from scripts, so it is written into the saved (closed) PcbDoc
        # before the pour; APPLYTOPOLYGONPOUR off because KiCad checks creepage in DRC only
        crp = getattr(pcbc, "creepage_rules", [])
        if crp:
            from .altium.pcbdoc_patch import read_creepage, set_creepage
            pcb_path = out_dir / f"{name}.PcbDoc"
            want = {rn: (mmv, f"Convi: KiCad rule '{kn}', creepage {mmv:g} mm") for rn, mmv, kn in crp}
            try:
                set_creepage(pcb_path, want, apply_to_pour=False)
                got = read_creepage(pcb_path)
                bad = [rn for rn, (mmv, _) in want.items()
                       if not got.get(rn) or abs(float((got[rn]["CHECKDISTANCE"] or "0mil")[:-3]) * 0.0254 - mmv) > 1e-4]
                if bad:
                    raise ValueError("not written: " + ", ".join(bad))
                iss.info("rules", "Creepage distance written into the PcbDoc (scripts cannot set it): " +
                         ", ".join(f"{rn} = {mmv:g} mm" for rn, mmv, _ in crp) +
                         "; 'Apply to polygon pour' is off, as KiCad checks creepage in DRC only")
            except Exception as e:
                for rn, mmv, kn in crp:
                    iss.warn("rules", f"Creepage rule '{rn}' (KiCad '{kn}'): Convi could not write the distance "
                                      f"into the PcbDoc ({e}) - open Design > Rules > Creepage and enter {mmv:g} mm")
        if ins.board.zones:
            say("Altium: pouring polygons")
            from .pcbcheck import repour
            skip = [nm for z, nm in pcbc.polygon_names if not z.filled]
            log = repour(out_dir / f"{name}.PcbDoc", work, x2=opts.x2, skip=skip)
            if "FATAL" in log:
                iss.warn("altium", "Polygon re-pour failed: " + log.strip())
        _unused_via_pads(ins, pcbc, out_dir / f"{name}.PcbDoc", work, opts, iss, say)

    # ------------------------------------------------------------ project file
    variants = []
    if schc is not None:
        dnp, n_kicad = [], 0
        for inst in schc.instances:
            for sym in inst.sch.symbols:
                ref, _ = schc._sym_ref_unit(inst, sym)
                if sym.dnp or ref in schc.markers:
                    up = schc.uid_path_of.get(schc._kicad_sym_path(inst, sym))
                    # a variation links by the path Altium compiled (the PCB's SourceUniqueId, e.g.
                    # '\\UID' for a sheet used once); the sheet-symbol path gives "Incorrect link in
                    # project variant" for every part although the variant itself still works
                    up = compiled_uid.get(ref, up)
                    if up and (ref, up) not in dnp:
                        dnp.append((ref, up))
                        n_kicad += bool(sym.dnp)
        if dnp:
            variants.append(("DNP", dnp))
            n_mark = len(dnp) - n_kicad
            iss.info("variants", f"Variant 'DNP': {len(dnp)} part(s) Not Fitted ({n_kicad} KiCad DNP, {n_mark} test "
                                 "point(s) / mounting hole(s) / fiducial(s), which are also out of the BOM)")
    plan = None
    if opts.make_outputs and pcbc is not None:
        say("Preparing manufacturing outputs")
        try:
            from .outputs import drill_table_rows, find_output_templates, prepare
            tpl = find_output_templates(Path(pf.pro or pf.pcb).parent)
            plan = prepare(tpl, out_dir, name, out_dir / f"{name}.PcbDoc", pcbc.lm.used_mech(),
                           pcbc.board_size_mm, "DNP" if variants else None, opts.outputs, iss,
                           drill_rows=drill_table_rows(ins.board), copper_layers=len(pcbc.lm.copper))
            if plan is not None:
                iss.info("outputs", f"Output templates: {tpl.outjob}" +
                         (f" and {tpl.draftsman}" if tpl.draftsman else " (no Draftsman template)"))
        except Exception as e:
            plan = None
            iss.warn("outputs", f"Manufacturing outputs could not be prepared: {e}")
    extra_docs = ([plan.outjob.name] + ([plan.draftsman.name] if plan.draftsman else [])) if plan else []
    write_prjpcb(prj, sch_docs, f"{name}.PcbDoc" if pcbc else None, schlib_name if schc else None,
                 pcblib_name if pcbc else None, parameters=ins.settings.text_vars, variants=variants,
                 extra_docs=extra_docs,
                 hierarchy_mode=schc.hierarchy_mode if schc else 2,
                 allow_port_net_names=schc.allow_port_net_names if schc else False,
                 erc_pin_map=kicad_pin_map(ins.settings.data),
                 append_sheet_numbers=schc.append_sheet_numbers if schc else False,
                 cross_refs=bool(((ins.settings.data or {}).get("schematic", {}).get("drawing", {}) or {})
                                 .get("intersheets_ref_show")))
    res.project = prj
    # final check: what would Altium's "Update PCB" change?
    eco = None
    bom = None
    if schc is not None and pcbc is not None:
        say("Altium: verifying project (compile + ECO preview)")
        from .verify import compare_bom, compile_and_dump, eco_preview
        try:
            nl = compile_and_dump(prj, work, x2=opts.x2, pcb=out_dir / f"{name}.PcbDoc")
            eco = eco_preview(nl, schc.not_on_board)
            res.stats["eco_preview"] = eco.summary()
            if eco.total:
                iss.warn("verify", eco.summary() + " (details in convi_report.json)")
            else:
                iss.info("verify", "Schematic and PCB are in sync: Update PCB would make no changes")
            bom = compare_bom(schc.bom_rows, nl, "DNP")
            res.stats["bom"] = bom.summary()
            write_bom_check(work / "bom_check.csv", schc.bom_rows, nl, bom)
            if not nl.bom:
                iss.warn("verify", "BOM check skipped: Altium returned no component data")
            elif bom.ok:
                iss.info("verify", bom.summary())
            else:
                iss.warn("verify", bom.summary() + ". " + "; ".join(
                    [f"only in KiCad: {', '.join(bom.only_kicad[:10])}"] * bool(bom.only_kicad)
                    + [f"only in Altium: {', '.join(bom.only_altium[:10])}"] * bool(bom.only_altium)
                    + [f"{d}: {w} '{k}' -> '{a}'" for d, w, k, a in bom.differences[:8]])
                         + " (all rows in _convi/bom_check.csv)")
        except Exception as e:
            iss.warn("verify", f"Final verification failed: {e}")
    if pcbc is not None:
        say("Altium: checking placement/routing against KiCad + DRC")
        try:
            from .pcbcheck import check_converted
            chk = check_converted(ins.board, pcbc, out_dir / f"{name}.PcbDoc", work, x2=opts.x2)
            # a board that is not fully routed in KiCad already has open connections: Altium reports them
            # too, so only what Altium reports beyond KiCad (per net) is a conversion difference
            kicad_open = Counter()
            if chk.unrouted_count:
                say("Checking the KiCad board's own open connections")
                try:
                    from .kicad.connectivity import open_connections
                    kicad_open = Counter({(pcbc.altium_net_name(n) or n): k
                                          for n, k in open_connections(ins.board).items()})
                except Exception as e:
                    iss.warn("verify", f"KiCad connectivity check failed: {e}")

            def extra_unrouted(c):
                from .pcbcheck import NO_NET
                return {n: k - kicad_open.get(n, 0) for n, k in c.unrouted_count.items()
                        if k > kicad_open.get(n, 0) and n != NO_NET}
            chk = _refine_pours(ins, pcbc, chk, out_dir / f"{name}.PcbDoc", work, opts, iss, say, extra_unrouted)
            k_total = sum(kicad_open.values())
            if k_total:
                iss.info("drc", f"The KiCad board itself has {k_total} open connection(s) on "
                                f"{len(kicad_open)} net(s) (not routed in KiCad); Altium reports them as un-routed too")
            extra = extra_unrouted(chk)
            res.stats["pcb_check"] = chk.summary()
            warn_rules = getattr(pcbc, "rule_kicad_warning", None) or {}
            hits = [(r, n) for r, n in chk.violations.most_common() if r in warn_rules]
            if hits:
                iss.info("drc", "Altium DRC violations that KiCad itself reports only as warnings (the project sets "
                                "these checks to 'warning'; Altium has no warning level): " +
                         ", ".join(f"{r} {n} ({warn_rules[r]})" for r, n in hits))
            if chk.pad_errors or chk.tracks_missing or chk.vias_missing or chk.arcs_missing or chk.pad_net_errors:
                iss.warn("verify", "PCB geometry differs from KiCad: " + chk.summary() +
                         " (details in _convi/pcbcheck.json)")
            if not (ins.board.tracks or ins.board.arcs or ins.board.vias):
                iss.info("drc", "The KiCad board is not routed yet: Altium's un-routed net check was skipped")
            excl_pairs = _kicad_excluded_pairs(pcbc)
            waived = []
            hole_too = []
            hole_exact = set(getattr(pcbc, "hole_exact_pads", None) or ())
            # clearance violations are listed under the rule's name (e.g. 'KiCad_clearance3_1' once a custom GND
            # rule is converted), not only the default 'Clearance'; rules KiCad reports as warnings are listed above
            clear_names = {r["altium"] for r in (getattr(pcbc, "rule_report", None) or [])
                           if r.get("kind") == "clearance" and r.get("detail") == "created"} | {"Clearance"}
            for kind, label in (("ShortCircuit", "short circuit"), ("UnRoutedNet", "unrouted connection"),
                                ("Clearance", "clearance violation")):
                if kind == "Clearance":
                    n = sum(v for r, v in chk.violations.items() if r in clear_names and r not in warn_rules)
                else:
                    n = chk.violations.get(kind, 0)
                if kind == "UnRoutedNet" and n and chk.unrouted_count:
                    from .pcbcheck import NO_NET
                    dead = chk.unrouted_count.get(NO_NET, 0)
                    if dead:
                        iss.warn("drc", f"Altium DRC: {dead} copper object(s) without a net reported as dead copper "
                                        "(KiCad keeps copper on an unconnected pin's own 'unconnected-' net; Altium "
                                        "gives such a pin no net). Assign a net in Altium or delete the copper "
                                        "(full list in _convi/drc_report.txt)")
                    n = sum(extra.values())
                    if n:
                        ex = ", ".join(f"{k} ({v})" for k, v in sorted(extra.items())[:10])
                        iss.warn("drc", f"Altium DRC: {n} unrouted connection(s) that KiCad connects - mostly "
                                        f"pads that KiCad's zone fill reaches through very narrow copper but "
                                        f"Altium's polygon pour does not. Nets: {ex} (full list in "
                                        f"_convi/drc_report.txt)")
                    continue
                if n:
                    key = {"ShortCircuit": "Short-Circuit", "UnRoutedNet": "Un-Routed", "Clearance": "Clearance"}[kind]
                    det = [d for d in chk.drc_details if d.startswith(key)]
                    if excl_pairs and kind != "UnRoutedNet":
                        keep = []
                        for d in det:
                            refs = re.findall(r"\bPad ([^\s(-]+)-", d)
                            if len(refs) == 2 and frozenset(refs) in excl_pairs:
                                waived.append((label, "/".join(refs)))
                            else:
                                keep.append(d)
                        n -= len(det) - len(keep)
                        det = keep
                        if n <= 0:
                            continue
                    if kind == "Clearance" and hole_exact:
                        # a non-plated hole whose Altium pad is exactly the hole: Convi's hole clearance rule
                        # measures what KiCad's hole clearance check measures, so KiCad's DRC has the pair too
                        # (e.g. USB-C and connector pegs 0.19 mm from the pads)
                        keep = []
                        for d in det:
                            (hole_too if _drc_pad_keys(d) & hole_exact else keep).append(d)
                        n -= len(det) - len(keep)
                        det = keep
                        if n <= 0:
                            continue
                    ex = det[:5]
                    hint = (" - mostly pads that KiCad's zone fill reaches through very narrow copper but "
                            "Altium's polygon pour does not" if kind == "UnRoutedNet" else "")
                    iss.warn("drc", f"Altium DRC: {n} {label}(s){hint}. First: " + " | ".join(ex) +
                             " (full list in _convi/drc_report.txt)")
            if hole_too:
                hc = getattr(pcbc, "hole_clearance_mm", 0.0) or 0.0
                iss.info("drc", f"{len(hole_too)} Altium clearance violation(s) between a non-plated hole and copper "
                                f"that the KiCad board has too: KiCad's DRC reports them as hole clearance "
                                f"violations (Board Setup > Constraints: hole clearance {hc:g} mm from the drill "
                                f"edge). First: " + " | ".join(hole_too[:3]))
            if waived:
                iss.info("drc", f"{len(waived)} Altium DRC violation(s) are ones the KiCad designer excluded in "
                                "KiCad's DRC (Board Setup > Violation Severity exclusions): " +
                         ", ".join(f"{lab} {p}" for lab, p in waived[:10]))
        except Exception as e:
            iss.warn("verify", f"PCB check failed: {e}")
    if plan is not None and plan.containers:
        say("Altium: generating manufacturing outputs")
        try:
            from .outputs import run_outputs
            orep = run_outputs(plan, prj, out_dir, work, x2=opts.x2, progress=say)
            res.stats["outputs"] = orep["summary"]
            for c in orep["failed"]:
                iss.warn("outputs", f"Output container '{c}' did not finish in Altium (see _convi/outputs.log)")
            if orep["files"]:
                iss.info("outputs", orep["summary"])
            else:
                iss.warn("outputs", "Altium wrote no output files (see _convi/outputs.log)")
        except Exception as e:
            iss.warn("outputs", f"Manufacturing outputs failed: {e}")
    elif plan is not None:
        iss.info("outputs", f"{plan.outjob.name} added to the project; no output container was selected to run")
    if opts.open_in_altium:
        from .altium.runner import run_script
        tpl = (Path(__file__).resolve().parent / "altium" / "open_project.pas").read_text(encoding="utf-8")
        scr = work / "open_project.pas"
        scr.write_text(tpl.replace("%PRJ%", str(prj)).replace("%DONE%", str(work / "open.done"))
                       .replace("%STARTED%", str(work / "open.started")), encoding="utf-8")
        try:
            run_script(scr, "Main", work / "open.done", timeout=120, x2=opts.x2, started=work / "open.started")
        except Exception:
            pass
    res.files = sorted(p.name for p in out_dir.iterdir() if p.is_file())
    if not opts.keep_ir:
        shutil.rmtree(work, ignore_errors=True)
    res.seconds = time.time() - t0
    (out_dir / "convi_report.json").write_text(json.dumps({
        "source": str(pf.folder), "project": str(prj), "seconds": round(res.seconds, 1), "stats": res.stats,
        "issues": [i.__dict__ for i in iss.items],
        "pin_mismatches": comparison.mismatched_pins if comparison else [],
        "shorted_nets": comparison.shorts if comparison else {},
        "net_map": comparison.net_map if comparison else {},
        "renamed_nets": comparison.renamed if comparison else {},
        "bom": bom.__dict__ if bom else None,
        "rules": getattr(pcbc, "rule_report", None) if pcbc is not None else None,
        "eco_preview": eco.__dict__ if eco else None}, indent=2, default=list), encoding="utf-8")
    return res


def _close_output_projects(out_dir: Path, work: Path, x2=None):
    """Close projects from a previous conversion into the same folder (avoids Altium's 'Reload?' prompts)."""
    from .altium.runner import is_altium_running, run_script
    if not is_altium_running():
        return
    tpl = (Path(__file__).resolve().parent / "altium" / "close_project.pas").read_text(encoding="utf-8")
    scr = work / "close_project.pas"
    folder = str(out_dir).rstrip("\\") + "\\"
    scr.write_text(tpl.replace("%DIR%", folder).replace("%DONE%", str(work / "close.done"))
                   .replace("%STARTED%", str(work / "close.started")), encoding="utf-8")
    try:
        run_script(scr, "Main", work / "close.done", timeout=300, x2=x2, started=work / "close.started")
    except Exception:
        pass
