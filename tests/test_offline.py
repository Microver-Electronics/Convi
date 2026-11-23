"""Offline tests (no Altium needed).  Run: py -m pytest -q tests"""
import math
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEMOS = ROOT / "testdata/kicad-src/demos"

from convi import sexpr  # noqa: E402
from convi.geom import arc_from_3pts_yup, rot  # noqa: E402
from convi.kicad.common import unescape_name  # noqa: E402

needs_demos = pytest.mark.skipif(not DEMOS.is_dir(), reason="KiCad demos not downloaded")


def test_sexpr_basic():
    n = sexpr.parse('(kicad_pcb (version 20260206) (net "a\\"b") (data |AB\n CD|))')
    assert n[0] == "kicad_pcb"
    assert sexpr.val(n, "version") == "20260206"
    assert sexpr.find(n, "net")[1] == 'a"b'
    assert sexpr.find(n, "data")[1] == "ABCD"


def test_rotation_matches_kicad():
    # KiCad RotatePoint: 90 deg moves +X to -Y (screen up, Y down)
    x, y = rot(1, 0, 90)
    assert abs(x) < 1e-9 and abs(y + 1) < 1e-9


def test_arc_ccw():
    c, r, a1, a2 = arc_from_3pts_yup((1, 0), (0, 1), (-1, 0))
    assert abs(r - 1) < 1e-9 and abs(a1 - 0) < 1e-6 and abs(a2 - 180) < 1e-6
    c, r, a1, a2 = arc_from_3pts_yup((-1, 0), (0, 1), (1, 0))
    assert abs(a1 - 0) < 1e-6 and abs(a2 - 180) < 1e-6


def test_unescape():
    assert unescape_name("/VPP{slash}MCLR") == "/VPP/MCLR"


@needs_demos
def test_all_demo_boards_parse():
    from convi.kicad.pcb import parse_board
    for f in DEMOS.rglob("*.kicad_pcb"):
        if f.stat().st_size > 20_000_000:
            continue
        b = parse_board(f)
        assert b.version > 0


@needs_demos
def test_symbol_pins_land_on_wires():
    """Symbol transform check: >97% of pins must touch a wire end / label / no-connect / another pin."""
    from convi.kicad.sch import parse_schematic

    def k(p):
        return round(p[0], 2), round(p[1], 2)
    tot = hit = 0
    for f in list(DEMOS.rglob("*.kicad_sch"))[:60]:
        s = parse_schematic(f)
        pts = {k(p) for w in s.wires for p in w.pts} | {k(p) for p in s.no_connects} | {k(l.pos) for l in s.labels}
        pins = []
        for sym in s.symbols:
            ls = s.lib_symbols.get(sym.lib_name or sym.lib_id)
            if not ls:
                continue
            for pin in ls.pins:
                if pin.unit in (0, sym.unit) and pin.style in (0, sym.body_style):
                    pins.append(k(sym.to_sheet(pin.pos)))
        c = Counter(pins)
        for p in pins:
            tot += 1
            hit += (p in pts) or c[p] > 1
    assert tot > 1000 and hit / tot > 0.97


@needs_demos
def test_pcb_ir_generation():
    from convi.altium.ir import IRWriter
    from convi.convert.pcb_convert import PcbConverter
    from convi.kicad.common import Issues
    from convi.kicad.pcb import parse_board
    from convi.models3d import ModelResolver
    f = DEMOS / "ecc83/ecc83-pp.kicad_pcb"
    b = parse_board(f)
    conv = PcbConverter(b, ModelResolver(f.parent, download=False), Issues())
    ir = IRWriter()
    conv.build_pcb(ir, Path("x.PcbDoc"))
    cmds = Counter(l.split("\t")[0] for l in ir.lines)
    assert cmds["COMP"] == len(b.footprints)
    assert cmds["PAD"] == sum(len(fp.pads) for fp in b.footprints)
    assert cmds["OUTLINE"] == 1


