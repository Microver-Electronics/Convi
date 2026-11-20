"""Compile the converted project in Altium and compare its connectivity with KiCad."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .altium.runner import run_script

HERE = Path(__file__).resolve().parent / "altium"


@dataclass
class AltiumNetlist:
    nets: dict = field(default_factory=dict)         # name -> set((des, pin))
    pin_net: dict = field(default_factory=dict)      # (des, pin) -> name
    components: dict = field(default_factory=dict)   # des -> (uid_path, footprint)
    pcb_components: dict = field(default_factory=dict)   # des -> (uid_path, pattern, kind)
    pcb_pad_net: dict = field(default_factory=dict)      # (des, pad) -> net
    bom: dict = field(default_factory=dict)          # des -> {comment, kind, footprint, params}
    variants: dict = field(default_factory=dict)     # variant description -> {des: variation kind}
    log: str = ""


_U_ESC = re.compile(r"\{U\+([0-9A-F]{4})\}")


def _unesc(text: str) -> str:
    """verify.pas writes non-ASCII characters as {U+XXXX} (Altium saves text files in the ANSI code page)."""
    return _U_ESC.sub(lambda m: chr(int(m.group(1), 16)), text)


def _norm_uid(u: str) -> str:
    while "\\\\" in u:
        u = u.replace("\\\\", "\\")
    return u


def compile_and_dump(prj: Path, work: Path, x2=None, timeout=1800, pcb: Path | None = None) -> AltiumNetlist:
    work.mkdir(parents=True, exist_ok=True)
    out = work / "netlist.txt"
    log = work / "verify.log"
    done = work / "verify.done"
    started = work / "verify.started"
    for p in (out, log, done):
        if p.exists():
            p.unlink()
    src = (HERE / "verify.pas").read_text(encoding="utf-8")
    src = (src.replace("%PRJ%", str(Path(prj).resolve())).replace("%OUT%", str(out))
           .replace("%LOG%", str(log)).replace("%DONE%", str(done))
           .replace("%STARTED%", str(started)).replace("%PCB%", str(Path(pcb).resolve()) if pcb else ""))
    script = work / "verify_run.pas"
    script.write_text(src, encoding="utf-8")
    run_script(script, "Main", done, timeout=timeout, x2=x2, started=started)
    nl = AltiumNetlist(log=log.read_text(encoding="utf-8", errors="replace") if log.exists() else "")
    if not out.exists():
        return nl
    for ln in _unesc(out.read_text(encoding="utf-8", errors="replace")).splitlines():
        parts = ln.split("\t")
        if parts[0] == "N" and len(parts) >= 3:
            name = parts[1]
            pins = set()
            for item in parts[2].split("\x02"):
                if "\x01" in item:
                    d, p = item.split("\x01", 1)
                    pins.add((d, p))
                    nl.pin_net[(d, p)] = name
            nl.nets[name] = pins
        elif parts[0] == "C" and len(parts) >= 3:
            path, _, uid = parts[2].partition("|")
            segs = [x for x in path.split("\\") if x]
            last = uid.split("\\")[-1]
            full = "\\" + "\\".join(segs + [last]) if last else _norm_uid(parts[2])
            nl.components[parts[1]] = (full, parts[3] if len(parts) > 3 else "")
            params = {}
            for item in (parts[6] if len(parts) > 6 else "").split("\x02"):
                if "\x01" in item:
                    k, v = item.split("\x01", 1)
                    params[k] = v
            nl.bom[parts[1]] = {"footprint": parts[3] if len(parts) > 3 else "",
                                "comment": parts[4] if len(parts) > 4 else "",
                                "kind": parts[5] if len(parts) > 5 else "", "params": params}
        elif parts[0] == "V" and len(parts) >= 4:
            nl.variants.setdefault(parts[1], {})[parts[2]] = parts[3]
        elif parts[0] == "P" and len(parts) >= 4:
            nl.pcb_components[parts[1]] = (_norm_uid(parts[2]), parts[3], parts[4] if len(parts) > 4 else "")
        elif parts[0] == "Q" and len(parts) >= 3:
            nl.pcb_pad_net[(parts[1], parts[2])] = parts[3] if len(parts) > 3 else ""
    return nl


@dataclass
class Comparison:
    net_map: dict = field(default_factory=dict)      # KiCad net -> Altium net name
    mismatched_pins: list = field(default_factory=list)  # (des, pin, kicad net, altium net)
    pcb_only_components: list = field(default_factory=list)
    sch_only_components: list = field(default_factory=list)
    altium_pin_net: dict = field(default_factory=dict)
    kicad_nets: dict = field(default_factory=dict)
    nets_total: int = 0
    nets_ok: int = 0
    pins_total: int = 0
    pins_ok: int = 0
    shorts: dict = field(default_factory=dict)       # Altium net -> sorted KiCad nets it joins (must be empty)
    renamed: dict = field(default_factory=dict)      # named KiCad net -> different Altium name
    named_total: int = 0

    def summary(self) -> str:
        return (f"nets matched {self.nets_ok}/{self.nets_total}, pins matched {self.pins_ok}/{self.pins_total}, "
                f"pin mismatches {len(self.mismatched_pins)}, shorted nets {len(self.shorts)}, "
                f"named nets kept {self.named_total - len(self.renamed)}/{self.named_total}, "
                f"PCB-only parts {len(self.pcb_only_components)}, "
                f"schematic-only parts {len(self.sch_only_components)}")


def compare(board, nl: AltiumNetlist, ref_of: dict | None = None, sheet_suffix: bool = False) -> Comparison:
    """KiCad PCB pad connectivity vs Altium compiled schematic netlist.

    ref_of: KiCad footprint path -> schematic designator (for boards not updated from the schematic).
    sheet_suffix: the project appends sheet numbers to local nets ('SDA' -> 'SDA_3' keeps the name).
    """
    cmp = Comparison()
    kicad_nets: dict[str, set] = defaultdict(set)
    pcb_refs = set()
    sch_refs = set(nl.components)
    for fp in board.footprints:
        ref = (ref_of or {}).get(fp.path) or fp.ref
        if "board_only" not in fp.attrs:
            pcb_refs.add(ref)
        if ref not in sch_refs:
            # PCB-only footprint (no symbol, or renamed on the board): its pads cannot be on the
            # schematic, so they are not connectivity errors.  Counting them split nets and made the
            # repair loop add useless glue power ports named after KiCad sheet paths (ERC 'multiple
            # names').  They are still reported in pcb_only_components.
            continue
        for pd in fp.pads:
            if pd.net and pd.number:
                kicad_nets[pd.net].add((ref, pd.number))
    for kn, pins in kicad_nets.items():
        names = Counter(nl.pin_net.get(p) for p in pins)
        best, cnt = names.most_common(1)[0]
        if kn.startswith("unconnected-("):
            # KiCad's per-pin placeholder nets: use Altium's single-pin net name when it has one
            if best is not None:
                cmp.net_map[kn] = best
            continue
        cmp.nets_total += 1
        cmp.pins_total += len(pins)
        if best is not None:
            cmp.net_map[kn] = best
            cmp.pins_ok += cnt
            if cnt == len(pins):
                cmp.nets_ok += 1
        for p in pins:
            an = nl.pin_net.get(p)
            if an != best:
                cmp.mismatched_pins.append((p[0], p[1], kn, an))
    # Two KiCad nets in one Altium net = a short.  The per-net check above cannot see it (every pin
    # of each KiCad net is in "its" Altium net), so it is checked separately: one test board reported
    # 65/65 while USB_D_N was joined to VBUS.
    joined: dict[str, set] = defaultdict(set)
    for kn, pins in kicad_nets.items():
        for p in pins:
            an = nl.pin_net.get(p)
            if an is not None:
                joined[an].add(kn)
    def one_pin(kns):
        # KiCad gives each pad of an unconnected pin its own 'unconnected-' net; a pin drawn as two pads with
        # one number (a VQFN-HR pad 10, an L-shaped corner pad) is one pin, one net in Altium
        pins = set().union(*(kicad_nets[k] for k in kns))
        return all(k.startswith("unconnected-(") for k in kns) and len(pins) == 1
    cmp.shorts = {an: sorted(kns) for an, kns in joined.items() if len(kns) > 1 and not one_pin(kns)}
    shorted = {kn for kns in cmp.shorts.values() for kn in kns if not kn.startswith("unconnected-(")}
    for kn in shorted:
        pins = kicad_nets[kn]
        if Counter(nl.pin_net.get(p) for p in pins).most_common(1)[0][1] == len(pins):
            cmp.nets_ok -= 1
    # a net the designer named in KiCad keeps that name in Altium (a local label's sheet path
    # '/05_USB_UART/USB_3V3' becomes 'USB_3V3'); KiCad's automatic names may change freely
    for kn, an in cmp.net_map.items():
        if kn.startswith(("Net-(", "unconnected-(")):
            continue
        cmp.named_total += 1
        want = kn.rstrip("/").split("/")[-1]
        if "~{" in want:
            # KiCad overbar '~{RST}' is Altium's 'R\S\T\': the same name
            from .convert.sch_convert import overbar
            want = overbar(want)
        if an != want and not (sheet_suffix and an.startswith(want + "_") and an[len(want) + 1:].isdigit()):
            cmp.renamed[kn] = an
    cmp.altium_pin_net = dict(nl.pin_net)
    cmp.kicad_nets = dict(kicad_nets)
    cmp.pcb_only_components = sorted(pcb_refs - sch_refs)
    cmp.sch_only_components = sorted(r for r in sch_refs - pcb_refs if not r.startswith("#")
                                     and r not in getattr(board, "_not_on_board", ()))
    return cmp


@dataclass
class EcoPreview:
    """What Altium's 'Update PCB' would change (schematic -> PCB)."""
    add_components: list = field(default_factory=list)
    remove_components: list = field(default_factory=list)
    relink_components: list = field(default_factory=list)
    footprint_changes: list = field(default_factory=list)
    pad_net_changes: list = field(default_factory=list)

    @property
    def total(self) -> int:
        return (len(self.add_components) + len(self.remove_components) + len(self.relink_components)
                + len(self.footprint_changes) + len(self.pad_net_changes))

    def summary(self) -> str:
        return (f"ECO preview: add {len(self.add_components)}, remove {len(self.remove_components)}, "
                f"re-link {len(self.relink_components)}, footprint {len(self.footprint_changes)}, "
                f"pad-net {len(self.pad_net_changes)}")


