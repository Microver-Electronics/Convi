"""Shared KiCad structures: text effects, strokes, issue collection."""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import sexpr as S


@dataclass
class Issue:
    level: str      # info / warning / error / missing
    area: str       # project / sch / pcb / 3d / lib ...
    message: str
    detail: str = ""


class Issues:
    """Collects messages produced while reading and converting."""

    def __init__(self):
        self.items: list[Issue] = []
        self._seen: set = set()

    def _add(self, level, area, msg, detail="", dedupe=True):
        key = (level, area, msg)
        if dedupe and key in self._seen:
            return
        self._seen.add(key)
        self.items.append(Issue(level, area, msg, detail))

    def info(self, area, msg, detail=""):
        self._add("info", area, msg, detail)

    def warn(self, area, msg, detail=""):
        self._add("warning", area, msg, detail)

    def error(self, area, msg, detail=""):
        self._add("error", area, msg, detail)

    def missing(self, area, msg, detail=""):
        self._add("missing", area, msg, detail)

    def of(self, level):
        return [i for i in self.items if i.level == level]

    def extend(self, other: "Issues"):
        for i in other.items:
            self._add(i.level, i.area, i.message, i.detail)


@dataclass
class Stroke:
    width: float | None = None
    style: str = "solid"     # solid / dash / dot / dash_dot / dash_dot_dot / default
    color: tuple | None = None


@dataclass
class TextEffects:
    size: tuple = (1.27, 1.27)   # (height, width) mm  -- KiCad stores (size H W)
    thickness: float | None = None
    bold: bool = False
    italic: bool = False
    face: str = ""               # TrueType font name, "" = KiCad stroke font
    hjustify: str = "center"     # left / center / right
    vjustify: str = "center"     # top / center / bottom
    mirror: bool = False
    hidden: bool = False
    color: tuple | None = None
    line_spacing: float = 1.0
    href: str = ""


def parse_color(node):
    if node is None or len(node) < 4:
        return None
    try:
        r, g, b = (int(float(node[i])) for i in (1, 2, 3))
        a = float(node[4]) if len(node) > 4 else 1.0
    except ValueError:
        return None
    if r == g == b == 0 and a == 0:
        return None
    return r, g, b, a


def parse_stroke(node) -> Stroke:
    st = Stroke()
    s = S.find(node, "stroke")
    if s is not None:
        w = S.val(s, "width")
        st.width = float(w) if w is not None else None
        st.style = str(S.val(s, "type", "solid"))
        st.color = parse_color(S.find(s, "color"))
    else:
        w = S.val(node, "width")
        if w is not None:
            st.width = float(w)
    return st


def parse_effects(node) -> TextEffects:
    e = TextEffects()
    if node is None:
        return e
    f = S.find(node, "font")
    if f is not None:
        sz = S.find(f, "size")
        if sz is not None and len(sz) >= 3:
            e.size = (float(sz[1]), float(sz[2]))
        th = S.val(f, "thickness")
        if th is not None:
            e.thickness = float(th)
        e.bold = S.flag(f, "bold")
        e.italic = S.flag(f, "italic")
        face = S.val(f, "face")
        if face:
            e.face = str(face)
        e.color = parse_color(S.find(f, "color"))
        ls = S.val(f, "line_spacing")
        if ls is not None:
            e.line_spacing = float(ls)
    j = S.find(node, "justify")
    if j is not None:
        for v in j[1:]:
            if v in ("left", "right"):
                e.hjustify = str(v)
            elif v in ("top", "bottom"):
                e.vjustify = str(v)
            elif v == "mirror":
                e.mirror = True
    e.hidden = S.flag(node, "hide")
    href = S.val(node, "href")
    if href:
        e.href = str(href)
    return e


_KICAD_ESC = {"{slash}": "/", "{backslash}": "\\", "{lt}": "<", "{gt}": ">", "{colon}": ":", "{dblquote}": '"',
              "{quote}": "'", "{tab}": "\t", "{return}": "\n", "{space}": " ", "{brace}": "{", "{dollar}": "$",
              "{tilde}": "~", "{comma}": ",", "{semicolon}": ";", "{bar}": "|"}


def unescape_name(s: str) -> str:
    """Undo KiCad's EscapeString() used for net, label and reference names (e.g. {slash})."""
    if not s or "{" not in s:
        return s
    for k, v in _KICAD_ESC.items():
        if k in s:
            s = s.replace(k, v)
    return s