@needs_demos
def test_pcbcheck_compare_logic():
    """Synthetic 'Altium' rows built from the KiCad board must compare clean; a moved pad must be flagged."""
    from convi.altium.ir import mm
    from convi.convert.pcb_convert import PcbConverter
    from convi.kicad.common import Issues
    from convi.kicad.pcb import parse_board
    from convi.models3d import ModelResolver
    from convi.pcbcheck import _layer_int, check
    # the first routed demo board (over 100 tracks)
    b = f = None
    for f in sorted(DEMOS.rglob("*.kicad_pcb")):
        if f.stat().st_size > 20_000_000:
            continue
        b = parse_board(f)
        if len(b.tracks) > 100:
            break
    assert b is not None and len(b.tracks) > 100
    conv = PcbConverter(b, ModelResolver(f.parent, download=False), Issues())
    rows = []
    for fp in b.footprints:
        for pd in fp.pads:
            rows.append(["P", fp.ref, pd.number, str(conv.X(pd.pos[0])), str(conv.Y(pd.pos[1])), "0", "1",
                         conv.altium_net_name(pd.net) or "", "0", "0", "0"])
    for t in b.tracks:
        rows.append(["T", str(conv.X(t.end[0])), str(conv.Y(t.end[1])), str(conv.X(t.start[0])),
                     str(conv.Y(t.start[1])), str(mm(t.width)), str(_layer_int(conv.lm.get(t.layer) or "")), t.net])
    for t in b.arcs:
        ar = conv._arc_alt(t.start, t.mid, t.end)
        if ar:
            rows.append(["A", str(ar[0][0]), str(ar[0][1]), str(ar[1]), "0", "0", "0",
                         str(_layer_int(conv.lm.get(t.layer) or "")), t.net])
    for v in b.vias:
        rows.append(["V", str(conv.X(v.pos[0])), str(conv.Y(v.pos[1])), str(mm(v.size)), "0", "1", "32", v.net])
    res = check(conv, rows)
    assert res.tracks_kicad > 100 and not res.tracks_missing and not res.arcs_missing and not res.vias_missing
    assert not res.pad_net_errors and res.pads_ok == res.pads_total
    rows[0][3] = str(int(rows[0][3]) + mm(0.1))
    assert len(check(conv, rows).pad_errors) >= 1


def test_workspace_layout(tmp_path, monkeypatch):
    """CONVI layout: Projects/<Name>/... -> Outputs/convi_out/<Name>, existing conversions are never reused."""
    from convi import layout
    monkeypatch.delenv("CONVI_WORKSPACE", raising=False)
    ws = tmp_path / "CONVI"
    for d in ("Tool", "Projects", "Outputs"):
        (ws / d).mkdir(parents=True)
    kicad = ws / "Projects" / "MyBoard" / "hardware" / "kicad" / "MyBoard_main"
    kicad.mkdir(parents=True)
    models = ws / "Projects" / "MyBoard" / "hardware" / "libraries" / "3dmodels"
    models.mkdir(parents=True)
    assert layout.find_workspace(kicad) == ws
    assert layout.project_name(kicad) == "MyBoard"
    out = ws / "Outputs" / "convi_out"
    assert layout.default_out_dir(kicad) == out / "MyBoard"
    (out / "MyBoard").mkdir(parents=True)
    assert layout.default_out_dir(kicad) == out / "MyBoard"          # empty folder may be reused
    (out / "MyBoard" / "x.PrjPcb").write_text("")
    (out / "MyBoard_v2").mkdir()
    (out / "MyBoard_v2" / "x.PrjPcb").write_text("")
    assert layout.default_out_dir(kicad) == out / "MyBoard_v3"
    assert layout.default_model_dirs(kicad) == [models]
    monkeypatch.setenv("CONVI_WORKSPACE", str(ws))
    assert layout.find_workspace(tmp_path) == ws
    monkeypatch.delenv("CONVI_WORKSPACE")
    other = tmp_path / "elsewhere" / "board"
    other.mkdir(parents=True)
    monkeypatch.setattr(layout, "TOOL_DIR", tmp_path / "no_ws_tool")
    assert layout.default_out_dir(other) == other.resolve() / "Altium"


def test_probe_tokens():
    """Development probes must not contain machine-specific absolute paths (use %ROOT%, %OUT%, %WS%)."""
    import re
    bad = [p.name for p in (ROOT / "devtools" / "probes").glob("*.pas")
           if p.name != "_show.pas" and re.search(r"'[A-Za-z]:\\", p.read_text(encoding="utf-8", errors="replace"))]
    assert not bad, bad


