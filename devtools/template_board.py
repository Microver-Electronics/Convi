"""Dev: generate the synthetic KiCad project CONVI_TEMPLATE (4 layers, parts on both sides).

    python devtools/template_board.py <output folder>      (needs KiCad's Python module pcbnew, KiCad 7+)

The built-in manufacturing-output templates (convi/templates/outputs) are made on this board: convert it
with manufacturing outputs and placeholder bodies, then run devtools/make_builtin_templates.py on the
conversion. Altium refreshes the board data the Draftsman drawing caches, so the templates carry no data
of a real product. Everything on this board is synthetic.
"""
import json
import sys
import uuid
from pathlib import Path

import pcbnew as P

NAME = "CONVI_TEMPLATE"
OUT = Path(sys.argv[1]) / NAME
OUT.mkdir(parents=True, exist_ok=True)
NS = uuid.UUID("6b1f3c2e-0d4a-4c3b-9a51-1f2e3d4c5b6a")
U = lambda key: str(uuid.uuid5(NS, key))
ROOT = U("root")
mm = P.FromMM
V = lambda x, y: P.VECTOR2I(mm(x), mm(y))

# ------------------------------------------------------------------ parts
# ref, symbol, value, footprint, {pin: net}, (x, y), rot, side
PARTS = [
    ("U1", "IC8", "LOGIC-8", "SOIC-8_3.9x4.9mm_P1.27mm",
     {"1": "SIG1", "2": "SIG2", "3": "SIG3", "4": "GND", "5": "SIG4", "6": "SIG3", "7": "SIG2", "8": "+3V3"},
     (130, 118), 0, "top"),
    ("J1", "CONN4", "CONN_1x4", "PinHeader_1x04_P2.54mm_Vertical",
     {"1": "+3V3", "2": "SIG1", "3": "SIG2", "4": "GND"}, (106, 114), 0, "top"),
    ("R1", "R", "10k", "R_0603_1608Metric", {"1": "+3V3", "2": "SIG1"}, (118, 110), 0, "top"),
    ("R2", "R", "10k", "R_0603_1608Metric", {"1": "+3V3", "2": "SIG2"}, (118, 114), 0, "top"),
    ("R3", "R", "4k7", "R_0603_1608Metric", {"1": "GND", "2": "SIG3"}, (118, 124), 0, "top"),
    ("R4", "R", "4k7", "R_0603_1608Metric", {"1": "GND", "2": "SIG4"}, (118, 128), 0, "top"),
    ("C1", "C", "100n", "C_0603_1608Metric", {"1": "+3V3", "2": "GND"}, (140, 112), 90, "top"),
    ("C2", "C", "1u", "C_0805_2012Metric", {"1": "+3V3", "2": "GND"}, (144, 112), 90, "top"),
    ("TP1", "TP", "TP", "TestPoint_Pad_D1.5mm", {"1": "SIG3"}, (148, 126), 0, "top"),
    ("R5", "R", "1k", "R_0603_1608Metric", {"1": "SIG1", "2": "GND"}, (124, 132), 0, "bottom"),
    ("C3", "C", "10n", "C_0603_1608Metric", {"1": "SIG2", "2": "GND"}, (132, 132), 0, "bottom"),
    ("D1", "D", "DIODE", "D_SOD-123", {"1": "+3V3", "2": "SIG4"}, (140, 132), 0, "bottom"),
    ("H1", "MH", "MountingHole", "MountingHole_3.2mm_M3", {}, (104, 104), 0, "top"),
    ("H2", "MH", "MountingHole", "MountingHole_3.2mm_M3", {}, (156, 104), 0, "top"),
    ("H3", "MH", "MountingHole", "MountingHole_3.2mm_M3", {}, (104, 136), 0, "top"),
    ("H4", "MH", "MountingHole", "MountingHole_3.2mm_M3", {}, (156, 136), 0, "top"),
]
NETS = ["/GND", "/+3V3", "/SIG1", "/SIG2", "/SIG3", "/SIG4"]