def eco_preview(nl: AltiumNetlist, skip_refs=()) -> EcoPreview:
    """skip_refs: schematic parts that are not synchronised with the PCB (KiCad 'not on board')."""
    e = EcoPreview()
    pcb = nl.pcb_components
    sch = nl.components
    for des, (uid, fpn) in sch.items():
        if des in skip_refs:
            continue
        if des not in pcb:
            e.add_components.append(des)
            continue
        puid, pattern, kind = pcb[des]
        if uid and puid and uid != puid:
            e.relink_components.append((des, uid, puid))
        if fpn and pattern and fpn != pattern:
            e.footprint_changes.append((des, fpn, pattern))
    for des, (puid, pattern, kind) in pcb.items():
        # Altium kinds: 1 mechanical, 2 graphical are not synchronised
        if des not in sch and kind not in ("1", "2"):
            e.remove_components.append(des)
    for (des, pin), net in nl.pcb_pad_net.items():
        if des not in sch:
            continue
        want = nl.pin_net.get((des, pin), "")
        if want != net:
            e.pad_net_changes.append((des, pin, net, want))
    return e


# ---------------------------------------------------------------- BOM
BOM_KINDS = {"0", "1", "3"}      # Altium component kinds in the BOM: standard, mechanical, net tie (BOM)
NOT_FITTED = "1"