def test_erc_matrix_follows_kicad_pin_map():
    from convi.convert.project_file import ALTIUM_ERC_DEFAULT, KICAD_PIN_MAP_DEFAULT, erc_matrix
    m = erc_matrix(KICAD_PIN_MAP_DEFAULT)
    assert len(m) == 17 and all(len(r) == 17 for r in m)
    assert m[2][2] == "E"                      # output - output: KiCad error
    assert m[1][2] == m[2][1] == "N"           # IO - output: KiCad OK (Altium default W)
    assert m[1][8] == m[0][12] == "N"          # pin - port / sheet entry: KiCad does not compare
    assert m[16] == ALTIUM_ERC_DEFAULT[16]     # Unconnected row: Altium default
    assert m[8][8] == ALTIUM_ERC_DEFAULT[8][8] and m[8][12] == ALTIUM_ERC_DEFAULT[8][12]   # port / entry
    assert erc_matrix(None) == m and erc_matrix([[0]]) == m


def test_local_net_name_clash_turns_on_sheet_numbers():
    from convi.convert.sch_convert import SchConverter
    from convi.kicad.common import Issues
    sc = SchConverter.__new__(SchConverter)
    sc.issues = Issues()
    clash = sc.plan_local_net_names(["/Left Board/SDA", "/Right Board/SDA", "GND", "Net-(R1-Pad2)", "/A/X"])
    assert sc.append_sheet_numbers and list(clash) == ["SDA"]
    sc.plan_local_net_names(["/A/SDA", "/B/SCL", "GND"])
    assert not sc.append_sheet_numbers


def test_table_grid_skips_merged_cells():
    from convi import sexpr
    from convi.kicad.pcb import parse_table
    cell = lambda t, x1, y1, x2, y2, cs=1: (f'(table_cell "{t}" (start {x1} {y1}) (end {x2} {y2}) '
                                            f'(margins 1 1 1 1) (span {cs} 1) (layer "Cmts.User") '
                                            '(effects (font (size 1 1)) (justify left)))')
    src = ('(table (column_count 2) (layer "Cmts.User") (border (external yes) (header yes) (stroke (width 0.2))) '
           '(separators (rows yes) (cols yes) (stroke (width 0.1))) (column_widths 10 10) (row_heights 5 5) (cells '
           + cell("HEAD", 0, 0, 20, 5, 2) + cell("", 10, 0, 20, 5, 0) + cell("a", 0, 5, 10, 10)
           + cell("b", 10, 5, 20, 10) + '))')
    cells, lines = parse_table(sexpr.parse(src))
    assert [c.text for c in cells] == ["HEAD", "", "a", "b"] and cells[0].margins == (1, 1, 1, 1)
    verticals = [l for l in lines if l[0][0] == l[1][0] and l[0][0] == 10]
    assert len(verticals) == 1 and verticals[0][0][1] == 5      # no column line through the merged header
    assert len(lines) == 4 + 2 + 1                              # border, header line (2 cols), 1 column line


def test_pin_to_pin_ignore_clears_pin_conflicts():
    from convi.convert.project_file import erc_matrix, kicad_pin_map
    m = erc_matrix(kicad_pin_map({"erc": {"rule_severities": {"pin_to_pin": "ignore"}}}))
    assert all(m[i][j] == "N" for i in range(8) for j in range(8))
    assert kicad_pin_map({"erc": {"pin_map": [[1]]}}) == [[1]]


def test_off_grid_symbol_moves_along_its_wire():
    from convi.convert.sch_convert import SchConverter
    from convi.kicad.common import Issues
    from convi.kicad.sch import LibPin, LibSymbol, Schematic, SymbolInstance, Wire
    tp = LibSymbol(lib_id="C:TP", pins=[LibPin(1, 1, "passive", "line", (0, 0), 90, 0, "1", "1")])
    r = LibSymbol(lib_id="D:R", pins=[LibPin(1, 1, "passive", "line", (0, 0), 90, 0, "1", "1")])
    sch = Schematic(path=Path("x.kicad_sch"), lib_symbols={"C:TP": tp, "D:R": r})
    sym = SymbolInstance("C:TP", "", (100.3633, 72.39), 0, "", 1, 1, "u1")
    sch.symbols = [sym, SymbolInstance("D:R", "", (40.0, 72.39), 0, "", 1, 1, "u2")]   # off grid, no wire
    w1 = Wire("wire", [(96.52, 72.39), (100.3633, 72.39)], None)
    w2 = Wire("wire", [(102.87, 72.39), (100.3633, 72.39)], None)
    w3 = Wire("wire", [(100.3633, 72.39), (100.3633, 80.01)], None)      # T across: would leave it
    sch.wires = [w1, w2]
    sch.junctions = [((100.3633, 72.39), 0.0)]
    sc = SchConverter.__new__(SchConverter)
    sc.issues = Issues()
    done = sc._snap_off_grid(sch)
    assert [d[0] for d in done] == ["C:TP", "D:R"]
    assert sym.pos == (100.33, 72.39) and w1.pts[1] == w2.pts[1] == (100.33, 72.39)
    assert sch.junctions[0][0] == (100.33, 72.39)
    sym.pos = (100.3633, 72.39)
    w1.pts[1] = w2.pts[1] = (100.3633, 72.39)
    sch.wires = [w1, w2, w3]
    sch.symbols = sch.symbols[:1]
    assert sc._snap_off_grid(sch) == [] and sym.pos == (100.3633, 72.39)


