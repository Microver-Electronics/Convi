"""Altium sheet templates (.SchDot) for converted schematics.

Schematics can be drawn on customer templates kept in a templates folder (a folder named
'__Templates', or any '__<Name>Templates', or the folder in the CONVI_TEMPLATES environment
variable), one file per customer and sheet size ("A3 - template - Acme.SchDot",
"A2 - template - Acme.SchDot").
Each converted sheet gets the smallest template of the customer's family (A3 by default) that
holds its drawing inside the border and clear of the title block; a sheet that does not fit
moves up to A2, A1, A0.

Convi never edits a template: the ones used are copied into the output project, and the
schematic builder copies their graphics into each sheet as that sheet's template.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

TEMPLATES_DIR_NAME = "__Templates"
TEMPLATES_DIR_GLOB = "__*Templates"       # any '__<Name>Templates' folder
TEMPLATES_ENV = "CONVI_TEMPLATES"
BORDER = 200.0          # Altium sheet border (margin) in mil
TITLE_CLEARANCE = 50.0  # keep the drawing this far from the template's title block

# Altium SheetStyle index -> (width, height) in mil, landscape
SHEET_STYLES = {
    0: (11500, 7600), 1: (15500, 11000), 2: (22000, 15500), 3: (32000, 22000), 4: (44000, 32000),
    5: (9500, 7500), 6: (15000, 9500), 7: (20000, 15000), 8: (32000, 20000), 9: (42000, 32000),
    10: (11000, 8500), 11: (14000, 8500), 12: (17000, 11000),
    13: (9900, 7900), 14: (15400, 9900), 15: (20600, 15600), 16: (32600, 20600), 17: (42800, 32800),
}
SHEET_STYLE_NAMES = {0: "A4", 1: "A3", 2: "A2", 3: "A1", 4: "A0", 5: "A", 6: "B", 7: "C", 8: "D", 9: "E",
                     10: "Letter", 11: "Legal", 12: "Tabloid"}

# "A3 - template - Acme", "A3- template - Example Corp", "A3 - template- Foo"
_NAME_RE = re.compile(r"^(A[0-4])\s*-\s*template\s*-\s*(.+?)\s*$", re.IGNORECASE)


@dataclass
class Template:
    path: Path
    size_name: str                  # A3 ...
    customer: str
    width: int = 0                  # sheet size in mil
    height: int = 0
    title_block: tuple | None = None  # (x1, y1, x2, y2) mil: bounding box of the template graphics
    param_refs: dict = field(default_factory=dict)   # template parameter -> parameter it shows ('=X')

    @property
    def area(self) -> int:
        return self.width * self.height


def find_templates_dir(*starts: Path) -> Path | None:
    """The templates folder: the CONVI_TEMPLATES environment variable, else the first '__Templates' (or
    '__<Name>Templates') folder next to one of the given paths or one of their parents."""
    env = os.environ.get(TEMPLATES_ENV, "").strip()
    if env and Path(env).is_dir():
        return Path(env)
    for start in starts:
        if not start:
            continue
        p = Path(start).resolve()
        for d in [p, *p.parents][:8]:
            cand = d / TEMPLATES_DIR_NAME
            if cand.is_dir():
                return cand
            try:
                hits = sorted(x for x in d.glob(TEMPLATES_DIR_GLOB) if x.is_dir())
            except OSError:
                hits = []
            if hits:
                return hits[0]
    return None


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def parse_name(path: Path) -> tuple[str, str] | None:
    """('A3', 'Acme') for 'A3 - template - Acme.SchDot'; None for other names."""
    if path.suffix.lower() != ".schdot":
        return None
    mt = _NAME_RE.match(path.stem)
    return (mt.group(1).upper(), mt.group(2)) if mt else None


def list_templates(folder: Path | None) -> list[Path]:
    """Customer templates (named '<size> - template - <customer>.SchDot') in the folder."""
    if not folder or not Path(folder).is_dir():
        return []
    return sorted((p for p in Path(folder).glob("*.SchDot") if parse_name(p)), key=lambda p: p.name.lower())


def customers(folder: Path | None) -> list[str]:
    seen = {}
    for p in list_templates(folder):
        c = parse_name(p)[1]
        seen.setdefault(_key(c), c)
    return sorted(seen.values(), key=str.lower)


def auto_customer(project_name: str, folder: Path | None) -> str | None:
    """The customer whose name appears in the project name (HW_ACME_WIDGET_R3 -> Acme);
    the longest one wins when several do."""
    pk = _key(project_name)
    hits = [c for c in customers(folder) if _key(c) and _key(c) in pk]
    return max(hits, key=lambda c: len(_key(c))) if hits else None


def family(folder: Path | None, customer: str) -> list[Template]:
    """All sizes of one customer's template, smallest sheet first."""
    out = []
    for p in list_templates(folder):
        size_name, cust = parse_name(p)
        if _key(cust) != _key(customer):
            continue
        t = read_template(p, size_name, cust)
        if t is not None:
            out.append(t)
    return sorted(out, key=lambda t: t.area)


