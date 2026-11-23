"""KiCad board -> Convi IR (Altium PCB + PCB library)."""
from __future__ import annotations

import copy
import hashlib
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..altium.ir import IRWriter, mm
from ..geom import arc_from_3pts_yup, arc_points, bezier_points, dist, rot
from ..kicad.common import Issues
from ..kicad.pcb import FLIP_LAYER, Board, Footprint, Pad, Shape, Text, Zone, parse_table
from .markers import marker_kind
from ..models3d import ModelResolver
from . import outline as OL
from .layers import BODY_BOTTOM, BODY_TOP, MECH_KIND, LayerMap

TEXT_VAR_RE = re.compile(r"\$\{([^}]+)\}")
# Altium TrueType Size that draws the same glyphs as KiCad's outline font of size 1
# (measured on Gerbers: Altium ink = 0.6405 x KiCad ink at equal size, width and cap height alike)
TTF_SCALE = 1 / 0.6405


def sanitize_name(s: str, maxlen: int = 120) -> str:
    s = s.replace("\t", " ").replace("\n", " ").strip()
    return s[:maxlen] or "UNNAMED"


@dataclass
class FootprintDef:
    name: str
    fp: Footprint             # representative instance
    signature: str
    instances: list = field(default_factory=list)
    height_mm: float = 0.0


@dataclass
class Options:
    teardrops: str = "regions"        # regions / skip
    placeholder_bodies: bool = False  # extruded box for parts without any 3D model
    origin: str = "auto"              # auto / aux / board / none
    keep_kicad_layer_names: bool = True


# KiCad 'remove unused layers' on vias -> Altium removed pad shapes: off until the script call is known
VIA_RING_REMOVAL = False


def _dp(pts, tol):
    """Douglas-Peucker, iterative."""
    n = len(pts)
    if n < 3:
        return pts
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        a, b = pts[i], pts[j]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        best, bk = -1.0, -1
        for k in range(i + 1, j):
            p = pts[k]
            d = (abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / L) if L > 1e-12 else math.hypot(p[0] - a[0], p[1] - a[1])
            if d > best:
                best, bk = d, k
        if bk >= 0 and best > tol:
            keep[bk] = True
            stack += [(i, bk), (bk, j)]
    return [p for p, k in zip(pts, keep) if k]

def merge_fp_lines(shapes, skip_layer, tol=0.002):
    """Footprint graphic lines (not copper / outline) that share end points, one layer, width and style:
    walked into polylines and simplified within tol mm (Douglas-Peucker).  Some footprint libraries draw 3D body
    outlines as segments of about 1 um on User layers: 165k lines -> 28k on a test board, each one an Altium track
    the builder adds at about 1500 per second.  Groups of fewer than 8 lines stay as they are."""
    groups = defaultdict(list)
    out = []
    for sh in shapes:
        lp = sh.local_pts
        if sh.kind != "line" or sh.fill or not lp or len(lp) != 2 or skip_layer(sh.layer):
            out.append(sh)
            continue
        groups[(sh.layer, sh.width, sh.stroke_style)].append(sh)
    for key, lines in groups.items():
        if len(lines) < 8:
            out += lines
            continue
        R = lambda p: (round(p[0], 6), round(p[1], 6))
        adj = defaultdict(list)
        segs = []
        for i, sh in enumerate(lines):
            a, b = R(sh.local_pts[0]), R(sh.local_pts[1])
            if a == b:
                continue            # zero length: a dot; kept only if nothing else touches it (below)
            segs.append((a, b))
            adj[a].append(len(segs) - 1)
            adj[b].append(len(segs) - 1)
        dots = [sh for sh in lines if R(sh.local_pts[0]) == R(sh.local_pts[1]) and R(sh.local_pts[0]) not in adj]
        used = [False] * len(segs)
        paths = []
        def walk(start, si):
            path = [start]
            p = start
            while True:
                used[si] = True
                a, b = segs[si]
                q = b if a == p else a
                path.append(q)
                nxt = [s for s in adj[q] if not used[s]]
                if len(adj[q]) != 2 or not nxt:
                    return path
                p, si = q, nxt[0]
        for node, ss in adj.items():
            if len(ss) != 2:
                for si in ss:
                    if not used[si]:
                        paths.append(walk(node, si))
        for si in range(len(segs)):
            if not used[si]:
                paths.append(walk(segs[si][0], si))
        proto = lines[0]
        for path in paths:
            for a, b in zip(*(lambda s: (s, s[1:]))(_dp(path, tol))):
                ns = copy.copy(proto)
                ns.local_pts = [a, b]
                out.append(ns)
        out += dots
    return out