def test_undefined_path_variable_falls_back_to_kicad_library_by_name(tmp_path):
    from convi.models3d import ModelResolver, std_lib_for_name
    lib = tmp_path / "3dmodels" / "Resistor_SMD.3dshapes"
    lib.mkdir(parents=True)
    (lib / "R_0402_1005Metric.wrl").write_text("x")
    (lib / "R_0402_1005Metric.step").write_text("ISO-10303-21;")
    (tmp_path / "prj").mkdir()
    r = ModelResolver(tmp_path / "prj", extra_vars={"KICAD10_3DMODEL_DIR": str(tmp_path / "3dmodels")},
                      download=False)
    # off by default: the designer's model offset / rotation do not fit KiCad's file
    assert r.resolve("${VENDOR3DMDL}/R_0402_1005Metric.step").status == "unresolved_var"
    r = ModelResolver(tmp_path / "prj", extra_vars={"KICAD10_3DMODEL_DIR": str(tmp_path / "3dmodels")},
                      download=False)
    r.by_name = True
    ref = r.resolve("${VENDOR3DMDL}/R_0402_1005Metric.step")
    assert ref.status == "by_name" and ref.step.name == "R_0402_1005Metric.step" and "VENDOR3DMDL" in ref.note
    assert r.resolve("${VENDOR3DMDL}/PLUG_GENERIC_X.step").status == "unresolved_var"
    assert std_lib_for_name("C_0603_1608Metric") == "Capacitor_SMD.3dshapes"
    assert std_lib_for_name("C_0603_1608Metric_EIA") is None and std_lib_for_name("sot23-3") is None


def test_schematic_table_cells_and_markup():
    from convi import sexpr as S
    from convi.convert.sch_convert import plain_markup
    from convi.kicad.pcb import parse_table
    node = S.parse('(table (column_count 1) (border (external yes) (header yes) (stroke (width 0)))'
                   ' (separators (rows yes) (cols yes) (stroke (width 0))) (column_widths 22.86)'
                   ' (row_heights 3.81 6.35) (cells (table_cell "Requirements" (at 161.29 58.42 0)'
                   ' (size 22.86 3.81) (span 1 1) (effects (font (size 1.27 1.27))))'
                   ' (table_cell "Max ripple: 20mV_{p-p}" (at 161.29 62.23 0) (size 22.86 6.35) (span 1 1)'
                   ' (effects (font (size 1.27 1.27))))))')
    cells, lines = parse_table(node[0] if isinstance(node[0], list) else node)
    assert len(cells) == 2 and cells[1].pos == (161.29, 62.23) and len(lines) == 5
    assert plain_markup(cells[1].text) == "Max ripple: 20mV_p-p" and plain_markup("V_{ACT}_RX") == "V_ACT_RX"