@dataclass
class BomComparison:
    kicad_count: int = 0
    altium_count: int = 0
    matched: int = 0
    only_kicad: list = field(default_factory=list)
    only_altium: list = field(default_factory=list)
    differences: list = field(default_factory=list)  # (des, what, kicad, altium)
    excluded: dict = field(default_factory=dict)     # des -> why it is out of both BOMs
    variant: str = ""

    @property
    def ok(self) -> bool:
        return not (self.only_kicad or self.only_altium or self.differences)

    def summary(self) -> str:
        return (f"BOM: {self.matched}/{self.kicad_count} KiCad BOM parts identical in Altium"
                + (f" (variant '{self.variant}')" if self.variant else "")
                + f", only in KiCad {len(self.only_kicad)}, only in Altium {len(self.only_altium)}, "
                  f"field differences {len(self.differences)}, out of the BOM {len(self.excluded)}")


def compare_bom(kicad_rows: dict, nl: AltiumNetlist, variant: str = "DNP") -> BomComparison:
    """KiCad BOM (from the schematic, as Convi read it) vs the BOM Altium builds from the compiled
    project: same parts, values, footprints and parameters.

    kicad_rows: des -> {value, footprint, in_bom, dnp, marker, params}."""
    b = BomComparison(variant=variant if variant in nl.variants else "")
    nf = {d for d, k in nl.variants.get(variant, {}).items() if k == NOT_FITTED}
    kic = {}
    for des, r in kicad_rows.items():
        if r.get("marker"):
            b.excluded[des] = r["marker"]
        elif r.get("dnp"):
            b.excluded[des] = "DNP"
        elif not r.get("in_bom", True):
            b.excluded[des] = "not in BOM"
        else:
            kic[des] = r
    alt = {d: v for d, v in nl.bom.items() if v.get("kind") in BOM_KINDS and d not in nf}
    b.kicad_count, b.altium_count = len(kic), len(alt)
    b.only_kicad = sorted(set(kic) - set(alt))
    b.only_altium = sorted(set(alt) - set(kic))
    for des in sorted(set(kic) & set(alt)):
        k, a = kic[des], alt[des]
        diffs = []
        if (k.get("value") or "") != (a.get("comment") or ""):
            diffs.append(("value/comment", k.get("value"), a.get("comment")))
        if k.get("footprint") and k["footprint"] != a.get("footprint"):
            diffs.append(("footprint", k["footprint"], a.get("footprint")))
        # Altium parameter names ignore case and its values lose surrounding blanks (U13 MPN
        # 'AT25DF641A-SH-T '): neither is a difference
        ap = {str(n).lower(): v for n, v in (a.get("params") or {}).items()}
        for pk, pv in (k.get("params") or {}).items():
            av = ap.get(pk.lower(), "") or ""
            if pv and av.strip() != pv.strip():
                diffs.append((pk, pv, av))
        for d in diffs:
            b.differences.append((des,) + d)
        if not diffs:
            b.matched += 1
    # markers / DNP must really be out of the Altium BOM
    for des in b.excluded:
        if des in alt:
            b.only_altium.append(des)
    b.only_altium = sorted(set(b.only_altium))
    return b