def footprint_def(fpname):
    """pads [(num, (x, y), (w, h), shape, kind, drill)], body (w, h), courtyard margin, body height (mm)."""
    if fpname.startswith("SOIC-8"):
        pads = [(str(i + 1), (-2.7, -1.905 + 1.27 * i), (1.55, 0.6), "rr", "smd", 0) for i in range(4)]
        pads += [(str(8 - i), (2.7, -1.905 + 1.27 * i), (1.55, 0.6), "rr", "smd", 0) for i in range(4)]
        return pads, (3.9, 4.9), 0.25
    if fpname.startswith("PinHeader_1x04"):
        pads = [(str(i + 1), (0, 2.54 * i), (1.7, 1.7), "rect" if i == 0 else "oval", "pth", 1.0) for i in range(4)]
        return pads, (2.54, 10.16), 0.25
    if fpname.startswith("R_0603") or fpname.startswith("C_0603"):
        return [("1", (-0.8, 0), (0.95, 1.0), "rr", "smd", 0), ("2", (0.8, 0), (0.95, 1.0), "rr", "smd", 0)], \
            (1.6, 0.8), 0.25
    if fpname.startswith("C_0805"):
        return [("1", (-0.95, 0), (1.0, 1.45), "rr", "smd", 0), ("2", (0.95, 0), (1.0, 1.45), "rr", "smd", 0)], \
            (2.0, 1.25), 0.25
    if fpname.startswith("D_SOD-123"):
        return [("1", (-1.65, 0), (0.9, 1.2), "rr", "smd", 0), ("2", (1.65, 0), (0.9, 1.2), "rr", "smd", 0)], \
            (2.7, 1.6), 0.25
    if fpname.startswith("TestPoint"):
        return [("1", (0, 0), (1.5, 1.5), "circle", "smd", 0)], (1.5, 1.5), 0.25
    if fpname.startswith("MountingHole"):
        return [("", (0, 0), (3.2, 3.2), "circle", "npth", 3.2)], (3.2, 3.2), 0.3
    raise KeyError(fpname)


def add_shape(fp, layer, kind, a, b, width):
    s = P.FP_SHAPE(fp, P.SHAPE_T_RECT if kind == "rect" else (P.SHAPE_T_CIRCLE if kind == "circle" else P.SHAPE_T_SEGMENT))
    s.SetStart0(V(*a))
    s.SetEnd0(V(*b))
    s.SetLayer(layer)
    s.SetWidth(mm(width))
    s.SetDrawCoord()
    fp.Add(s)