def test_group_bus_becomes_signal_harness():
    from convi.convert.sch_convert import SchConverter, split_group_bus
    from convi.kicad.common import Issues
    from convi.kicad.sch import Label, Schematic, Wire
    assert split_group_bus("PCIe_{x4}{PCIe}") == ("PCIe_{x4}", "PCIe")
    assert split_group_bus("V_{FAN}") is None and split_group_bus("~{CE}") is None
    assert split_group_bus("ETH{D+ D-}") == ("ETH", "D+ D-")
    sch = Schematic(path=Path("sub.kicad_sch"))
    sch.bus_aliases = {"G": ["A", "B"]}
    # vertical bus run x=50.8 (y 50.8..55.88) with two entries to the right, then to a label at x=40.64
    sch.buses = [Wire("bus", [(50.8, 50.8), (50.8, 55.88)], None),
                 Wire("bus", [(50.8, 55.88), (40.64, 55.88)], None)]
    sch.bus_entries = [((50.8, 50.8), (2.54, 2.54)), ((50.8, 53.34), (2.54, 2.54))]
    sch.wires = [Wire("wire", [(53.34, 53.34), (60.96, 53.34)], None),
                 Wire("wire", [(53.34, 55.88), (60.96, 55.88)], None)]
    sch.labels = [Label("label", "ETH.A", (55.88, 53.34), 0, None), Label("label", "ETH.B", (55.88, 55.88), 0, None),
                  Label("hierarchical_label", "ETH{G}", (40.64, 55.88), 180, None, "bidirectional")]
    sc = SchConverter.__new__(SchConverter)
    sc.issues, sc.cache = Issues(), {sch.path: sch}
    hp = sc._harness_plan(sch)
    assert hp["skip_bus"] == {id(b) for b in sch.buses} and hp["skip_entry"] == {0, 1}
    (c,) = hp["conns"]
    assert c["xb"] == 50.8 and c["dx"] == 2.54 and c["tip"] == (50.8, 55.88) and c["type"] == "G"
    assert [n for _, n in c["members"]] == ["A", "B"]
    assert hp["harness"] == [((50.8, 55.88), (40.64, 55.88))]
    assert hp["ports"][id(sch.labels[2])] == ("ETH", "G")


def test_delphiscript_comments_and_strings_close():
    """A '}' inside a { } comment ends it early (c64: '{10GbE}' in a comment -> 'Unterminated string' in
    Altium, nothing built); a string must close on its own line."""
    bad = []
    for f in sorted((ROOT / "convi" / "altium").glob("*.pas")):
        s, i, line = f.read_text(), 0, 1
        while i < len(s):
            c = s[i]
            if c == "\n":
                line += 1
            elif c == "'":
                j, nl = s.find("'", i + 1), s.find("\n", i + 1)
                if j < 0 or 0 <= nl < j:
                    bad.append((f.name, line, "unterminated string"))
                    j = nl
                i = j + 1
                continue
            elif s.startswith("//", i):
                i = s.find("\n", i)
                continue
            elif c == "{":
                j = s.find("}", i + 1)
                if "{" in s[i + 1:j]:
                    bad.append((f.name, line, "'{' inside a comment"))
                line += s[i:j].count("\n")
                i = j + 1
                continue
            i += 1
    assert not bad, bad


def test_footprint_line_runs_are_joined():
    import random
    from convi.convert.pcb_convert import merge_fp_lines
    from convi.kicad.pcb import Shape
    pts = [(math.cos(t / 400 * math.pi), math.sin(t / 400 * math.pi)) for t in range(401)]   # half circle
    lines = [Shape("line", "User.9", 0.02, local_pts=[pts[i], pts[i + 1]]) for i in range(400)]
    lines += [Shape("line", "User.9", 0.02, local_pts=[pts[5], pts[5]])]                    # a dot on the arc
    rect = [(0, 0), (2, 0), (2, 1), (0, 1), (0, 0)]
    lines += [Shape("line", "User.9", 0.02, local_pts=[rect[i], rect[i + 1]]) for i in range(4)]
    random.Random(1).shuffle(lines)
    cu = [Shape("line", "F.Cu", 0.2, local_pts=[(0, 0), (0.001, 0)]) for _ in range(10)]
    out = merge_fp_lines(lines + cu, lambda L: L.endswith(".Cu"))
    assert sum(1 for s in out if s.layer == "F.Cu") == 10                  # copper untouched
    g = [s for s in out if s.layer == "User.9"]
    assert 4 < len(g) < 60                                                 # 405 lines -> arc chords + 4 sides
    for a, b in ((s.local_pts[0], s.local_pts[1]) for s in g):
        if abs(math.hypot(*a) - 1) < 1e-6 and abs(math.hypot(*b) - 1) < 1e-6:
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            assert 1 - math.hypot(*mid) <= 0.002 + 1e-9                     # chord within tol of the arc
    corners = {tuple(round(c, 6) for c in p) for s in g for p in s.local_pts}
    assert {(0, 0), (2, 0), (2, 1), (0, 1)} <= corners