def _header(data: bytes) -> dict:
    i = data.find(b"|RECORD=31|")
    if i < 0:
        return {}
    end = data.find(b"\x00", i)
    txt = data[i:end if end > 0 else i + 4000].decode("latin1")
    return dict(kv.split("=", 1) for kv in txt.split("|") if "=" in kv)


def _coord(rec: dict, key: str) -> float | None:
    """Altium schematic coordinate in mil: 'X' in 10 mil units plus 'X_Frac' (1/100000 unit)."""
    if key not in rec and key + "_Frac" not in rec:
        return None
    try:
        return (int(rec.get(key, "0")) + int(rec.get(key + "_Frac", "0")) / 100000.0) * 10.0
    except ValueError:
        return None


def read_template(path: Path, size_name: str = "", customer: str = "") -> Template | None:
    """Sheet size and title block box of a .SchDot, read from its records."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    hdr = _header(data)
    if not hdr:
        return None
    portrait = hdr.get("WorkspaceOrientation", "0") == "1"
    if hdr.get("UseCustomSheet", "F") == "T":
        try:
            w, h = int(hdr.get("CustomX", "0")) * 10, int(hdr.get("CustomY", "0")) * 10
        except ValueError:
            return None
    else:
        style = int(hdr.get("SheetStyle", "0") or 0)
        if style not in SHEET_STYLES:
            return None
        w, h = SHEET_STYLES[style]
        size_name = size_name or SHEET_STYLE_NAMES.get(style, f"style {style}")
    if portrait:
        w, h = h, w
    # the template graphics are the title block (Altium draws the border itself)
    xs, ys = [], []
    for mt in re.finditer(rb"\|RECORD=(\d+)\|[^\x00]*", data):
        if mt.group(1) in (b"31", b"41"):          # sheet header, parameters
            continue
        rec = dict(kv.split("=", 1) for kv in mt.group(0).decode("latin1").split("|") if "=" in kv)
        if rec.get("IsHidden") == "T":
            continue
        for kx, ky in [("Location.X", "Location.Y"), ("Corner.X", "Corner.Y")] + \
                      [(f"X{i}", f"Y{i}") for i in range(1, 50)]:
            x, y = _coord(rec, kx), _coord(rec, ky)
            if x is None and y is None:
                continue
            xs.append(x or 0.0)
            ys.append(y or 0.0)
    tb = (min(xs), min(ys), max(xs), max(ys)) if xs else None
    t = Template(Path(path), size_name, customer, w, h, tb)
    # parameters that stand for others ('Title' = '=TitleParameter'): name -> the one it shows
    for mt in re.finditer(rb"\|RECORD=41\|[^\x00]*", data):
        rec = dict(kv.split("=", 1) for kv in mt.group(0).decode("latin1").split("|") if "=" in kv)
        txt = rec.get("Text", "")
        if rec.get("Name") and txt.startswith("=") and len(txt) > 1:
            t.param_refs[rec["Name"]] = txt[1:]
    return t


def _seg_hits_box(a, b, box) -> bool:
    """Segment a-b crosses or touches the box (x1, y1, x2, y2)."""
    x1, y1, x2, y2 = box
    (ax, ay), (bx, by) = a, b
    if max(ax, bx) < x1 or min(ax, bx) > x2 or max(ay, by) < y1 or min(ay, by) > y2:
        return False
    if x1 <= ax <= x2 and y1 <= ay <= y2:
        return True
    # sample the segment (schematic wires are short and axis-aligned in practice)
    n = max(1, int(max(abs(bx - ax), abs(by - ay)) / 25.0))
    return any(x1 <= ax + (bx - ax) * i / n <= x2 and y1 <= ay + (by - ay) * i / n <= y2 for i in range(n + 1))


@dataclass
class Drawing:
    """What a sheet draws, in mil: anchor points, boxes (rectangles, frames, images, sheet
    symbols) and wire segments."""
    pts: list
    boxes: list
    segs: list

    def bbox(self):
        xs = [p[0] for p in self.pts] + [v for b in self.boxes for v in (b[0], b[2])] + \
             [v for s in self.segs for v in (s[0][0], s[1][0])]
        ys = [p[1] for p in self.pts] + [v for b in self.boxes for v in (b[1], b[3])] + \
             [v for s in self.segs for v in (s[0][1], s[1][1])]
        return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def fits(t: Template, d: Drawing, dy: float = 0.0) -> list[str]:
    """Why the drawing, moved up by dy, does not fit on the template (empty list: it fits)."""
    bb = d.bbox()
    if bb is None:
        return []
    x1, y1, x2, y2 = bb[0], bb[1] + dy, bb[2], bb[3] + dy
    why = []
    if x1 < BORDER:
        why.append(f"left by {BORDER - x1:.0f} mil")
    if x2 > t.width - BORDER:
        why.append(f"right by {x2 - (t.width - BORDER):.0f} mil")
    if y1 < BORDER:
        why.append(f"bottom by {BORDER - y1:.0f} mil")
    if y2 > t.height - BORDER:
        why.append(f"top by {y2 - (t.height - BORDER):.0f} mil")
    if t.title_block:
        c = TITLE_CLEARANCE
        tb = (t.title_block[0] - c, t.title_block[1] - c - dy, t.title_block[2] + c, t.title_block[3] + c - dy)
        n = sum(1 for x, y in d.pts if tb[0] <= x <= tb[2] and tb[1] <= y <= tb[3])
        n += sum(1 for b in d.boxes if b[0] <= tb[2] and b[2] >= tb[0] and b[1] <= tb[3] and b[3] >= tb[1])
        n += sum(1 for a, b in d.segs if _seg_hits_box(a, b, tb))
        if n:
            why.append(f"{n} object(s) on the title block")
    return why


def place(t: Template, d: Drawing, top_dy: float) -> float | None:
    """Vertical offset (mil, multiple of 100) that puts the drawing on the template: the one
    nearest to top_dy (which keeps the KiCad top margin); None when no offset fits."""
    bb = d.bbox()
    if bb is None:
        return 0.0
    lo = BORDER - bb[1]                     # lowest offset: bottom on the border
    hi = t.height - BORDER - bb[3]          # highest offset: top on the border
    if lo > hi:
        return None
    cands = range(int(-(-lo // 100)) * 100, int(hi // 100) * 100 + 1, 100)
    for dy in sorted(cands, key=lambda v: (abs(v - top_dy), -v)):
        if not fits(t, d, dy):
            return float(dy)
    return None


def choose(fam: list[Template], d: Drawing, page_h: float, minimum: str = "A3"):
    """(template, dy, reasons): the smallest template of the family, not smaller than `minimum`,
    that the drawing fits on, and the vertical offset that puts it there.  When none fits: the
    largest one, offset 0 and the reasons it does not fit."""
    if not fam:
        return None, 0.0, []
    floor = next((t.area for t in fam if t.size_name.upper() == minimum.upper()), 0)
    cands = [t for t in fam if t.area >= floor] or fam
    for t in cands:
        dy = place(t, d, (t.height - page_h) // 100 * 100)
        if dy is not None:
            return t, dy, []
    big = cands[-1]
    return big, 0.0, fits(big, d)