def build_board():
    b = P.BOARD()
    b.SetFileName(str(OUT / f"{NAME}.kicad_pcb"))
    b.SetCopperLayerCount(4)
    tb = b.GetTitleBlock()
    tb.SetTitle(NAME)
    tb.SetRevision("1")
    nets = {}
    for n in NETS:
        ni = P.NETINFO_ITEM(b, n)
        b.Add(ni)
        nets[n] = ni
    # outline 60 x 40 mm
    e = P.PCB_SHAPE(b, P.SHAPE_T_RECT)
    e.SetStart(V(100, 100))
    e.SetEnd(V(160, 140))
    e.SetLayer(P.Edge_Cuts)
    e.SetWidth(mm(0.1))
    b.Add(e)
    pads_of = {}
    fps = {}
    for ref, sym, value, fpname, pinnet, (x, y), rot, side in PARTS:
        pads, (bw, bh), cm = footprint_def(fpname)
        fp = P.FOOTPRINT(b)
        fp.SetFPID(P.LIB_ID("Convi", fpname))
        fp.SetReference(ref)
        fp.SetValue(value)
        fp.SetPath(P.KIID_PATH("/" + U("sym" + ref)))
        attrs = 0
        if pads and pads[0][4] == "smd":
            attrs |= P.FP_SMD
        elif pads and pads[0][4] == "pth":
            attrs |= P.FP_THROUGH_HOLE
        if sym in ("MH",):
            attrs |= P.FP_EXCLUDE_FROM_BOM | P.FP_EXCLUDE_FROM_POS_FILES
        if sym == "TP":
            attrs |= P.FP_EXCLUDE_FROM_BOM
        fp.SetAttributes(attrs)
        b.Add(fp)
        for num, (px, py), (w, h), shape, kind, drill in pads:
            pd = P.PAD(fp)
            pd.SetNumber(num)
            pd.SetAttribute({"smd": P.PAD_ATTRIB_SMD, "pth": P.PAD_ATTRIB_PTH, "npth": P.PAD_ATTRIB_NPTH}[kind])
            pd.SetShape({"rr": P.PAD_SHAPE_ROUNDRECT, "rect": P.PAD_SHAPE_RECT, "oval": P.PAD_SHAPE_OVAL,
                         "circle": P.PAD_SHAPE_CIRCLE}[shape])
            if shape == "rr":
                pd.SetRoundRectRadiusRatio(0.25)
            pd.SetSize(V(w, h))
            pd.SetPos0(V(px, py))
            pd.SetPosition(V(px, py))
            if kind == "smd":
                pd.SetLayerSet(P.PAD.SMDMask())
            elif kind == "pth":
                pd.SetLayerSet(P.PAD.PTHMask())
                pd.SetDrillSize(V(drill, drill))
            else:
                pd.SetLayerSet(P.PAD.UnplatedHoleMask())
                pd.SetDrillSize(V(drill, drill))
            fp.Add(pd)
            if num in pinnet:
                pd.SetNet(nets["/" + pinnet[num]])
        # graphics: fab body, courtyard, silk
        hx, hy = bw / 2, bh / 2
        if sym == "MH":
            add_shape(fp, P.Cmts_User, "circle", (0, 0), (1.6, 0), 0.15)
            add_shape(fp, P.F_CrtYd, "circle", (0, 0), (1.6 + cm + 0.25, 0), 0.05)
        else:
            add_shape(fp, P.F_Fab, "rect", (-hx, -hy), (hx, hy), 0.1)
            xs = [p[1][0] - p[2][0] / 2 for p in pads] + [p[1][0] + p[2][0] / 2 for p in pads] + [-hx, hx]
            ys = [p[1][1] - p[2][1] / 2 for p in pads] + [p[1][1] + p[2][1] / 2 for p in pads] + [-hy, hy]
            add_shape(fp, P.F_CrtYd, "rect", (min(xs) - cm, min(ys) - cm), (max(xs) + cm, max(ys) + cm), 0.05)
            if sym in ("IC8", "CONN4"):
                add_shape(fp, P.F_SilkS, "seg", (-hx - 0.12, -hy - 0.12), (hx + 0.12, -hy - 0.12), 0.12)
                add_shape(fp, P.F_SilkS, "seg", (-hx - 0.12, hy + 0.12), (hx + 0.12, hy + 0.12), 0.12)
            elif sym in ("R", "C"):
                add_shape(fp, P.F_SilkS, "seg", (-0.2, -hy - 0.15), (0.2, -hy - 0.15), 0.12)
                add_shape(fp, P.F_SilkS, "seg", (-0.2, hy + 0.15), (0.2, hy + 0.15), 0.12)
            elif sym == "D":
                add_shape(fp, P.F_SilkS, "seg", (-2.4, -1.0), (-2.4, 1.0), 0.12)
            t = P.FP_TEXT(fp)
            t.SetText("${REFERENCE}")
            t.SetLayer(P.F_Fab)
            t.SetTextSize(V(0.4, 0.4))
            t.SetTextThickness(mm(0.06))
            t.SetPos0(V(0, 0))
            t.SetDrawCoord()
            fp.Add(t)
        r = fp.Reference()
        r.SetLayer(P.F_SilkS)
        r.SetTextSize(V(0.8, 0.8))
        r.SetTextThickness(mm(0.12))
        r.SetPos0(V(0, -max(hy, 1.0) - 1.0 if sym != "MH" else -3.2))
        r.SetDrawCoord()
        v = fp.Value()
        v.SetLayer(P.F_Fab)
        v.SetTextSize(V(0.6, 0.6))
        v.SetTextThickness(mm(0.09))
        v.SetPos0(V(0, max(hy, 1.0) + 1.0 if sym != "MH" else 3.2))
        v.SetDrawCoord()
        if sym == "MH":
            v.SetVisible(False)
        fp.SetPosition(V(x, y))
        if rot:
            fp.SetOrientationDegrees(rot)
        if side == "bottom":
            fp.Flip(V(x, y), False)
        pads_of[ref] = {pd.GetNumber(): pd for pd in fp.Pads()}
        fps[ref] = fp

    def pad_xy(ref, num):
        p = pads_of[ref][num].GetPosition()
        return P.ToMM(p.x), P.ToMM(p.y)

    def track(points, net, layer=P.F_Cu, w=0.25):
        for a, c in zip(points, points[1:]):
            t = P.PCB_TRACK(b)
            t.SetStart(V(*a))
            t.SetEnd(V(*c))
            t.SetWidth(mm(w))
            t.SetLayer(layer)
            t.SetNet(nets[net])
            b.Add(t)

    def via(xy, net, d=0.6, drill=0.3):
        v = P.PCB_VIA(b)
        v.SetPosition(V(*xy))
        v.SetWidth(mm(d))
        v.SetDrill(mm(drill))
        v.SetNet(nets[net])
        b.Add(v)

    def away(ref, num, dist, center=None):
        """A point `dist` mm from a pad, away from the part's centre (where a fan-out via goes)."""
        x0, y0 = pad_xy(ref, num)
        cx, cy = center or [P.ToMM(c) for c in (fps[ref].GetPosition().x, fps[ref].GetPosition().y)]
        dx, dy = x0 - cx, y0 - cy
        if abs(dx) >= abs(dy):
            return (x0 + (dist if dx >= 0 else -dist), y0)
        return (x0, y0 + (dist if dy >= 0 else -dist))

    # signals (top layer, 45-degree fan-out from U1)
    u1, u2, u3, u6 = (pad_xy("U1", n) for n in "1236")
    track([u1, (u1[0] - 1.8, u1[1]), (u1[0] - 1.8 - 6, u1[1] - 6), pad_xy("R1", "2")], "/SIG1")
    track([u2, (u2[0] - 2.3, u2[1]), (u2[0] - 2.3 - 3.365, u2[1] - 3.365), pad_xy("R2", "2")], "/SIG2")
    track([u3, (u3[0] - 1.8, u3[1]), (u3[0] - 1.8 - 5.365, u3[1] + 5.365), pad_xy("R3", "2")], "/SIG3")
    tp = pad_xy("TP1", "1")
    k = tp[1] - u6[1] - 6
    track([u6, (u6[0] + 3.3, u6[1]), (u6[0] + 3.3 + 6, u6[1] + 6), (u6[0] + 3.3 + 6 + k, tp[1]), tp], "/SIG3")
    # power and ground: fan-out vias into the inner planes
    for ref, num, net in (("R1", "1", "/+3V3"), ("R2", "1", "/+3V3"), ("C1", "1", "/+3V3"), ("C2", "1", "/+3V3"),
                          ("U1", "8", "/+3V3"), ("J1", "1", "/+3V3"), ("D1", "1", "/+3V3"),
                          ("R3", "1", "/GND"), ("R4", "1", "/GND"), ("C1", "2", "/GND"), ("C2", "2", "/GND"),
                          ("U1", "4", "/GND"), ("J1", "4", "/GND"), ("R5", "2", "/GND"), ("C3", "2", "/GND")):
        two_pad = len(pads_of[ref]) == 2
        if ref in ("R1", "R2", "R3", "R4"):
            x0, y0 = pad_xy(ref, num)
            dv = (x0, y0 + 1.6)
        elif ref == "J1":
            x0, y0 = pad_xy(ref, num)
            dv = (x0 + 1.9, y0)
        else:
            x0, y0 = pad_xy(ref, num)
            dv = away(ref, num, 1.8 if two_pad else 1.9)
        layer = P.B_Cu if ref in ("R5", "C3", "D1") else P.F_Cu
        track([(x0, y0), dv], net, layer, 0.4)
        via(dv, net, 0.8 if net == "/+3V3" else 0.6, 0.4 if net == "/+3V3" else 0.3)
    # inner planes
    for layer, net in ((P.In1_Cu, "/GND"), (P.In2_Cu, "/+3V3")):
        z = P.ZONE(b)
        z.SetLayer(layer)
        z.SetNetCode(nets[net].GetNetCode())
        z.SetLocalClearance(mm(0.3))
        z.SetMinThickness(mm(0.25))
        z.SetPadConnection(P.ZONE_CONNECTION_THERMAL)
        o = z.Outline()
        o.NewOutline()
        for x, y in ((100.5, 100.5), (159.5, 100.5), (159.5, 139.5), (100.5, 139.5)):
            o.Append(mm(x), mm(y))
        z.SetZoneName(f"{net[1:]}_plane")
        b.Add(z)
    P.SaveBoard(str(OUT / f"{NAME}.kicad_pcb"), b)
    b2 = P.LoadBoard(str(OUT / f"{NAME}.kicad_pcb"))
    P.ZONE_FILLER(b2).Fill(b2.Zones())
    P.SaveBoard(str(OUT / f"{NAME}.kicad_pcb"), b2)
    return pads_of