def test_designer_model_names_find_the_kicad_package():
    from convi.models3d import nearest_std_names
    lib = [x.lower() for x in ["SOT-23-6", "SOT-23", "D_SOD-523", "USON-10_2.5x1.0mm_P0.5mm", "C_0603_1608Metric",
                               "QFN-16-1EP_4x4mm_P0.65mm_EP2.1x2.1mm", "QFN-16-1EP_4x4mm_P0.65mm_EP2.7x2.7mm_ThermalVias",
                               "L_Coilcraft_XAL5030"]]
    assert nearest_std_names("sot23-6", lib) == ["sot-23-6"]
    assert nearest_std_names("SOD523F", lib) == ["d_sod-523"]
    assert nearest_std_names("USON-10_2.5x1mm_P0.5mm", lib) == ["uson-10_2.5x1.0mm_p0.5mm"]
    assert nearest_std_names("C_0603_1608Metric_EIA", lib) == ["c_0603_1608metric"]
    assert nearest_std_names("QFN-16-1EP_4x4mm_P0.65mm", lib) == ["qfn-16-1ep_4x4mm_p0.65mm_ep2.1x2.1mm"]
    assert nearest_std_names("L_Coilcraft-XAL5030", lib) == ["l_coilcraft_xal5030"]
    assert nearest_std_names("VQFN-24", lib) == [] and nearest_std_names("MODULE_VENDOR_SOM-NX", lib) == []


def test_kicad_drc_exclusions_name_the_part_pairs():
    from types import SimpleNamespace as NS
    from convi.pipeline import _kicad_excluded_pairs
    fps = [NS(ref="J11", uuid="f1", path="", pads=[NS(uuid="p1", number="")]),
           NS(ref="J7", uuid="f2", path="", pads=[NS(uuid="p2", number="MP1")]),
           NS(ref="R1", uuid="f3", path="", pads=[NS(uuid="p3", number="1")])]
    data = {"board": {"design_settings": {"drc_exclusions": [
        ["hole_clearance|145249000|81350000|p2|p1", ""], "courtyards_overlap|1|2|f1|f3"]}}}
    pcbc = NS(project=NS(data=data), b=NS(footprints=fps), symbol_of={})
    assert _kicad_excluded_pairs(pcbc) == {frozenset(("J11", "J7"))}


def test_units_from_different_symbol_copies_make_one_part():
    # each unit of one reference used its own embedded copy (lib_name X_1, X_2, X)
    from convi.convert.sch_convert import SchConverter, SheetInstance
    from convi.kicad.common import Issues
    from convi.kicad.sch import LibPin, LibShape, LibSymbol, Schematic, SymbolInstance

    def copy_(lid, mark):
        return LibSymbol(lib_id=lid, unit_count=2,
                         pins=[LibPin(1, 1, "passive", "line", (0, 0), 0, 2.54, "A", "1"),
                               LibPin(2, 1, "passive", "line", (0, 0), 0, 2.54, "B", "2")],
                         shapes=[LibShape("rectangle", 1, 1, [(0, 0), (mark, 1)]),
                                 LibShape("rectangle", 2, 1, [(0, 0), (mark, 2)])])
    a, b = copy_("X_1", 5), copy_("X_2", 7)
    sch = Schematic(path=Path("x.kicad_sch"), lib_symbols={"X_1": a, "X_2": b})
    s1 = SymbolInstance("L:X", "X_1", (0, 0), 0, "", 1, 1, "u1", instances=[("/r", "U6", 1)])
    s2 = SymbolInstance("L:X", "X_2", (20, 0), 0, "", 2, 1, "u2", instances=[("/r", "U6", 2)])
    sch.symbols = [s1, s2]
    sc = SchConverter.__new__(SchConverter)
    sc.issues = Issues()
    sc.root = SheetInstance(path=(), sch=sch, name="x")
    sc.instances = [sc.root]
    sc._merge_split_units()
    m = sc._lib_for(sch, s1)
    assert m is sc._lib_for(sch, s2) and m.name == "X"
    assert [sh.pts[1][0] for sh in m.shapes] == [5, 7]          # unit 1 from X_1, unit 2 from X_2
    assert any("U6" in i.message for i in sc.issues.items)


def test_local_label_named_like_a_global_label_becomes_a_port():
    from convi.convert.sch_convert import SchConverter
    from convi.kicad.common import Issues, TextEffects
    from convi.kicad.sch import Label, Schematic, Wire
    sch = Schematic(path=Path("usb.kicad_sch"))
    te = TextEffects()
    sch.labels = [Label("label", "~{PDC_CS}", (0, 0), 0, te), Label("global_label", "~{PDC_CS}", (50, 0), 0, te,
                                                                     shape="output"),
                  Label("label", "ONLY_LOCAL", (0, 10), 0, te)]
    sc = SchConverter.__new__(SchConverter)
    sc.issues = Issues()
    sc.global_labels = "port"
    sc._harness_plan = lambda s: {"ports": {}}
    assert sc._port_twins(sch) == {id(sch.labels[0]): "output"}
    # on the global label's own wire it is the same net already: stays a net label
    sch.wires = [Wire("wire", [(0, 0), (50, 0)], None)]
    sc._twins = {}
    assert sc._port_twins(sch) == {}


