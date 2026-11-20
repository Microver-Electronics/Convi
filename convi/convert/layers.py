"""KiCad -> Altium layer mapping."""
from __future__ import annotations

from dataclasses import dataclass

from ..kicad.pcb import Board, COPPER_ORDER

# Altium TMechanicalLayerKind enum order (Altium 26 SDK)
MECH_KIND = {name: i for i, name in enumerate([
    "Undefined", "AssemblyTop", "AssemblyBottom", "AssemblyNotes", "Board", "CoatingTop", "CoatingBottom",
    "ComponentCenterTop", "ComponentCenterBottom", "ComponentOutlineTop", "ComponentOutlineBottom",
    "CourtyardTop", "CourtyardBottom", "DesignatorTop", "DesignatorBottom", "Dimensions", "DimensionsTop",
    "DimensionsBottom", "FabNotes", "GluePointsTop", "GluePointsBottom", "GoldPlatingTop", "GoldPlatingBottom",
    "ValueTop", "ValueBottom", "VCut", "3DBodyTop", "3DBodyBottom", "RouteToolPath", "Sheet", "BoardShape",
])}

# Fixed KiCad technical layers
FIXED = {
    "F.SilkS": "TO", "B.SilkS": "BO", "F.Paste": "TP", "B.Paste": "BP", "F.Mask": "TS", "B.Mask": "BS",
}

# Default mechanical assignments: kicad layer -> (mech number, Altium name, kind, pair partner)
DEFAULT_MECH = [
    ("Edge.Cuts", 1, "Board Outline (Edge.Cuts)", "Board", None),
    ("F.Fab", 2, "Assembly Top (F.Fab)", "AssemblyTop", "B.Fab"),
    ("B.Fab", 3, "Assembly Bottom (B.Fab)", "AssemblyBottom", "F.Fab"),
    ("F.CrtYd", 4, "Courtyard Top (F.CrtYd)", "CourtyardTop", "B.CrtYd"),
    ("B.CrtYd", 5, "Courtyard Bottom (B.CrtYd)", "CourtyardBottom", "F.CrtYd"),
    ("F.Adhes", 6, "Glue Points Top (F.Adhes)", "GluePointsTop", "B.Adhes"),
    ("B.Adhes", 7, "Glue Points Bottom (B.Adhes)", "GluePointsBottom", "F.Adhes"),
    ("Dwgs.User", 8, "Drawings (Dwgs.User)", "FabNotes", None),
    ("Cmts.User", 9, "Comments (Cmts.User)", "Undefined", None),
    ("Eco1.User", 10, "Eco1.User", "Undefined", None),
    ("Eco2.User", 11, "Eco2.User", "Undefined", None),
    ("Margin", 12, "Margin", "Undefined", None),
]
BODY_TOP = 13
BODY_BOTTOM = 14
FIRST_USER_MECH = 15


@dataclass
class MechLayer:
    number: int
    name: str
    kind: str
    pair: int | None = None
    kicad: str = ""


class LayerMap:
    def __init__(self, board: Board, issues=None):
        self.board = board
        self.issues = issues
        self.code: dict[str, str] = {}
        self.copper: list[str] = []          # kicad copper names in stack order
        self.mech: dict[int, MechLayer] = {}
        cu = [l.name for l in board.copper_layers]
        if not cu:
            cu = ["F.Cu", "B.Cu"]
        self.copper = cu
        n = len(cu)
        for i, name in enumerate(cu):
            if i == 0:
                self.code[name] = "T"
            elif i == n - 1:
                self.code[name] = "B"
            else:
                self.code[name] = f"M{i}"
        # Mid layers that exist only in KiCad naming (e.g. In5.Cu on a 4-layer board) map by order
        for name in COPPER_ORDER:
            self.code.setdefault(name, None)
        self.code.update(FIXED)
        for kname, num, aname, kind, partner in DEFAULT_MECH:
            disp = board.layer(kname).display if board.layer(kname) else kname
            if disp != kname and kname not in ("Edge.Cuts",):
                aname = f"{disp} ({kname})"
            self.mech[num] = MechLayer(num, aname, kind, kicad=kname)
            self.code[kname] = f"MECH{num}"
        for kname, num, aname, kind, partner in DEFAULT_MECH:
            if partner:
                pnum = [d[1] for d in DEFAULT_MECH if d[0] == partner][0]
                self.mech[num].pair = pnum
        self.mech[BODY_TOP] = MechLayer(BODY_TOP, "3D Body Top", "3DBodyTop", BODY_BOTTOM)
        self.mech[BODY_BOTTOM] = MechLayer(BODY_BOTTOM, "3D Body Bottom", "3DBodyBottom", BODY_TOP)
        nxt = FIRST_USER_MECH
        for l in board.layers:
            if l.name.startswith("User.") or (l.name not in self.code and not l.is_copper):
                if l.name in self.code:
                    continue
                self.mech[nxt] = MechLayer(nxt, l.display if l.display == l.name else f"{l.display} ({l.name})",
                                           "Undefined", kicad=l.name)
                self.code[l.name] = f"MECH{nxt}"
                nxt += 1
        self.used: set[str] = set()
        self.board_layers = {l.name for l in board.layers}
        self._reported: set[str] = set()

    def get(self, kicad_layer: str) -> str | None:
        c = self.code.get(kicad_layer)
        if c is None and kicad_layer.endswith(".Cu") and kicad_layer.startswith("In"):
            if self.issues:
                self.issues.warn("pcb", f"Item on non-existent copper layer {kicad_layer} skipped")
            return None
        if c is None and self.issues and kicad_layer not in self._reported:
            self._reported.add(kicad_layer)
            if kicad_layer.startswith("User.") and kicad_layer not in self.board_layers:
                # a layer the board does not enable (plugin settings, e.g. a via stitching tool's JSON
                # text on User.9): KiCad does not show these items either
                self.issues.info("pcb", f"Items on KiCad layer '{kicad_layer}', which is not enabled on the board "
                                        "(KiCad does not show them either), skipped")
            else:
                self.issues.warn("pcb", f"Unknown KiCad layer '{kicad_layer}' - items skipped")
        if c:
            self.used.add(c)
        return c

    def is_copper_code(self, code: str) -> bool:
        return code in ("T", "B") or (code.startswith("M") and not code.startswith("MECH"))

    def used_mech(self) -> list[MechLayer]:
        nums = set()
        for c in self.used:
            if c.startswith("MECH"):
                n = int(c[4:])
                nums.add(n)
                if self.mech.get(n) and self.mech[n].pair:
                    nums.add(self.mech[n].pair)
        return [self.mech[n] for n in sorted(nums) if n in self.mech]