# ------------------------------------------------------------------ schematic (own simple symbols)
def F(t, x, y, hide=False, ang=0):
    return (f'(property "{t[0]}" "{t[1]}" (at {x:g} {y:g} {ang}) (effects (font (size 1.27 1.27))'
            f'{" hide" if hide else ""}))')


PIN = '(pin {kind} line (at {x:g} {y:g} {a}) (length {ln:g}) (name "{nm}" (effects (font (size 1.27 1.27)))) ' \
      '(number "{num}" (effects (font (size 1.27 1.27)))))'
SYMS = {
    "R": ("R", [("1", 0, 3.81, 270, "~"), ("2", 0, -3.81, 90, "~")], 1.27,
          "(rectangle (start -1.016 -2.54) (end 1.016 2.54) (stroke (width 0.254) (type default)) (fill (type none)))",
          "passive"),
    "C": ("C", [("1", 0, 3.81, 270, "~"), ("2", 0, -3.81, 90, "~")], 2.794,
          "(polyline (pts (xy -2.032 -0.762) (xy 2.032 -0.762)) (stroke (width 0.508) (type default)) (fill (type none)))"
          " (polyline (pts (xy -2.032 0.762) (xy 2.032 0.762)) (stroke (width 0.508) (type default)) (fill (type none)))",
          "passive"),
    "D": ("D", [("1", -3.81, 0, 0, "K"), ("2", 3.81, 0, 180, "A")], 2.54,
          "(polyline (pts (xy -1.27 1.27) (xy -1.27 -1.27)) (stroke (width 0.254) (type default)) (fill (type none)))"
          " (polyline (pts (xy 1.27 1.27) (xy -1.27 0) (xy 1.27 -1.27) (xy 1.27 1.27)) (stroke (width 0.254) "
          "(type default)) (fill (type none)))", "passive"),
    "IC8": ("U", [(str(i + 1), -10.16, 3.81 - 2.54 * i, 0, f"P{i + 1}") for i in range(4)]
            + [(str(8 - i), 10.16, 3.81 - 2.54 * i, 180, f"P{8 - i}") for i in range(4)], 2.54,
            "(rectangle (start -7.62 6.35) (end 7.62 -6.35) (stroke (width 0.254) (type default)) (fill (type background)))",
            "bidirectional"),
    "CONN4": ("J", [(str(i + 1), -5.08, 3.81 - 2.54 * i, 0, f"Pin_{i + 1}") for i in range(4)], 3.81,
              "(rectangle (start -1.27 5.08) (end 1.27 -6.35) (stroke (width 0.254) (type default)) (fill (type background)))",
              "passive"),
    "TP": ("TP", [("1", 0, 0, 90, "1")], 0,
           "(circle (center 0 3.048) (radius 0.762) (stroke (width 0) (type default)) (fill (type none)))", "passive"),
    "MH": ("H", [], 0,
           "(circle (center 0 0) (radius 1.27) (stroke (width 1.27) (type default)) (fill (type none)))", None),
}