def test_pinless_symbol_goes_onto_the_grid():
    from convi.convert.sch_convert import SchConverter
    from convi.kicad.common import Issues
    from convi.kicad.sch import LibSymbol, Schematic, SymbolInstance
    mech = LibSymbol(lib_id="M:Mech")
    sch = Schematic(path=Path("x.kicad_sch"), lib_symbols={"M:Mech": mech})
    sym = SymbolInstance("M:Mech", "", (283.845, 264.795), 180, "", 1, 1, "u1")   # a pinless mechanical part
    sch.symbols = [sym]
    sc = SchConverter.__new__(SchConverter)
    sc.issues = Issues()
    assert len(sc._snap_off_grid(sch)) == 1
    assert all(abs(v / 1.27 - round(v / 1.27)) < 1e-6 for v in sym.pos)


def test_footprint_graphics_drawn_twice_are_placed_once():
    from convi.convert.pcb_convert import PcbConverter
    from convi.kicad.pcb import Shape

    class FP:
        shapes = [Shape("line", "User.9", 0.02, local_pts=[(0, 0), (1, 0)]),
                  Shape("line", "User.9", 0.02, local_pts=[(1, 0), (0, 0)]),        # same line, reversed
                  Shape("line", "F.Cu", 0.2, local_pts=[(0, 0), (1, 0)]),
                  Shape("line", "F.Cu", 0.2, local_pts=[(0, 0), (1, 0)])]           # copper kept as drawn
    c = PcbConverter.__new__(PcbConverter)
    out = c._fp_shapes(FP())
    assert sum(1 for s in out if s.layer == "User.9") == 1 and sum(1 for s in out if s.layer == "F.Cu") == 2
    assert c.dup_graphics == 1


def test_negated_mixed_condition_splits_into_rules():
    # a rule 'GND 0.125 mm except zone to zone'
    from convi.convert import dru
    n = dru.parse_condition("A.NetName == 'GND' && !(A.Type == 'Zone' && B.Type == 'Zone')")
    pairs = dru.split_dnf(n)
    assert len(pairs) == 2
    assert ("pred", "A", "Type", "!=", "Zone") in pairs[0][0][1]
    assert pairs[1][1] == ("pred", "B", "Type", "!=", "Zone")


_MINI_PCB = """(kicad_pcb (version 20221018) (generator pcbnew)
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (38 "B.Mask" user) (39 "F.Mask" user) (44 "Edge.Cuts" user))
  (net 0 "") (net 1 "GND") (net 2 "VCC")
  (footprint "Conn:Peg" (layer "F.Cu") (at 50 50)
    (property "Reference" "J1")
    (pad "" np_thru_hole circle (at 0 0) (size 0.65 0.65) (drill 0.65) (layers "*.Cu" "*.Mask"))
    (pad "1" smd rect (at 0 1) (size 0.6 1) (layers "F.Cu" "F.Mask") (net 1 "GND"))
    (pad "2" smd rect (at 0.75 1) (size 0.6 1) (layers "F.Cu" "F.Mask") (net 2 "VCC")))
  (gr_rect (start 40 40) (end 60 60) (layer "Edge.Cuts") (width 0.1)))
"""


def test_custom_clearance_below_board_minimum_is_kept(tmp_path):
    # KiCad 7.0.11 DRC: a custom clearance rule below the board minimum is applied as is
    from convi.altium.ir import IRWriter
    from convi.convert.pcb_convert import PcbConverter
    from convi.convert.rules import lim
    from convi.kicad.common import Issues
    from convi.kicad.pcb import parse_board
    from convi.kicad.project import load_settings
    from convi.models3d import ModelResolver
    (tmp_path / "m.kicad_pcb").write_text(_MINI_PCB)
    (tmp_path / "m.kicad_pro").write_text('{"board": {"design_settings": {"rules": {"min_clearance": 0.2, '
                                          '"min_hole_clearance": 0.25}}}, "net_settings": {"classes": '
                                          '[{"name": "Default", "clearance": 0.2}]}}')
    (tmp_path / "m.kicad_dru").write_text('(version 1)\n(rule "fine"\n  (condition "A.NetName == \'GND\' && '
                                          'B.NetName == \'VCC\'")\n  (constraint clearance (min 0.14mm)))\n')
    iss = Issues()
    c = PcbConverter(parse_board(tmp_path / "m.kicad_pcb", iss), ModelResolver(tmp_path, download=False), iss,
                     project=load_settings(tmp_path / "m.kicad_pro"))
    c.dru_path = tmp_path / "m.kicad_dru"
    ir = IRWriter()
    c.build_pcb(ir, tmp_path / "x.PcbDoc")
    rule = next(l for l in ir.lines if "name=KiCad_fine\t" in l)
    assert f"gap={lim(0.14)}" in rule
    assert not [i for i in iss.items if i.level == "warning" and "fine" in i.message]
    # the NPTH pad is exactly its hole: its clearance violations are KiCad's own hole clearance violations
    assert c.hole_exact_pads == {"J1-"} and c.hole_clearance_mm == 0.25