class PcbConverter:
    def __init__(self, board: Board, resolver: ModelResolver, issues: Issues, net_name_map: dict | None = None,
                 netclasses=None, design_rules=None, text_vars: dict | None = None, options: Options | None = None,
                 lib_name: str = "Convi.PcbLib", sch_lib_name: str = "Convi.SchLib", symbol_of: dict | None = None,
                 project=None):
        self.project = project
        self.dru_path = None
        self.polygon_names: list = []
        self.polygon_layer: dict = {}     # polygon name -> KiCad copper layer
        self.unreached_polygons: list = []  # polygons poured with dead copper kept (nothing of the net reaches)
        self.b = board
        self.res = resolver
        self.issues = issues
        self.opt = options or Options()
        self.lm = LayerMap(board, issues)
        self.netmap = net_name_map or {}
        self.netclasses = netclasses or []
        self.rules = design_rules
        self.text_vars = text_vars or {}
        self.lib_name = lib_name
        self.sch_lib_name = sch_lib_name
        self.symbol_of = symbol_of or {}   # footprint path -> (libref, uniqueid path, description)
        self.relinked: set = set()         # id(fp) of board footprints linked to a symbol by Convi
        self.des_override: dict[int, str] = {}  # id(fp) -> unique designator for repeated board-only refs
        self.board_size_mm: tuple | None = None  # outline bounding box (w, h)
        self.net_index: dict[str, int] = {}
        self.fpdefs: dict[str, FootprintDef] = {}
        self.fp_name_of: dict[int, str] = {}   # id(fp) -> def name
        self.stats = {"bodies": 0, "bodies_missing": 0}
        self.missing_models: dict[str, list] = {}
        self._compute_offset()

    # ------------------------------------------------------------ transforms
    def _compute_offset(self):
        pts = []
        for sh in self.b.shapes:
            if sh.layer == "Edge.Cuts":
                pts.extend(sh.pts)
        for fp in self.b.footprints:
            for sh in fp.shapes:
                if sh.layer == "Edge.Cuts":
                    pts.extend(sh.pts)
        if not pts:
            for fp in self.b.footprints:
                pts.append(fp.pos)
            for t in self.b.tracks:
                pts.extend([t.start, t.end])
        if not pts:
            pts = [(0, 0)]
        self.minx = min(p[0] for p in pts)
        self.maxx = max(p[0] for p in pts)
        self.miny = min(p[1] for p in pts)
        self.maxy = max(p[1] for p in pts)
        margin = 25.4  # 1000 mil from Altium's absolute zero
        self.ox = margin - self.minx + 25.4 * 4
        self.oy = margin + self.maxy + 25.4 * 4

    def X(self, x: float) -> int:
        return mm(x + self.ox)

    def Y(self, y: float) -> int:
        return mm(self.oy - y)

    def P(self, p) -> tuple[int, int]:
        return self.X(p[0]), self.Y(p[1])

    # ------------------------------------------------------------ nets
    def net_id(self, name: str) -> int:
        if not name:
            return 0
        return self.net_index.get(name, 0)

    def altium_net_name(self, kname: str) -> str | None:
        if kname in self.netmap:
            return self.netmap[kname]
        if not kname or kname.startswith("unconnected-("):
            return None
        return kname

    # ------------------------------------------------------------ text
    def subst(self, text: str, fp: Footprint | None = None) -> str:
        def rep(m):
            k = m.group(1)
            if fp is not None:
                if k == "REFERENCE":
                    return fp.ref
                if k == "VALUE":
                    return fp.value
                if k in fp.properties:
                    return fp.properties[k]
                if k == "FOOTPRINT_NAME":
                    return fp.lib_name
                if k == "FOOTPRINT_LIBRARY":
                    return fp.lib_nick
            if k in self.text_vars:
                return self.text_vars[k]
            if k in self.b.properties:
                return self.b.properties[k]
            tb = self.b.title_block
            mapping = {"TITLE": "title", "REVISION": "rev", "ISSUE_DATE": "date", "COMPANY": "company"}
            if k in mapping and mapping[k] in tb:
                return tb[mapping[k]]
            if k.startswith("COMMENT") and k[7:].isdigit():
                return tb.get(f"comment{k[7:]}", "")
            return m.group(0)
        return TEXT_VAR_RE.sub(rep, text)

    @staticmethod
    def lib_text(text: str) -> str:
        """Text for library footprints: KiCad variables -> Altium special strings."""
        t = text.strip()
        if t in ("${REFERENCE}", "%R"):
            return ".Designator"
        if t in ("${VALUE}", "%V"):
            return ".Comment"
        return text

    # ------------------------------------------------------------ main
    def build_pcb(self, ir: IRWriter, out_pcb: Path):
        b = self.b
        ir.comment(f"Convi PCB from {b.path.name} (KiCad file version {b.version})")
        ir.cmd("NEWPCB", path=str(out_pcb))
        self._emit_stack(ir)
        # nets
        names = []
        for n in b.nets:
            an = self.altium_net_name(n)
            if an and an not in names:
                names.append(an)
        idx = {an: i + 1 for i, an in enumerate(names)}
        for kn in b.nets:
            an = self.altium_net_name(kn)
            if an:
                self.net_index[kn] = idx[an]
        for an, i in idx.items():
            ir.cmd("NET", i=i, name=an)
        self._emit_outline(ir)
        self._prepare_footprint_defs()
        self._unique_designators()
        for fp in b.footprints:
            self._emit_component(ir, fp)
        for t in b.tracks:
            self._emit_track(ir, t)
        for a in b.arcs:
            self._emit_track_arc(ir, a)
        for v in b.vias:
            self._emit_via(ir, v)
        for sh in b.shapes:
            self._emit_shape(ir, sh, board_level=True)
        for tx in b.texts:
            self._emit_free_text(ir, tx)
        self._emit_zones(ir)
        self._emit_dimensions(ir)
        for tb in b.tables:
            self._emit_table(ir, tb)
        for tag, coll in (("barcode", b.barcodes), ("image", b.images)):
            if coll:
                self.issues.warn("pcb", f"{len(coll)} {tag} object(s) on the board are not converted")
        self._emit_classes_rules(ir)
        org = self._origin()
        if org is not None:
            ir.cmd("ORIGIN", x=self.X(org[0]), y=self.Y(org[1]))
        # vias with KiCad 'remove unused layers': handled after the pour (pipeline._unused_via_pads)
        ml = getattr(self, "merged_lines", None)
        if ml:
            self.issues.info("pcb", f"Footprint drawing lines joined: {ml[0]} short lines (curves drawn as many tiny "
                                    f"segments) placed as {ml[1]} tracks, within 0.002 mm of the original")
        if getattr(self, "dup_graphics", 0):
            self.issues.info("pcb", f"{self.dup_graphics} footprint graphic(s) drawn twice on the same layer in KiCad "
                                    "were placed once")
        if getattr(self, "thin_stroke_shapes", 0):
            self.issues.info("pcb", "Filled copper shapes with a hairline outline (thinner than the minimum track "
                                    "width, e.g. logos) are converted as regions without the outline: as tracks "
                                    "the outline fails Altium's width rule, which KiCad never applies to shapes")
        ir.cmd("REPOUR")
        ir.cmd("SAVE")

    def _origin(self):
        if self.opt.origin == "none":
            return None
        aux = self.b.setup.get("aux_axis_origin")
        if self.opt.origin in ("auto", "aux") and aux and (abs(aux[0]) > 1e-9 or abs(aux[1]) > 1e-9):
            return aux
        return (self.minx, self.maxy)

    # ------------------------------------------------------------ stack
    def _emit_stack(self, ir: IRWriter):
        cu = self.lm.copper
        st = {s.name: s for s in self.b.stackup}
        ir.cmd("LAYERS", cu=len(cu), thickness=mm(self.b.thickness))
        stack_names = [s.name for s in self.b.stackup]
        for i, name in enumerate(cu):
            lay = self.b.layer(name)
            disp = lay.display if lay else name
            th = st[name].thickness if name in st and st[name].thickness else 0.035
            ir.cmd("CU", i=i, name=disp if disp else name, th=mm(th))
            # dielectric(s) below this copper layer
            if i < len(cu) - 1 and name in stack_names:
                j = stack_names.index(name) + 1
                h = 0.0
                er = 0.0
                tand = 0.0
                mat = ""
                typ = "core"
                while j < len(stack_names) and not stack_names[j].endswith(".Cu"):
                    d = self.b.stackup[j]
                    if d.type.lower() in ("core", "prepreg") or d.name.startswith("dielectric"):
                        h += d.thickness + sum(sl.get("thickness", 0.0) for sl in d.sublayers)
                        er = er or d.epsilon_r
                        tand = tand or d.loss_tangent
                        mat = mat or d.material
                        typ = d.type.lower() if d.type else typ
                    j += 1
                if h > 0:
                    ir.cmd("DIEL", i=i, h=mm(h), er=float(er or 4.5), tand=float(tand or 0.02), mat=mat or "FR-4",
                           type="prepreg" if "prepreg" in typ else "core")
            elif i < len(cu) - 1:
                n_d = len(cu) - 1
                h = max(self.b.thickness - 0.035 * len(cu), 0.1) / n_d
                ir.cmd("DIEL", i=i, h=mm(h), er=4.5, tand=0.02, mat="FR-4", type="core")
        for side, nm in (("T", "F.Mask"), ("B", "B.Mask")):
            if nm in st and st[nm].thickness:
                ir.cmd("MASK", side=side, h=mm(st[nm].thickness), er=float(st[nm].epsilon_r or 3.5),
                       mat=st[nm].material or "Solder Resist")

    def emit_mech(self, ir: IRWriter):
        """Must be called after all geometry was generated (to know the used layers)."""
        self.lm.used.update({f"MECH{BODY_TOP}", f"MECH{BODY_BOTTOM}", "MECH1"})
        for ml in self.lm.used_mech():
            ir.cmd("MECH", n=ml.number, name=ml.name, kind=MECH_KIND.get(ml.kind, 0), pair=ml.pair or 0)

    def designator(self, fp: Footprint) -> str:
        """The Altium designator of a footprint: the schematic's, else the board's (made unique)."""
        if id(fp) in self.des_override:
            return self.des_override[id(fp)]
        return (self.symbol_of.get(fp.path, {}) or {}).get("ref") or fp.ref

    def _unique_designators(self):
        """Board-only footprints that share a reference (KiCad logos are all 'G***') get G1, G2 ...:
        Altium's Draftsman stops updating its board data on a repeated designator, without a message."""
        self.des_override = {}
        count = Counter(self.designator(fp) for fp in self.b.footprints)
        used = set(count)
        renamed = []
        for fp in self.b.footprints:
            des = self.designator(fp)
            if (count[des] < 2 and "*" not in des and "?" not in des) or self.symbol_of.get(fp.path):
                continue
            prefix = re.sub(r"[^A-Za-z_]", "", des.split("*")[0].split("?")[0]) or "X"
            k = 1
            while f"{prefix}{k}" in used:
                k += 1
            new = f"{prefix}{k}"
            used.add(new)
            self.des_override[id(fp)] = new
            renamed.append(f"{des} -> {new}")
        if renamed:
            self.issues.info("pcb", f"{len(renamed)} board-only footprint(s) share a reference in KiCad and get a "
                                    "unique Altium designator (Draftsman needs unique designators): "
                                    + ", ".join(renamed[:20]))

    # ------------------------------------------------------------ outline
    def _emit_outline(self, ir: IRWriter):
        edge = [s for s in self.b.shapes if s.layer == "Edge.Cuts"]
        for fp in self.b.footprints:
            edge.extend(s for s in fp.shapes if s.layer == "Edge.Cuts")
        outer, cutouts, problems = OL.build_board_outline(edge)
        self.board_cutouts = len(cutouts)
        for p in problems:
            self.issues.warn("pcb", p)
        if outer is None:
            return
        pts = OL.contour_points(outer, 0.005)
        self.board_size_mm = (max(p[0] for p in pts) - min(p[0] for p in pts),
                              max(p[1] for p in pts) - min(p[1] for p in pts))
        # orient outer CCW in the Altium frame (Y up) == CW in KiCad frame
        segs = self._contour_to_osegs(outer)
        ir.cmd("OUTLINE", n=len(segs))
        for s in segs:
            ir.cmd("OSEG", **s)
        ir.cmd("OUTLINEEND")
        for c in cutouts:
            pts = OL.contour_points(c, 0.005)
            self._emit_region(ir, "MULTI", [pts], kind="boardcutout")

    def _contour_to_osegs(self, cont):
        out = []
        for s in cont:
            if s.kind == "line":
                out.append(dict(k="L", x=self.X(s.a[0]), y=self.Y(s.a[1])))
            else:
                a = self._arc_alt(s.a, s.m, s.b)
                if a is None:
                    out.append(dict(k="L", x=self.X(s.a[0]), y=self.Y(s.a[1])))
                    continue
                (cx, cy), r, a1, a2 = a
                out.append(dict(k="A", x=self.X(s.a[0]), y=self.Y(s.a[1]), cx=cx, cy=cy, r=r, a1=a1, a2=a2))
        return out

    def _arc_alt(self, s, m, e, frame=None):
        """Arc through 3 KiCad points -> Altium (center(int), r(int), a1, a2)."""
        P = frame or self.P
        sa, ma, ea = P(s), P(m), P(e)
        r = arc_from_3pts_yup(sa, ma, ea)
        if r is None:
            return None
        (cx, cy), rad, a1, a2 = r
        return (int(round(cx)), int(round(cy))), int(round(rad)), float(a1), float(a2)

    # ------------------------------------------------------------ footprint defs
    def _lib_geom_signature(self, fp: Footprint) -> str:
        h = hashlib.sha1()
        L = self._libpt_fn(fp)
        pads = []
        for p in fp.pads:     # compared in the library frame: a bottom-side copy lists its pads in another order
            lp = L(p.local_pos)
            pads.append(repr((p.number, p.type, p.shape, round(lp[0], 4) + 0.0, round(lp[1], 4) + 0.0,
                           round(self._pad_lib_rot(fp, p) % 360, 3), round(p.size[0], 4), round(p.size[1], 4),
                           p.drill and (p.drill.shape, round(p.drill.w, 4), round(p.drill.h, 4)),
                           tuple(sorted(self._lib_layers(fp, p.layers))), round(p.rr_ratio, 4),
                           tuple(sorted(p.chamfer)), round(p.chamfer_ratio, 4))))
        for it in sorted(pads):
            h.update(it.encode())
        items = []
        for s in fp.shapes:
            # + 0.0: -0.0 and 0.0 are the same place (a bottom-side copy flips the sign of a zero)
            pts = [(round(a, 3) + 0.0, round(b, 3) + 0.0) for a, b in (L(q) for q in (s.local_pts or []))]
            kind = s.kind
            if kind == "line" and len(pts) == 2:
                pts = sorted(pts)       # a line drawn the other way round is the same line
            # KiCad saves a rectangle of a footprint placed at 45 / 135 deg as a 4-point polygon (two R_0402
            # parts at different angles): the same drawing, so no library variant for it
            if kind in ("rect", "poly") and len(pts) in (2, 4) and (kind == "rect" or (
                    len({x for x, _ in pts}) == 2 and len({y for _, y in pts}) == 2)):
                xs, ys = [x for x, _ in pts], [y for _, y in pts]
                kind, pts = "rect", [(min(xs), min(ys)), (max(xs), max(ys))]
            items.append(repr((self._lib_layer(fp, s.layer), kind, pts, round(s.width, 3), s.fill)))
        for it in sorted(items):
            h.update(it.encode())
        for m in fp.models:
            h.update(repr((m.path, tuple(round(v, 6) + 0.0 for v in m.offset),
                           tuple(round(v, 6) + 0.0 for v in m.rotate), m.scale, m.show)).encode())
        return h.hexdigest()[:16]

    def _prepare_footprint_defs(self):
        by_name: dict[str, list[FootprintDef]] = {}
        for fp in self.b.footprints:
            base = sanitize_name(fp.lib_name or fp.ref or "FOOTPRINT")
            sig = self._lib_geom_signature(fp)
            lst = by_name.setdefault(base, [])
            for d in lst:
                if d.signature == sig:
                    d.instances.append(fp)
                    self.fp_name_of[id(fp)] = d.name
                    break
            else:
                name = base if not lst else f"{base}_{len(lst) + 1}"
                d = FootprintDef(name=name, fp=fp, signature=sig, instances=[fp])
                lst.append(d)
                self.fpdefs[name] = d
                self.fp_name_of[id(fp)] = name
                if lst and len(lst) > 1:
                    self.issues.info("lib", f"Footprint '{base}' has instance-specific modifications; "
                                            f"created variant '{name}' (e.g. {fp.ref})")

    # ------------------------------------------------------------ lib frame helpers
    @staticmethod
    def _libpt_fn(fp: Footprint):
        if fp.flipped:
            return lambda p: (p[0], p[1])
        return lambda p: (p[0], -p[1])

    @staticmethod
    def _lib_layer(fp: Footprint, layer: str) -> str:
        return FLIP_LAYER.get(layer, layer) if fp.flipped else layer

    def _lib_layers(self, fp, layers):
        return [self._lib_layer(fp, l) for l in layers]

    @staticmethod
    def _pad_lib_rot(fp: Footprint, pad: Pad) -> float:
        return (fp.angle - pad.angle) if fp.flipped else (pad.angle - fp.angle)

    def _fp_local_from_abs(self, fp: Footprint, p) -> tuple:
        dx, dy = p[0] - fp.pos[0], p[1] - fp.pos[1]
        return rot(dx, dy, -fp.angle)

    # ------------------------------------------------------------ component
    def _emit_component(self, ir: IRWriter, fp: Footprint):
        name = self.fp_name_of[id(fp)]
        ax, ay = self.P(fp.pos)
        kind = "standard"
        attrs = fp.attrs
        unlinked = bool(self.symbol_of) and fp.path not in self.symbol_of
        if "board_only" in attrs or unlinked:
            kind = "graphical" if "exclude_from_bom" in attrs else "mechanical"
        elif fp.net_tie_groups:
            kind = "nettie_nobom" if "exclude_from_bom" in attrs else "nettie_bom"
        elif "exclude_from_bom" in attrs:
            kind = "standard_nobom"
        sym = self.symbol_of.get(fp.path, {})
        des = self.designator(fp)
        if marker_kind(des, fp.lib_id, name):
            # house rule: test points, mounting holes and fiducials are never assembled; a
            # board-only one is graphical (not synchronised: Update PCB leaves it alone)
            kind = "standard_nobom" if sym else "graphical"
        if des != fp.ref and id(fp) not in self.relinked and id(fp) not in self.des_override:
            self.issues.info("link", f"Footprint '{fp.ref}' takes the schematic designator '{des}' "
                                     "(the KiCad board was not updated from the schematic)")
        height = self._fp_height(fp)
        ref_txt = next((t for t in fp.texts if t.kind == "reference"), None)
        val_txt = next((t for t in fp.texts if t.kind == "value"), None)
        ir.cmd("COMP", x=ax, y=ay, des=des, pattern=name, comment=fp.value, kind=kind,
               h=mm(height), fplib=self.lib_name, desc=fp.descr,
               uid=sym.get("uid", ""), libref=sym.get("libref", ""), complib=self.sch_lib_name if sym else "",
               locked=fp.locked)
        self._emit_fp_prims(ir, fp, (fp.pos[0] + self.ox, self.oy - fp.pos[1]), for_board=True)
        ir.cmd("COMPEND", flip=fp.flipped, rot=float(fp.angle % 360), x=ax, y=ay)
        # check: first pad absolute position computed by KiCad vs Altium result
        if fp.pads:
            p0 = fp.pads[0]
            ir.cmd("CHECKPAD", des=des, pad=p0.number, x=self.X(p0.pos[0]), y=self.Y(p0.pos[1]))
        for which, t in (("name", ref_txt), ("comment", val_txt)):
            if t is None:
                ir.cmd("COMPTEXT", which=which, show=0)
                continue
            lay = self.lm.get(t.layer) if t.layer else None
            show = (not t.hidden) and lay is not None
            txt = self.subst(t.text, fp)
            if which == "name" and txt == fp.ref:
                txt = des
            if which == "name" and id(fp) in self.relinked:
                show = False      # the KiCad label is kept as a component text (_emit_fp_prims)
            if which == "comment":
                # house rule: the part value never shows on the board. KiCad libraries draw it on F.Fab
                # (shown), so it would land on the assembly layer and in assembly prints; Altium keeps it
                # as the component Comment (BOM, parameters), hidden like Altium's own libraries do.
                show = False
            self._text_cmd(ir, "COMPTEXT", t, t.pos, t.angle, lay or ("TO" if not fp.flipped else "BO"),
                           txt, which=which, show=show)
        for k, v in fp.properties.items():
            if k in ("Reference", "Value"):
                continue
            ir.cmd("COMPPARAM", name=k, value=v)

    def _fp_height(self, fp: Footprint) -> float:
        return 0.0

    def _fp_shapes(self, fp: Footprint) -> list:
        """fp.shapes with long runs of tiny graphic lines joined (merge_fp_lines), cached per footprint."""
        cache = self.__dict__.setdefault("_fp_shape_cache", {})
        got = cache.get(id(fp))
        if got is None:
            # a graphic drawn twice on one layer is dropped (some library footprints: 132 of 268 User.9
            # lines repeated; Altium's Component Rule Check: 'primitives drawn twice')
            uniq, seen = [], set()
            for x in fp.shapes:
                lp = getattr(x, "local_pts", None) or x.pts
                pts = tuple(tuple(round(c, 5) for c in q) for q in lp)
                if x.kind == "line":
                    pts = tuple(sorted(pts))
                k = (x.kind, x.layer, round(x.width, 5), x.fill, pts, repr(getattr(x, "arcs", None)))
                if x.layer.endswith(".Cu") or k not in seen:
                    seen.add(k)
                    uniq.append(x)
            if len(uniq) < len(fp.shapes):
                self.dup_graphics = getattr(self, "dup_graphics", 0) + len(fp.shapes) - len(uniq)
            got = merge_fp_lines(uniq, lambda L: L.endswith(".Cu") or L in ("Edge.Cuts", "Margin"))
            # two overlapping source lines can simplify to the same segment (e.g. FB_0603 / SW_DS04 User.9 drawings):
            # placed once
            seen2, out2 = set(), []
            for x in got:
                if x.kind == "line" and not x.layer.endswith(".Cu") and x.local_pts and len(x.local_pts) == 2:
                    k = (x.layer, round(x.width, 5), tuple(sorted(tuple(round(c, 5) for c in q) for q in x.local_pts)))
                    if k in seen2:
                        self.dup_graphics = getattr(self, "dup_graphics", 0) + 1
                        continue
                    seen2.add(k)
                out2.append(x)
            got = out2
            n0 = sum(1 for x in uniq if x.kind == "line")
            n1 = sum(1 for x in got if x.kind == "line")
            if n1 < n0:
                self.merged_lines = getattr(self, "merged_lines", (0, 0))
                self.merged_lines = (self.merged_lines[0] + n0, self.merged_lines[1] + n1)
            cache[id(fp)] = got
        return got

    def _emit_fp_prims(self, ir: IRWriter, fp: Footprint, origin_mm: tuple, for_board: bool):
        """Emit footprint primitives in the library frame, offset by origin_mm (Altium frame, mm)."""
        L = self._libpt_fn(fp)
        ox, oy = origin_mm

        def A(p_local):
            q = L(p_local)
            return mm(ox + q[0]), mm(oy + q[1])

        for pad in fp.pads:
            self._emit_pad(ir, fp, pad, A, for_board)
        for sh in self._fp_shapes(fp):
            if sh.layer == "Edge.Cuts" and for_board:
                # board outline already handled; keep a copy on the outline mech layer
                pass
            self._emit_shape(ir, sh, fp=fp, A=A, for_board=for_board)
        for tx in fp.texts:
            relabel = (for_board and tx.kind == "reference" and id(fp) in self.relinked
                       and not tx.hidden and tx.layer)
            if tx.kind in ("reference", "value") and not relabel:
                continue
            if tx.kind == "field" and (tx.hidden or not tx.layer):
                continue
            if tx.hidden:
                continue
            lay = self.lm.get(self._lib_layer(fp, tx.layer))
            if not lay:
                continue
            lp = L(tx.local_pos or (0, 0))
            lang = (fp.angle - tx.angle) if fp.flipped else (tx.angle - fp.angle)
            text = self.lib_text(tx.text) if not for_board else self._board_fp_text(tx.text, fp)
            if relabel:
                # a test point the KiCad board named after its net ('VBUS'), linked to its schematic
                # symbol (TP301): the board keeps showing the KiCad label, as a component text
                text = self.subst(tx.text, fp)
            if tx.kind == "textbox":
                self._emit_textbox(ir, tx, fp=fp, A=A, layer=lay, text=text)
                continue
            self._text_cmd(ir, "TEXT", tx, None, lang, lay, text, pos_int=(mm(ox + lp[0]), mm(oy + lp[1])),
                           mirror_override=tx.effects.mirror if not fp.flipped else not tx.effects.mirror,
                           absmap=(lambda p, fp=fp: A(self._fp_local_from_abs(fp, p))) if for_board else False,
                           angmap=(lambda ra, fp=fp: (fp.angle - ra) if fp.flipped else (ra - fp.angle)),
                           shown=self.subst(tx.text, fp) if for_board else None)
        for z in fp.zones:
            self._emit_fp_zone(ir, fp, z, A)
        placed = 0
        for m in fp.models:
            placed += self._emit_body(ir, fp, m, origin_mm)
        if placed == 0 and self.opt.placeholder_bodies and fp.pads and "board_only" not in fp.attrs and \
                not marker_kind(fp.ref, fp.lib_id, fp.lib_name, fp.value) and \
                not ({"exclude_from_bom", "dnp"} & set(fp.attrs)):
            # no box for test points, mounting holes, fiducials and other non-assembled parts (one test
            # board had 60 test-point boxes over it)
            self._emit_placeholder(ir, fp, origin_mm)
        if fp.raw_unsupported:
            # KiCad 10 'point' (snap points) and groups carry no geometry for Altium
            kinds = sorted(set(n[0] for n in fp.raw_unsupported) - {"point", "group"})
            if kinds:
                self.issues.warn("pcb", f"{fp.ref}: footprint items not converted: {', '.join(kinds)}")

    def _board_fp_text(self, text: str, fp: Footprint) -> str:
        t = self.lib_text(text)
        if t in (".Designator", ".Comment"):
            return t
        return self.subst(text, fp)

    # ------------------------------------------------------------ pads
    def _pad_geom(self, shape, size, rr, chamfer, chamfer_ratio, delta):
        """Return (altium shape code, sx, sy, cr%, custom polygon or None) in pad-local Altium frame."""
        sx, sy = size
        if shape == "circle":
            return "R", sx, sx, 0, None
        if shape == "oval":
            return "R", sx, sy, 0, None
        if shape == "rect":
            if chamfer and chamfer_ratio > 0:
                return "S", sx, sy, 0, self._chamfer_poly(sx, sy, 0, chamfer, chamfer_ratio)
            return "S", sx, sy, 0, None
        if shape == "roundrect":
            if chamfer and chamfer_ratio > 0:
                return "S", sx, sy, 0, self._chamfer_poly(sx, sy, rr, chamfer, chamfer_ratio)
            return "RR", sx, sy, min(100.0, rr * 200.0), None
        if shape == "trapezoid":
            dx, dy = delta
            if abs(dx) < 1e-9 and abs(dy) < 1e-9:
                return "S", sx, sy, 0, None
            # KiCad: delta.x widens bottom/narrows top along Y?  rect_delta (dx dy):
            # corners (Y-down local): top-left (-w/2 - dy/2, -h/2 + dx/2) ...
            hw, hh = sx / 2, sy / 2
            ddx, ddy = dx / 2, dy / 2
            pts_k = [(-hw - ddy, hh + ddx), (hw + ddy, hh - ddx), (hw - ddy, -hh + ddx), (-hw + ddy, -hh - ddx)]
            pts = [(p[0], -p[1]) for p in pts_k]
            return "S", min(sx, sy) * 0.5, min(sx, sy) * 0.5, 0, pts
        return "S", sx, sy, 0, None

    @staticmethod
    def _chamfer_poly(sx, sy, rr, chamfer, ratio):
        """Chamfered (optionally rounded) rectangle outline, Altium Y-up pad frame."""
        hw, hh = sx / 2, sy / 2
        c = min(sx, sy) * ratio
        r = min(sx, sy) * rr
        # corners in Altium frame: top_left (-hw, +hh), top_right (+hw,+hh), bottom_right(+hw,-hh), bottom_left(-hw,-hh)
        corners = [("top_left", (-hw, hh), (1, -1)), ("top_right", (hw, hh), (-1, -1)),
                   ("bottom_right", (hw, -hh), (-1, 1)), ("bottom_left", (-hw, -hh), (1, 1))]
        pts = []
        order = [corners[0], corners[1], corners[2], corners[3]]
        for name, (x, y), (sxn, syn) in order:
            if name in chamfer:
                # two points along the edges
                if name in ("top_left", "bottom_right"):
                    pts.append((x, y + syn * c))
                    pts.append((x + sxn * c, y))
                else:
                    pts.append((x + sxn * c, y))
                    pts.append((x, y + syn * c))
            elif r > 0:
                cx, cy = x + sxn * r, y + syn * r
                base = math.degrees(math.atan2(-syn, -sxn))
                for k in range(7):
                    a = math.radians(base - 45 + 90 * k / 6)
                    pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
            else:
                pts.append((x, y))
        return pts

    def _emit_pad(self, ir: IRWriter, fp: Footprint, pad: Pad, A, for_board: bool):
        lib_layers = self._lib_layers(fp, pad.layers)
        cu = [l for l in lib_layers if l.endswith(".Cu")]
        lrot = self._pad_lib_rot(fp, pad) % 360
        L = self._libpt_fn(fp)
        x, y = A(pad.local_pos)
        if pad.type in ("thru_hole", "np_thru_hole"):
            layer = "MULTI"
        elif "F.Cu" in cu:
            layer = "T"
        elif "B.Cu" in cu:
            layer = "B"
        elif cu:
            layer = self.lm.get(cu[0]) or "T"
        else:
            # mask/paste-only "pad" (aperture): emit as region on those layers
            self._emit_aperture_pad(ir, fp, pad, A)
            return
        base = (pad.shape, pad.size, pad.rr_ratio, pad.chamfer, pad.chamfer_ratio, pad.delta)
        shp, sx, sy, cr, poly = self._pad_geom(*base)
        custom_regions = []
        if pad.shape == "custom":
            anchor = pad.anchor or "circle"
            shp = "R" if anchor == "circle" else "S"
            sx, sy = pad.size
            custom_regions = self._custom_pad_polys(pad)
        elif poly is not None:
            custom_regions = [poly]
        kw = dict(x=x, y=y, rot=float(lrot), name=pad.number, layer=layer, shape=shp,
                  sx=mm(sx), sy=mm(sy), cr=float(cr))
        # padstack variants
        if pad.layer_shapes:
            front = pad.layer_shapes.get("F.Cu")
            inner = pad.layer_shapes.get("Inner")
            back = pad.layer_shapes.get("B.Cu")
            if fp.flipped:
                front, back = back, front
            def g(ls):
                if ls is None:
                    return shp, sx, sy, cr
                s2, x2, y2, c2, p2 = self._pad_geom(ls.shape, ls.size, ls.rr_ratio, ls.chamfer, ls.chamfer_ratio,
                                                    ls.delta)
                return s2, x2, y2, c2
            ts, tx_, ty_, tc = g(front) if front else (shp, sx, sy, cr)
            ms, mx_, my_, mc = g(inner) if inner else (shp, sx, sy, cr)
            bs, bx_, by_, bc = g(back) if back else (shp, sx, sy, cr)
            kw.update(mode="stack", shape=ts, sx=mm(tx_), sy=mm(ty_), cr=float(tc),
                      mshape=ms, msx=mm(mx_), msy=mm(my_), mcr=float(mc),
                      bshape=bs, bsx=mm(bx_), bsy=mm(by_), bcr=float(bc))
            if pad.padstack_mode == "custom":
                self.issues.info("pcb", "Pads with per-layer custom padstacks were mapped to "
                                        "Top/Middle/Bottom stacks")
        if pad.drill is not None and pad.type in ("thru_hole", "np_thru_hole"):
            d = pad.drill
            if d.shape == "oval" and abs(d.w - d.h) > 1e-6:
                kw.update(hole=mm(min(d.w, d.h)), slot=mm(max(d.w, d.h)), holerot=0.0 if d.w >= d.h else 90.0)
            else:
                kw.update(hole=mm(d.w))
            kw["plated"] = pad.type == "thru_hole"
            if pad.type == "np_thru_hole" and not cu:
                kw.update(sx=mm(min(d.w, d.h) if d.shape == "oval" else d.w),
                          sy=mm(min(d.w, d.h) if d.shape == "oval" else d.w), shape="R")
                if d.shape == "oval" and abs(d.w - d.h) > 1e-6:
                    kw.update(sx=mm(d.w), sy=mm(d.h))
        if pad.offset != (0.0, 0.0):
            o = L(pad.offset)
            kw.update(offx=mm(o[0]), offy=mm(o[1]))
        # solder mask
        has_tmask = "F.Mask" in lib_layers
        has_bmask = "B.Mask" in lib_layers
        mm_marg = pad.mask_margin if pad.mask_margin is not None else fp.mask_margin
        if layer == "T":
            if not has_tmask:
                kw["tentT"] = 1
        elif layer == "B":
            if not has_bmask:
                kw["tentB"] = 1
        else:
            if not has_tmask:
                kw["tentT"] = 1
            if not has_bmask:
                kw["tentB"] = 1
        if mm_marg is not None:
            kw["maskexp"] = mm(mm_marg)
        # paste
        pm = pad.paste_margin if pad.paste_margin is not None else fp.paste_margin
        pr = pad.paste_ratio if pad.paste_ratio is not None else fp.paste_ratio
        has_paste = ("F.Paste" in lib_layers) or ("B.Paste" in lib_layers)
        if layer in ("T", "B") and not has_paste:
            kw["nopaste"] = 1
        elif pm is not None or pr is not None:
            dm = self.b.setup.get("pad_to_paste_clearance", 0.0) if pm is None else pm
            dr = self.b.setup.get("pad_to_paste_clearance_ratio", 0.0) if pr is None else pr
            kw["pasteexp"] = mm(dm + dr * min(sx, sy) / 2.0)
        if for_board:
            kw["net"] = self.net_id(pad.net)
        ir.cmd("PAD", **kw)
        # Altium draws no paste for a through-hole pad; KiCad does when the pad lists F.Paste / B.Paste
        if layer == "MULTI" and pad.type == "thru_hole" and not custom_regions and has_paste:
            grow = 0.0
            if pm is not None or pr is not None:
                grow = (self.b.setup.get("pad_to_paste_clearance", 0.0) if pm is None else pm) + \
                       (self.b.setup.get("pad_to_paste_clearance_ratio", 0.0) if pr is None else pr) * min(sx, sy) / 2.0
            pts = self._pad_outline(fp, pad, A, grow)
            for pl, code in (("F.Paste", "TP"), ("B.Paste", "BP")):
                if pl in lib_layers:
                    self._emit_region_int(ir, code, [pts])
        # custom shape copper/mask/paste regions in the library frame
        if custom_regions:
            for poly in custom_regions:
                pts = []
                for px, py in poly:
                    # poly is in pad-local Altium frame; rotate by lib rotation and translate
                    qx, qy = rot(px, -py, -lrot)  # use KiCad rot helper in Y-up: convert
                    qx, qy = qx, -qy
                    pts.append((x + mm(qx), y + mm(qy)))
                cu_codes = [layer] if layer in ("T", "B") else [self.lm.get(c) for c in cu]
                for c in cu_codes:
                    if c:
                        self._emit_region_int(ir, c, [pts], net=kw.get("net", 0))
                if has_tmask and layer != "B":
                    self._emit_region_int(ir, "TS", [pts])
                if has_bmask and layer != "T":
                    self._emit_region_int(ir, "BS", [pts])
                if "F.Paste" in lib_layers:
                    self._emit_region_int(ir, "TP", [pts])
                if "B.Paste" in lib_layers:
                    self._emit_region_int(ir, "BP", [pts])

    def _custom_pad_polys(self, pad: Pad) -> list:
        """Custom pad primitives -> list of polygons (pad-local Altium frame, mm)."""
        polys = []
        for pr in pad.primitives:
            w = pr.width
            if pr.kind == "poly" and len(pr.pts) >= 3:
                pts = [(p[0], -p[1]) for p in pr.pts]
                polys.append(pts)
                if w > 0:
                    for i in range(len(pts)):
                        polys.append(self._stroke_poly(pts[i], pts[(i + 1) % len(pts)], w))
            elif pr.kind == "circle":
                c, e = pr.pts
                r = dist(c, e)
                if pr.fill or w <= 0:
                    rr = r + w / 2
                    polys.append([(c[0] + rr * math.cos(2 * math.pi * k / 48), -(c[1]) + rr * math.sin(2 * math.pi * k / 48))
                                  for k in range(48)])
                else:
                    ring = arc_points((c[0], -c[1]), r, 0, 360, 0.005)
                    for i in range(len(ring) - 1):
                        polys.append(self._stroke_poly(ring[i], ring[i + 1], w))
            elif pr.kind == "rect":
                (x1, y1), (x2, y2) = pr.pts
                polys.append([(x1, -y1), (x2, -y1), (x2, -y2), (x1, -y2)])
            elif pr.kind == "line":
                a, b2 = pr.pts
                polys.append(self._stroke_poly((a[0], -a[1]), (b2[0], -b2[1]), w))
            elif pr.kind == "arc":
                s, m, e = pr.pts
                ar = arc_from_3pts_yup((s[0], -s[1]), (m[0], -m[1]), (e[0], -e[1]))
                if ar:
                    (cx, cy), r, a1, a2 = ar
                    pts = arc_points((cx, cy), r, a1, a2, 0.005)
                    for i in range(len(pts) - 1):
                        polys.append(self._stroke_poly(pts[i], pts[i + 1], w))
            elif pr.kind == "bezier" and len(pr.pts) == 4:
                bp = bezier_points(*[(p[0], -p[1]) for p in pr.pts])
                for i in range(len(bp) - 1):
                    polys.append(self._stroke_poly(bp[i], bp[i + 1], w))
        return polys

    @staticmethod
    def _stroke_poly(a, b, w, n=8):
        """Stadium (rounded segment) polygon of width w from a to b."""
        dx, dy = b[0] - a[0], b[1] - a[1]
        L_ = math.hypot(dx, dy)
        r = w / 2
        if L_ < 1e-9:
            return [(a[0] + r * math.cos(2 * math.pi * k / 16), a[1] + r * math.sin(2 * math.pi * k / 16)) for k in range(16)]
        ang = math.atan2(dy, dx)
        pts = []
        for k in range(n + 1):
            t = ang - math.pi / 2 + math.pi * k / n
            pts.append((b[0] + r * math.cos(t), b[1] + r * math.sin(t)))
        for k in range(n + 1):
            t = ang + math.pi / 2 + math.pi * k / n
            pts.append((a[0] + r * math.cos(t), a[1] + r * math.sin(t)))
        return pts

    def _emit_aperture_pad(self, ir, fp, pad, A):
        """A KiCad pad without copper (e.g. paste-only aperture) -> regions."""
        lib_layers = self._lib_layers(fp, pad.layers)
        pts = self._pad_outline(fp, pad, A)
        for l in lib_layers:
            code = self.lm.get(l)
            if code and not self.lm.is_copper_code(code):
                self._emit_region_int(ir, code, [pts])

    def _pad_outline(self, fp, pad, A, grow: float = 0.0) -> list:
        """The pad's shape as one polygon in the library frame (Altium units), grown by `grow` mm."""
        size = (max(pad.size[0] + 2 * grow, 0.01), max(pad.size[1] + 2 * grow, 0.01))
        shp, sx, sy, cr, poly = self._pad_geom(pad.shape, size, pad.rr_ratio, pad.chamfer,
                                                pad.chamfer_ratio, pad.delta)
        if poly is None:
            if shp == "R":
                if abs(sx - sy) < 1e-9:
                    poly = [((sx / 2) * math.cos(2 * math.pi * k / 48), (sx / 2) * math.sin(2 * math.pi * k / 48))
                            for k in range(48)]
                else:
                    poly = self._stroke_poly((-(sx - sy) / 2, 0) if sx > sy else (0, -(sy - sx) / 2),
                                             ((sx - sy) / 2, 0) if sx > sy else (0, (sy - sx) / 2), min(sx, sy), 12)
            elif shp == "RR" and cr > 0:
                r = min(sx, sy) * cr / 200.0
                poly = []
                for cx, cy, a0 in ((sx / 2 - r, sy / 2 - r, 0), (-sx / 2 + r, sy / 2 - r, 90),
                                   (-sx / 2 + r, -sy / 2 + r, 180), (sx / 2 - r, -sy / 2 + r, 270)):
                    for k in range(7):
                        t = math.radians(a0 + 15 * k)
                        poly.append((cx + r * math.cos(t), cy + r * math.sin(t)))
            else:
                poly = [(-sx / 2, -sy / 2), (sx / 2, -sy / 2), (sx / 2, sy / 2), (-sx / 2, sy / 2)]
        x, y = A(pad.local_pos)
        lrot = self._pad_lib_rot(fp, pad)
        pts = []
        for px, py in poly:
            a = math.radians(lrot)
            qx = px * math.cos(a) - py * math.sin(a)
            qy = px * math.sin(a) + py * math.cos(a)
            pts.append((x + mm(qx), y + mm(qy)))
        return pts

    # ------------------------------------------------------------ shapes
    def _emit_shape(self, ir: IRWriter, sh: Shape, fp: Footprint | None = None, A=None, for_board=True,
                    board_level=False):
        if fp is not None:
            layer = self._lib_layer(fp, sh.layer)
            pts_local = sh.local_pts or []
            L = self._libpt_fn(fp)
            P = lambda p: A(p)
            pts = pts_local
            # arcs inside polygons: use absolute data converted back to local
            arcs = None
            if sh.arcs:
                arcs = [("xy", *self._fp_local_from_abs(fp, (it[1], it[2]))) if it[0] == "xy" else
                        ("arc", self._fp_local_from_abs(fp, it[1]), self._fp_local_from_abs(fp, it[2]),
                         self._fp_local_from_abs(fp, it[3])) for it in sh.arcs]
        else:
            layer = sh.layer
            P = self.P
            pts = sh.pts
            arcs = sh.arcs
        code = self.lm.get(layer)
        if code is None:
            return
        net = self.net_id(sh.net) if (for_board and self.lm.is_copper_code(code)) else 0
        w = mm(sh.width) if sh.width else 0
        if sh.fill and sh.kind in ("rect", "poly") and self.lm.is_copper_code(code) and 0 < (sh.width or 0) < \
                self._min_track_width():
            # a filled copper shape's hairline stroke (logos: 0.01 mm) adds at most half its width around the
            # region; as tracks it fails Altium's width rule, which KiCad never applies to shapes (500+
            # violations on a test board).  The region alone stands for the shape.
            w = 0
            self.thin_stroke_shapes = getattr(self, "thin_stroke_shapes", 0) + 1
        if sh.stroke_style not in ("solid", "default", "") and w > 0:
            pass  # Altium has no dashed tracks; drawn solid
        if sh.kind == "line":
            a, b = P(pts[0]), P(pts[1])
            ir.cmd("TRACK", x1=a[0], y1=a[1], x2=b[0], y2=b[1], w=max(w, 1), l=code, net=net)
        elif sh.kind == "arc":
            ar = self._arc_alt(pts[0], pts[1], pts[2], frame=P)
            if ar:
                (cx, cy), r, a1, a2 = ar
                ir.cmd("ARC", cx=cx, cy=cy, r=r, a1=a1, a2=a2, w=max(w, 1), l=code, net=net)
        elif sh.kind == "circle":
            c = P(pts[0])
            e = P(pts[1])
            r = math.hypot(e[0] - c[0], e[1] - c[1])
            if sh.fill:
                outer = r + w / 2
                ir.cmd("ARC", cx=c[0], cy=c[1], r=int(round(outer / 2)), a1=0.0, a2=360.0, w=int(round(outer)),
                       l=code, net=net)
            else:
                ir.cmd("ARC", cx=c[0], cy=c[1], r=int(round(r)), a1=0.0, a2=360.0, w=max(w, 1), l=code, net=net)
        elif sh.kind == "rect":
            (x1, y1), (x2, y2) = pts
            corners = [P((x1, y1)), P((x2, y1)), P((x2, y2)), P((x1, y2))]
            if sh.fill:
                self._emit_region_int(ir, code, [corners], net=net)
            if w > 0 or not sh.fill:
                for i in range(4):
                    a, b = corners[i], corners[(i + 1) % 4]
                    ir.cmd("TRACK", x1=a[0], y1=a[1], x2=b[0], y2=b[1], w=max(w, 1), l=code, net=net)
        elif sh.kind == "poly":
            if arcs:
                ring = []
                for it in arcs:
                    if it[0] == "xy":
                        ring.append(P((it[1], it[2])))
                    else:
                        sa, ma, ea = it[1], it[2], it[3]
                        ar = self._arc_alt(sa, ma, ea, frame=P)
                        if ar:
                            (cx, cy), r, a1, a2 = ar
                            seg = arc_points((cx, cy), r, a1, a2, mm(0.005))
                            # orient from start
                            s_int = P(sa)
                            if math.hypot(seg[0][0] - s_int[0], seg[0][1] - s_int[1]) > math.hypot(seg[-1][0] - s_int[0], seg[-1][1] - s_int[1]):
                                seg = seg[::-1]
                            ring.extend((int(round(x)), int(round(y))) for x, y in seg)
                        else:
                            ring.append(P(sa))
            else:
                ring = [P(p) for p in pts]
            if len(ring) < 2:
                return
            if sh.fill and len(ring) >= 3:
                self._emit_region_int(ir, code, [ring], net=net)
            if w > 0 or not sh.fill:
                for i in range(len(ring)):
                    a, b = ring[i], ring[(i + 1) % len(ring)]
                    if a != b:
                        ir.cmd("TRACK", x1=a[0], y1=a[1], x2=b[0], y2=b[1], w=max(w, 1), l=code, net=net)
        elif sh.kind == "bezier":
            if len(pts) == 4:
                bp = bezier_points(*pts)
                for i in range(len(bp) - 1):
                    a, b = P(bp[i]), P(bp[i + 1])
                    ir.cmd("TRACK", x1=a[0], y1=a[1], x2=b[0], y2=b[1], w=max(w, 1), l=code, net=net)

    def _min_track_width(self) -> float:
        r = (getattr(self.project, "rules", None) or {}) if self.project is not None else {}
        try:
            return max(float(r.get("min_track_width") or 0.0), 0.05)
        except (TypeError, ValueError):
            return 0.05

    # ------------------------------------------------------------ regions
    def _emit_region(self, ir, code, contours_kicad, **kw):
        cs = [[self.P(p) for p in c] for c in contours_kicad]
        self._emit_region_int(ir, code, cs, **kw)

    def _emit_region_int(self, ir, code, contours, net=0, kind="copper", keepout=None, name=None):
        if not contours or len(contours[0]) < 3:
            return
        ir.cmd("REGION", l=code, net=net or 0, kind=kind, ko=keepout, name=name)
        for ci, c in enumerate(contours):
            if ci > 0:
                ir.cmd("HOLE")
            for x, y in c:
                ir.lines.append(f"P\t{int(round(x))}\t{int(round(y))}")
        ir.cmd("REGIONEND")

    # ------------------------------------------------------------ text
    def _text_cmd(self, ir, cmd, t: Text, pos, angle, layer, text, pos_int=None, mirror_override=None,
                  absmap=None, angmap=None, shown=None, **extra):
        e = t.effects
        h = e.size[0] if e.size else 1.0
        wdt = e.size[1] if e.size else h
        th = e.thickness if e.thickness else (h * (0.2 if e.bold else 0.15))
        x, y = pos_int if pos_int is not None else self.P(pos)
        mirror = e.mirror if mirror_override is None else mirror_override
        if e.face and absmap is not False and self._ttf_text_cmd(ir, cmd, t, layer, text, mirror, th, wdt, absmap or self.P,
                                                                 angmap or (lambda a: a), extra, shown):
            return
        hs = h * TTF_SCALE if e.face else h
        hj, vj = e.hjustify, e.vjustify
        ang = angle % 360
        if t.keep_upright and cmd != "TEXT_LIB":
            # KiCad draws "keep upright" texts readable: rotation folded into (-90, 90]
            a = ang if ang <= 180 else ang - 360
            if a > 90 or a <= -90:
                ang = (ang + 180) % 360
                hj = {"left": "right", "right": "left"}.get(hj, hj)
                vj = {"top": "bottom", "bottom": "top"}.get(vj, vj)
        lines = text.split("\n") if text else [""]
        kw = dict(x=x, y=y, rot=float(ang), h=mm(hs), cw=mm(wdt), w=mm(th), l=layer, mirror=mirror,
                  hj=hj[0], vj=vj[0], font=e.face, bold=e.bold, italic=e.italic, ko=t.knockout or None)
        kw.update(extra)
        if len(lines) == 1:
            ir.cmd(cmd, text=text, **kw)
            return
        # multi-line: one text per line, KiCad interline = 1.62 * size * line_spacing
        pitch = mm(h * 1.62 * (e.line_spacing or 1.0))
        n = len(lines)
        if vj[:1] == "t":
            first_off = 0
        elif vj[:1] == "b":
            first_off = (n - 1) * pitch
        else:
            first_off = (n - 1) * pitch / 2
        a = math.radians(ang)
        for i, ln in enumerate(lines):
            off = first_off - i * pitch      # along the text's "up" direction
            dx = -off * math.sin(a) * -1
            dy = off * math.cos(a)
            kw2 = dict(kw)
            kw2.update(x=int(round(x - off * math.sin(a))), y=int(round(y + off * math.cos(a))))
            if cmd == "COMPTEXT" and i > 0:
                ir.cmd("TEXT", text=ln, **{k: v for k, v in kw2.items() if k not in ("which", "show")})
            else:
                ir.cmd(cmd, text=ln, **kw2)

    def _ttf_text_cmd(self, ir, cmd, t: Text, layer, text, mirror, th, wdt, absmap, angmap, extra,
                      shown=None) -> bool:
        """Place a TrueType text where KiCad drew it, from KiCad's own rendering (render_cache).

        Altium lays a TrueType text out from its location: the baseline passes through YLocation and the
        ink starts at XLocation (ends there when mirrored); KiCad's outline font is TTF_SCALE times larger
        for the same size. KiCad's justification box differs from Altium's, so the text is placed by its
        ink: the left (right when mirrored) ink edge and KiCad's baseline, both read from render_cache."""
        rc = t.render
        if not rc or t.knockout:
            return False
        rtext, ra, polys = rc
        lines = (text or "").split("\n")
        while len(lines) > 1 and lines[-1] == "":
            lines.pop()
        slines = (shown if shown is not None else text or "").split("\n")
        while len(slines) > 1 and slines[-1] == "":
            slines.pop()
        rlines = (rtext or "").split("\n")
        while len(rlines) > 1 and rlines[-1] == "":
            rlines.pop()
        # the text Altium shows (special strings resolved) must be the one KiCad rendered
        if len(lines) != 1 or len(rlines) != 1 or slines[:1] != rlines or not rlines[0].strip():
            return False
        e = t.effects
        h = e.size[0] if e.size else 1.0
        vj = e.vjustify
        if abs(((t.angle - ra) % 360) - 180) < 1:          # KiCad turned it upright: justification flips
            vj = {"top": "bottom", "bottom": "top"}.get(vj, vj)
        # KiCad outline-font layout (single line), y down from the anchor to the baseline
        bl = {"bottom": -0.17, "top": 1.0}.get(vj, 0.415) * h
        a = math.radians(ra)
        ca, sa = math.cos(a), math.sin(a)
        ax, ay = t.pos
        base_abs = (ax + bl * sa, ay + bl * ca)
        theta = angmap(ra) % 360
        r = math.radians(theta)
        d = (math.cos(r), math.sin(r))
        n = (-math.sin(r), math.cos(r))
        s_vals = []
        for ring in polys:
            for p in ring:
                q = absmap(p)
                s_vals.append(q[0] * d[0] + q[1] * d[1])
        if not s_vals:
            return False
        b = absmap(base_abs)
        tb = b[0] * n[0] + b[1] * n[1]
        sx = max(s_vals) if mirror else min(s_vals)
        x = sx * d[0] + tb * n[0]
        y = sx * d[1] + tb * n[1]
        kw = dict(x=int(round(x)), y=int(round(y)), rot=float(theta), h=mm(h * TTF_SCALE), cw=mm(wdt), w=mm(th),
                  l=layer, mirror=mirror, hj="l", vj="b", font=e.face, bold=e.bold, italic=e.italic, tt=1)
        kw.update(extra)
        ir.cmd(cmd, text=lines[0], **kw)
        return True

    def _emit_free_text(self, ir, t: Text):
        code = self.lm.get(t.layer)
        if code is None or t.hidden:
            return
        if t.kind == "textbox":
            self._emit_textbox(ir, t, layer=code, text=self.subst(t.text))
            return
        self._text_cmd(ir, "TEXT", t, t.pos, t.angle, code, self.subst(t.text))

    def _emit_textbox(self, ir, t: Text, fp=None, A=None, layer=None, text=""):
        P = (lambda p: A(self._fp_local_from_abs(fp, p))) if fp is not None else self.P
        box = [P(p) for p in t.box or []]
        if len(box) < 4:
            return
        if t.border is not None and (t.border.width or 0) > 0:
            w = mm(t.border.width)
            for i in range(len(box)):
                a, b = box[i], box[(i + 1) % len(box)]
                ir.cmd("TRACK", x1=a[0], y1=a[1], x2=b[0], y2=b[1], w=w, l=layer, net=0)
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        e = t.effects
        h = e.size[0]
        tx = Text(text=text, pos=(0, 0), angle=t.angle, layer=t.layer, effects=e)
        e2 = type(e)(**{**e.__dict__})
        if t.margins is not None and abs(t.angle % 180.0) < 1e-6:
            # KiCad lays the text out inside the margins by its own justification (table cells: left /
            # vertically centred); Altium's stroke text justification does the same from that anchor
            ml, mt, mr, mb = (mm(v) for v in t.margins)
            left, right, bottom, top = min(xs) + ml, max(xs) - mr, min(ys) + mb, max(ys) - mt
            if t.angle % 360.0 > 90.0:          # 180 deg: KiCad's left/top are on the other side
                left, right, bottom, top = right, left, top, bottom
            hj = e.hjustify if e.hjustify in ("left", "right") else "center"
            vj = e.vjustify if e.vjustify in ("top", "bottom") else "center"
            x = {"left": left, "right": right}.get(hj, (left + right) / 2)
            y = {"top": top, "bottom": bottom}.get(vj, (top + bottom) / 2)
            e2.hjustify, e2.vjustify = hj, vj
            tx.effects = e2
            self._text_cmd(ir, "TEXT", tx, None, t.angle, layer, text, pos_int=(int(round(x)), int(round(y))))
            return
        # place text at the top-left inside margin
        inset = mm(max(h * 0.4, 0.2))
        e2.vjustify = "top"
        e2.hjustify = "left"
        tx.effects = e2
        self._text_cmd(ir, "TEXT", tx, None, t.angle, layer, text, pos_int=(min(xs) + inset, max(ys) - inset))

    def _emit_table(self, ir, node):
        """KiCad table (board characteristics, stackup, ...): cell texts at their KiCad place and the grid
        lines KiCad draws, on the table's layer."""
        cells, lines = parse_table(node)
        code = self.lm.get(cells[0].layer) if cells else None
        if code is None:
            return
        for a, b2, w in lines:
            (x1, y1), (x2, y2) = self.P(a), self.P(b2)
            ir.cmd("TRACK", x1=x1, y1=y1, x2=x2, y2=y2, w=mm(w), l=code, net=0)
        for c in cells:
            if c.text.strip() and c.name.split(",")[0] != "0":
                self._emit_textbox(ir, c, layer=code, text=self.subst(c.text))

    # ------------------------------------------------------------ 3D
    def _emit_placeholder(self, ir, fp: Footprint, origin_mm):
        """Grey extruded box from the courtyard (or pads) for a part without any 3D model."""
        L = self._libpt_fn(fp)
        pts = []
        for sh in fp.shapes:
            if self._lib_layer(fp, sh.layer) == "F.CrtYd" and sh.local_pts:
                pts.extend(L(p) for p in sh.local_pts)
        if not pts:
            for pd in fp.pads:
                lx, ly = L(pd.local_pos)
                r = max(pd.size) / 2
                pts += [(lx - r, ly - r), (lx + r, ly + r)]
        if not pts:
            return
        x1, y1 = min(p[0] for p in pts), min(p[1] for p in pts)
        x2, y2 = max(p[0] for p in pts), max(p[1] for p in pts)
        h = 1.0
        for src in [m.path for m in fp.models] + [fp.lib_name]:
            mt = re.search(r"[_-]H(\d+(?:\.\d+)?)", src)
            if mt:
                h = float(mt.group(1))
                break
        ox, oy = origin_mm
        ir.cmd("XBODY", x1=mm(ox + x1), y1=mm(oy + y1), x2=mm(ox + x2), y2=mm(oy + y2), h=mm(h),
               l=f"MECH{BODY_TOP}")
        self.stats["placeholders"] = self.stats.get("placeholders", 0) + 1
        self.issues.info("3d", f"{fp.ref}: no 3D model available - placeholder box {h:g} mm high created")

    def _emit_body(self, ir, fp: Footprint, m, origin_mm) -> int:
        if not m.show:
            return 0
        ref = self.res.resolve(m.path, [fp.embedded, self.b.embedded])
        if ref.step is None:
            self.stats["bodies_missing"] += 1
            self.missing_models.setdefault(m.path, []).append(fp.ref)
            return 0
        if any(abs(s - 1.0) > 1e-6 for s in m.scale):
            self.issues.warn("3d", f"{fp.ref}: 3D model scale {m.scale} is not supported for STEP in Altium; "
                                   "model placed unscaled")
        ox, oy = origin_mm
        rx, ry, rz = (-m.rotate[0]) % 360, (-m.rotate[1]) % 360, (-m.rotate[2]) % 360
        ir.cmd("BODY", file=str(ref.step), x=mm(ox + m.offset[0]), y=mm(oy + m.offset[1]),
               dz=mm(m.offset[2]), rx=float(rx), ry=float(ry), rz=float(rz),
               l=f"MECH{BODY_TOP}", opacity=float(m.opacity))
        self.lm.used.add(f"MECH{BODY_TOP}")
        self.stats["bodies"] += 1
        return 1

    # ------------------------------------------------------------ tracks/vias
    def _emit_track(self, ir, t):
        code = self.lm.get(t.layer)
        if code is None:
            return
        a, b = self.P(t.start), self.P(t.end)
        ir.cmd("TRACK", x1=a[0], y1=a[1], x2=b[0], y2=b[1], w=mm(t.width), l=code, net=self.net_id(t.net),
               locked=t.locked or None)

    def _emit_track_arc(self, ir, t):
        code = self.lm.get(t.layer)
        if code is None:
            return
        ar = self._arc_alt(t.start, t.mid, t.end)
        if ar is None:
            a, b = self.P(t.start), self.P(t.end)
            ir.cmd("TRACK", x1=a[0], y1=a[1], x2=b[0], y2=b[1], w=mm(t.width), l=code, net=self.net_id(t.net))
            return
        (cx, cy), r, a1, a2 = ar
        ir.cmd("ARC", cx=cx, cy=cy, r=r, a1=a1, a2=a2, w=mm(t.width), l=code, net=self.net_id(t.net),
               locked=t.locked or None)

    def _emit_via(self, ir, v):
        lo = self.lm.get(v.layers[0]) or "T"
        hi = self.lm.get(v.layers[1]) or "B"
        ten = v.tenting
        dflt = self.b.setup.get("tenting")
        tt = ten[0] if ten[0] is not None else (dflt[0] if dflt else None)
        tb = ten[1] if ten[1] is not None else (dflt[1] if dflt else None)
        kw = dict(x=self.X(v.pos[0]), y=self.Y(v.pos[1]), d=mm(v.size), hole=mm(v.drill), lo=lo, hi=hi,
                  net=self.net_id(v.net), tentT=tt, tentB=tb, locked=v.locked or None)
        if tt is not None or tb is not None:
            # Altium keeps tenting only on a manual mask; the open side uses KiCad's board mask margin
            kw["mask"] = mm(float(self.b.setup.get("pad_to_mask_clearance", 0.0) or 0.0))
        if v.layer_sizes:
            sizes = {}
            for ln, d in v.layer_sizes.items():
                if ln == "Inner":
                    sizes["I"] = d
                else:
                    c = self.lm.get(ln)
                    if c:
                        sizes[c] = d
            kw["stack"] = ";".join(f"{k}:{mm(d)}" for k, d in sizes.items())
        if v.backdrill:
            self.issues.warn("pcb", "Backdrilled vias were imported as regular vias (set back-drill in Altium)")
        rm = self._via_unused_layers(v)
        if rm:
            # the builder's pad removal (SetState_IsPadRemoved / IsPadRemoved[L]) does not exist in AD24's
            # script API (every call failed); the layers are only reported until the right call
            # is known
            if VIA_RING_REMOVAL:
                kw["rm"] = ",".join(rm)
            self.vias_unused = getattr(self, "vias_unused", 0) + 1
        ir.cmd("VIA", **kw)

    def _via_unused_layers(self, v) -> list:
        """Altium layer codes where KiCad draws no annular ring for this via: 'remove_unused_layers' drops
        the ring on every layer with nothing connected (no track or arc of its net ending on it, no zone in
        zone_layer_connections; the outer layers stay with 'keep_end_layers').  On a test board, inner tracks run
        0.1 mm past the BGA fanout vias, where KiCad has the bare hole; full rings gave 118 Altium
        clearance violations."""
        if not v.remove_unused_layers:
            return []
        cu = [l.name for l in self.b.copper_layers]
        try:
            i0, i1 = sorted((cu.index(v.layers[0]), cu.index(v.layers[1])))
        except ValueError:
            return []
        span = cu[i0:i1 + 1]
        keep = set(v.zone_layers)
        if v.keep_end_layers:
            keep.update((span[0], span[-1]))
        idx = getattr(self, "_end_index", None)
        if idx is None:
            # track / arc ends per (net, layer), on a 0.05 mm grid for the lookup
            idx = {}
            for t in list(self.b.tracks) + list(self.b.arcs):
                for p in (t.start, t.end):
                    idx.setdefault((t.net, t.layer, round(p[0] / 0.05), round(p[1] / 0.05)), []).append((p, t.width))
            self._end_index = idx
        r = v.size / 2.0
        gx, gy = round(v.pos[0] / 0.05), round(v.pos[1] / 0.05)
        reach = int(r / 0.05) + 2
        for lay in span:
            if lay in keep:
                continue
            hit = False
            for dx in range(-reach, reach + 1):
                for dy in range(-reach, reach + 1):
                    for p, w in idx.get((v.net, lay, gx + dx, gy + dy), ()):
                        if math.hypot(p[0] - v.pos[0], p[1] - v.pos[1]) <= r + w / 2.0:
                            hit = True
                            break
                    if hit:
                        break
                if hit:
                    break
            if hit:
                keep.add(lay)
        return [self.lm.get(l) for l in span if l not in keep and self.lm.get(l)]

    # ------------------------------------------------------------ zones
    def _zone_ring(self, items, P=None):
        P = P or self.P
        ring = []
        for it in items:
            if it[0] == "xy":
                ring.append(P((it[1], it[2])))
            else:
                ar = self._arc_alt(it[1], it[2], it[3], frame=P)
                if ar:
                    (cx, cy), r, a1, a2 = ar
                    seg = arc_points((cx, cy), r, a1, a2, mm(0.005))
                    s_int = P(it[1])
                    if math.hypot(seg[0][0] - s_int[0], seg[0][1] - s_int[1]) > math.hypot(seg[-1][0] - s_int[0], seg[-1][1] - s_int[1]):
                        seg = seg[::-1]
                    ring.extend((int(round(x)), int(round(y))) for x, y in seg)
        return ring

    def _emit_zones(self, ir):
        zones = list(self.b.zones)
        # pour order: KiCad higher priority fills first. Between zones of equal priority KiCad's
        # ZONE::HigherPriority falls back to the zone UUID (the larger one wins); that decides on
        # which side of two abutting zones the clearance gap lies
        copper = [z for z in zones if z.keepout is None and not z.is_teardrop and
                  any(l.endswith(".Cu") for l in z.layers)]
        copper.sort(key=lambda z: (z.priority, (z.uuid or "").lower()), reverse=True)
        used_names = set()
        order = 0
        for z in zones:
            if z.is_teardrop:
                if self.opt.teardrops == "skip":
                    continue
                for lay, polys in z.filled_polys.items():
                    code = self.lm.get(lay)
                    if code:
                        for poly in polys:
                            self._emit_region(ir, code, [poly], net=self.net_id(z.net))
                continue
            if z.keepout is not None:
                self._emit_keepout(ir, z)
                continue
            if not any(l.endswith(".Cu") for l in z.layers):
                # non-copper zone -> solid region on that layer
                for lay in z.layers:
                    code = self.lm.get(lay)
                    if code:
                        ring = self._zone_ring(z.outline_items)
                        holes = [self._zone_ring(h) for h in z.hole_items]
                        self._emit_region_int(ir, code, [ring] + holes)
                continue
        for z in copper:
            for lay in z.layers:
                if not lay.endswith(".Cu"):
                    continue
                code = self.lm.get(lay)
                if code is None:
                    continue
                order += 1
                base = z.name or (z.net and self.altium_net_name(z.net)) or "Polygon"
                name = f"{base}_{lay.replace('.', '')}"
                k = 2
                nm = name
                while nm in used_names:
                    nm = f"{name}_{k}"
                    k += 1
                used_names.add(nm)
                self.polygon_names.append((z, nm))
                self.polygon_layer[nm] = lay
                ring = self._zone_ring(z.outline_items)
                if len(ring) < 3:
                    continue
                hatch = "solid"
                if z.fill_mode == "hatch":
                    ang = z.hatch_orientation % 90
                    hatch = "45" if abs(ang - 45) < 1 else "90"
                islands = z.island_removal
                if islands != 1 and z.net and z.filled_polys.get(lay) and not self._zone_reached(z, lay):
                    # KiCad keeps the fill of a zone that nothing of its net reaches (an inner plane before
                    # any via); Altium's dead-copper removal would drop all of it
                    islands = 1
                    self.unreached_polygons.append(nm)
                ir.cmd("POLY", l=code, net=self.net_id(z.net), name=nm, order=order, hatch=hatch,
                       track=mm(z.hatch_thickness or z.min_thickness), grid=mm((z.hatch_gap + z.hatch_thickness) if z.fill_mode == "hatch" else z.min_thickness),
                       minw=mm(z.min_thickness), islands=islands,
                       islandarea=float(z.island_area_min), clearance=mm(z.clearance),
                       connect=z.connect_pads, tgap=mm(z.thermal_gap), twidth=mm(z.thermal_bridge_width),
                       locked=z.locked or None, pour=1 if z.filled else 0,
                       # KiCad's filler drops copper narrower than min_thickness: Altium's counterpart is
                       # neck removal at that width (the pipeline turns it off per polygon if it would
                       # disconnect pads that KiCad connects)
                       neck=1, neckw=mm(z.min_thickness))
                for x, y in ring:
                    ir.lines.append(f"P\t{x}\t{y}")
                ir.cmd("POLYEND")
                for h in z.hole_items:
                    hr = self._zone_ring(h)
                    if len(hr) >= 3:
                        self._emit_region_int(ir, code, [hr], kind="cutout")

    def _zone_reached(self, z: Zone, lay: str) -> bool:
        """Is there a pad, via or track of the zone's net on this layer inside the zone outline?"""
        poly = z.outline

        def inside(p):
            x, y = p
            c = False
            for i in range(len(poly)):
                (x1, y1), (x2, y2) = poly[i - 1], poly[i]
                if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                    c = not c
            return c

        order = list(self.lm.copper)
        li = order.index(lay) if lay in order else -1
        for fp in self.b.footprints:
            for pd in fp.pads:
                if pd.net == z.net and (lay in pd.layers or "*.Cu" in pd.layers or
                                        (pd.type in ("thru_hole",) and li > 0)) and inside(pd.pos):
                    return True
        for v in self.b.vias:
            if v.net == z.net and inside(v.pos):
                a, b = (order.index(v.layers[0]) if v.layers[0] in order else 0,
                        order.index(v.layers[1]) if v.layers[1] in order else len(order) - 1)
                if min(a, b) <= li <= max(a, b):
                    return True
        for t in list(self.b.tracks) + list(self.b.arcs):
            if t.net == z.net and t.layer == lay and (inside(t.start) or inside(t.end)):
                return True
        return False

    def _emit_keepout(self, ir, z: Zone, A=None, fp=None):
        ko = z.keepout or {}
        bits = 0
        if not ko.get("vias", True):
            bits |= 1
        if not ko.get("tracks", True):
            bits |= 2
        if not ko.get("copperpour", True):
            bits |= 4
        if not ko.get("pads", True):
            bits |= 8 | 16
        if not ko.get("footprints", True):
            self.issues.info("pcb", "KiCad 'no footprints' keepout areas have no Altium keepout equivalent "
                                    "(converted for the remaining restrictions)")
        if bits == 0:
            return
        if fp is not None:
            P = lambda p: A(self._fp_local_from_abs(fp, p))
        else:
            P = self.P
        ring = self._zone_ring(z.outline_items, P)
        holes = [self._zone_ring(h, P) for h in z.hole_items]
        cu_layers = [l for l in z.layers if l.endswith(".Cu")]
        all_cu = set(cu_layers) >= set(self.lm.copper)
        if all_cu:
            self._emit_region_int(ir, "KO", [ring] + holes, keepout=bits)
        else:
            for l in cu_layers:
                lay = self._lib_layer(fp, l) if fp is not None else l
                code = self.lm.get(lay)
                if code:
                    self._emit_region_int(ir, code, [ring] + holes, keepout=bits)

    def _emit_fp_zone(self, ir, fp, z: Zone, A):
        if z.keepout is not None:
            self._emit_keepout(ir, z, A=A, fp=fp)
        else:
            # copper / technical zones inside footprints: static regions from the zone outline
            P = lambda p: A(self._fp_local_from_abs(fp, p))
            for lay in z.layers:
                code = self.lm.get(self._lib_layer(fp, lay))
                if code:
                    ring = self._zone_ring(z.outline_items, P)
                    self._emit_region_int(ir, code, [ring])

    # ------------------------------------------------------------ dimensions
    def _emit_dimensions(self, ir):
        from .dimensions import emit_dimension
        for d in self.b.dimensions:
            try:
                emit_dimension(self, ir, d)
            except Exception as e:
                self.issues.warn("pcb", f"Dimension not converted: {e}")

    # ------------------------------------------------------------ classes / rules
    def _emit_classes_rules(self, ir):
        from .rules import emit_classes_and_rules
        emit_classes_and_rules(self, ir)

    # ------------------------------------------------------------ library
    def build_pcblib(self, ir: IRWriter, out_lib: Path):
        ir.comment("Convi PCB library")
        ir.cmd("NEWLIB", path=str(out_lib))
        for name, d in sorted(self.fpdefs.items()):
            fp = d.fp
            ir.cmd("FP", name=name, desc=fp.descr or fp.lib_id, h=0)
            self._emit_fp_prims(ir, fp, (0.0, 0.0), for_board=False)
            ir.cmd("FPEND")
        ir.cmd("SAVELIB")