def lib_symbol(key):
    pref, pins, ln, gfx, kind = SYMS[key]
    pin_txt = " ".join(PIN.format(kind=kind, x=x, y=y, a=a, ln=ln, nm=nm, num=num) for num, x, y, a, nm in pins)
    bom = "no" if key in ("MH",) else "yes"
    return (f'(symbol "Convi:{key}" (pin_names (offset 0.254)) (in_bom {bom}) (on_board yes) '
            f'{F(("Reference", pref), 0, 7.62)} {F(("Value", key), 0, -7.62)} '
            f'{F(("Footprint", ""), 0, 0, True)} {F(("Datasheet", "~"), 0, 0, True)} '
            f'(symbol "{key}_0_1" {gfx}) (symbol "{key}_1_1" {pin_txt}))')


def build_schematic():
    items = []
    used = sorted({p[1] for p in PARTS})
    # place symbols in a grid; labels on every connected pin
    col, row = 0, 0
    for ref, sym, value, fpname, pinnet, _xy, _rot, _side in PARTS:
        X = 38.1 + col * 38.1
        Y = 38.1 + row * 30.48
        col += 1
        if col == 6:
            col, row = 0, row + 1
        su = U("sym" + ref)
        pins = SYMS[sym][1]
        pin_uuids = " ".join(f'(pin "{num}" (uuid {U("pin" + ref + num)}))' for num, *_ in pins)
        dnp = "no"
        in_bom = "no" if sym in ("MH", "TP") else "yes"
        items.append(
            f'(symbol (lib_id "Convi:{sym}") (at {X:g} {Y:g} 0) (unit 1) (in_bom {in_bom}) (on_board yes) (dnp {dnp}) '
            f'(uuid {su}) {F(("Reference", ref), X + 3.81, Y - 8.89)} {F(("Value", value), X + 3.81, Y + 8.89)} '
            f'{F(("Footprint", "Convi:" + fpname), X, Y, True)} {F(("Datasheet", "~"), X, Y, True)} {pin_uuids} '
            f'(instances (project "{NAME}" (path "/{ROOT}" (reference "{ref}") (unit 1)))))')
        for num, px, py, a, nm in pins:
            if num not in pinnet:
                continue
            sx, sy = X + px, Y - py
            items.append(f'(label "{pinnet[num]}" (at {sx:g} {sy:g} 0) (fields_autoplaced) '
                         f'(effects (font (size 1.27 1.27)) (justify left bottom)) (uuid {U("lab" + ref + num)}))')
    libs = " ".join(lib_symbol(k) for k in used)
    sch = (f'(kicad_sch (version 20230121) (generator eeschema) (uuid {ROOT}) (paper "A3") '
           f'(title_block (title "{NAME}") (rev "1")) (lib_symbols {libs}) '
           + " ".join(items) + ' (sheet_instances (path "/" (page "1"))))')
    (OUT / f"{NAME}.kicad_sch").write_text(sch, encoding="utf-8")


def build_project():
    pro = {
        "board": {"design_settings": {"defaults": {}, "rules": {"min_clearance": 0.15}}},
        "meta": {"filename": f"{NAME}.kicad_pro", "version": 1},
        "net_settings": {"classes": [{"name": "Default", "clearance": 0.2, "track_width": 0.25,
                                      "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6,
                                      "bus_width": 12, "schematic_color": "rgba(0, 0, 0, 0.000)",
                                      "pcb_color": "rgba(0, 0, 0, 0.000)", "line_style": 0,
                                      "diff_pair_gap": 0.25, "diff_pair_width": 0.2, "diff_pair_via_gap": 0.25,
                                      "microvia_diameter": 0.3, "microvia_drill": 0.1}],
                         "meta": {"version": 3}},
        "schematic": {"legacy_lib_dir": "", "legacy_lib_list": []},
        "sheets": [[ROOT, ""]],
        "text_variables": {},
    }
    (OUT / f"{NAME}.kicad_pro").write_text(json.dumps(pro, indent=2), encoding="utf-8")


build_project()
build_schematic()
build_board()
print("written", OUT)