def test_drc_line_pad_keys():
    from convi.pipeline import _drc_pad_keys
    d = ("Clearance Constraint: (7.615mil < 7.854mil) Between Pad J2-(1181.496mil,3735.039mil) on Multi-Layer "
         "And Pad J2-A12(1169.291mil,3692.756mil) on F.Cu")
    assert _drc_pad_keys(d) == {"J2-", "J2-A12"}


def test_lost_unrouted_connections_name_their_layers():
    from convi.pipeline import _lost_layers
    a = ("Un-Routed Net Constraint: Net GND Between Pad C13-2(-2555.905mil,-1090.157mil) on B.Cu And "
         "Pad C1-2(-2555.905mil,-1033.661mil) on B.Cu ")
    v = ("Un-Routed Net Constraint: Net GND Between Pad C38-2(3296.85mil,492.126mil) on B.Cu And "
         "Via (3326.772mil,496.063mil) from F.Cu to B.Cu")
    t = ("Un-Routed Net Constraint: Net GND Between Track (1.0mil,2.0mil)(3.0mil,2.0mil) on In1.Cu And "
         "Pad J1-1(5.0mil,2.0mil) on Multi-Layer")
    vv = "Un-Routed Net Constraint: Net GND Between Via (1.0mil,2.0mil) from F.Cu to B.Cu And Via (3.0mil,2.0mil) from F.Cu to B.Cu"
    assert _lost_layers([a], [a, v]) == {"B.Cu"}
    assert _lost_layers([], [t]) == {"In1.Cu"}
    # the same pair reported the other way round is not a new connection
    rev = ("Un-Routed Net Constraint: Net GND Between Pad C1-2(-2555.905mil,-1033.661mil) on B.Cu And "
           "Pad C13-2(-2555.905mil,-1090.157mil) on B.Cu")
    assert _lost_layers([a], [rev]) is None
    assert _lost_layers([a], [a, vv]) is None                  # two vias: any layer may make it


def test_templates_folder_is_found_by_pattern_or_environment(tmp_path, monkeypatch):
    from convi.convert.sheet_template import auto_customer, find_templates_dir, parse_name
    monkeypatch.delenv("CONVI_TEMPLATES", raising=False)
    prj = tmp_path / "ws" / "Projects" / "HW_ACME_WIDGET_R1"
    prj.mkdir(parents=True)
    assert find_templates_dir(prj) is None
    tpl = tmp_path / "__TeamTemplates"
    tpl.mkdir()
    (tpl / "A3 - template - Acme.SchDot").write_bytes(b"")
    assert find_templates_dir(prj) == tpl                      # any '__<Name>Templates' folder above
    assert parse_name(tpl / "A3 - template - Acme.SchDot") == ("A3", "Acme")
    assert auto_customer("HW_ACME_WIDGET_R1", tpl) == "Acme"
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setenv("CONVI_TEMPLATES", str(other))
    assert find_templates_dir(prj) == other                    # the environment variable wins


def test_symbols_are_built_as_one_piece():
    """Placed parts and library symbols get their primitives marked as part of the symbol
    (Altium writes ISNOTACCESIBLE=T); without it a click picks one line and drags it out."""
    s = (ROOT / "convi" / "altium" / "builder_sch.pas").read_text()
    body = lambda name: s[s.index("procedure " + name + ";"):].split("\nend;", 1)[0]
    for proc in ("DoCEnd", "DoLCompEnd"):
        assert "LockPrimitives(CurSComp)" in body(proc), proc
    assert "UpdatePrimitivesAccessibility" in body("LockPrimitives(C: Variant)")
