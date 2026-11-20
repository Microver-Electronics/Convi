"""Fast S-expression reader for KiCad files.

Nodes are Python lists whose first element is the tag (a plain ``str``).
Atoms are ``str`` (bare symbols / numbers) or :class:`QStr` (quoted strings).
Embedded binary blobs written as ``|base64...|`` become :class:`Blob`.
"""
from __future__ import annotations

import re
from typing import Iterator

_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|(\|[^|]*\|)|([^\s()"|]+))', re.S)
_ESC = re.compile(r'\\(.)', re.S)
_ESC_MAP = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"'}


class QStr(str):
    """A quoted string atom."""
    __slots__ = ()


class Blob(str):
    """Contents of a ``|...|`` data block with whitespace removed."""
    __slots__ = ()


def _unescape(s: str) -> str:
    if "\\" not in s:
        return s
    return _ESC.sub(lambda m: _ESC_MAP.get(m.group(1), m.group(1)), s)


class ParseError(ValueError):
    pass


def parse(text: str) -> list:
    """Parse the first top-level expression in ``text``."""
    stack: list[list] = []
    cur: list | None = None
    root = None
    pos = 0
    n = len(text)
    match = _TOKEN.match
    while pos < n:
        m = match(text, pos)
        if m is None:
            if text[pos:].strip() == "":
                break
            raise ParseError(f"Unexpected character at offset {pos}: {text[pos:pos+30]!r}")
        pos = m.end()
        op, cl, qs, blob, atom = m.groups()
        if op:
            node: list = []
            if cur is not None:
                cur.append(node)
                stack.append(cur)
            cur = node
        elif cl:
            if cur is None:
                raise ParseError(f"Unbalanced ')' at offset {pos}")
            if not stack:
                root = cur
                break
            cur = stack.pop()
        elif qs is not None:
            if cur is None:
                raise ParseError("Atom outside of list")
            cur.append(QStr(_unescape(qs)))
        elif blob is not None:
            cur.append(Blob("".join(blob[1:-1].split())))
        elif atom is not None:
            if cur is None:
                raise ParseError("Atom outside of list")
            cur.append(atom)
        else:  # trailing whitespace
            break
    if root is None:
        raise ParseError("Unterminated S-expression (file truncated?)")
    return root


def parse_file(path) -> list:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return parse(f.read())


# ---------------------------------------------------------------- helpers

def tag(node) -> str | None:
    return node[0] if isinstance(node, list) and node else None


def find(node: list, name: str):
    """First child list with tag ``name`` or None."""
    for c in node[1:] if node else ():
        if isinstance(c, list) and c and c[0] == name:
            return c
    return None


def findall(node: list, name: str) -> Iterator[list]:
    for c in node[1:] if node else ():
        if isinstance(c, list) and c and c[0] == name:
            yield c


def children(node: list) -> Iterator[list]:
    for c in node[1:]:
        if isinstance(c, list) and c:
            yield c


def atoms(node: list) -> list:
    return [c for c in node[1:] if not isinstance(c, list)]


def val(node: list, name: str, default=None, idx: int = 1):
    """Atom value of child ``(name value ...)``."""
    c = find(node, name)
    if c is None or len(c) <= idx or isinstance(c[idx], list):
        return default
    return c[idx]


def fval(node: list, name: str, default: float = 0.0, idx: int = 1) -> float:
    v = val(node, name, None, idx)
    if v is None:
        return default
    try:
        return float(v)
    except ValueError:
        return default


def flag(node: list, name: str, default: bool = False) -> bool:
    """Handle both ``(name yes|no)`` and bare-atom ``name`` forms."""
    for c in node[1:]:
        if isinstance(c, list):
            if c and c[0] == name:
                if len(c) == 1:
                    return True
                return str(c[1]).lower() in ("yes", "true", "1")
        elif c == name and not isinstance(c, QStr):
            return True
    return default


def has_atom(node: list, name: str) -> bool:
    return any((not isinstance(c, list)) and c == name and not isinstance(c, QStr) for c in node[1:])


def xy(node: list | None, default=(0.0, 0.0)):
    if node is None:
        return default
    return float(node[1]), float(node[2])


def at(node: list):
    """(at x y [angle]) -> (x, y, angle)."""
    a = find(node, "at")
    if a is None:
        return 0.0, 0.0, 0.0
    x = float(a[1]) if len(a) > 1 else 0.0
    y = float(a[2]) if len(a) > 2 else 0.0
    ang = 0.0
    if len(a) > 3 and not isinstance(a[3], list):
        try:
            ang = float(a[3])
        except ValueError:
            ang = 0.0
    return x, y, ang


def pts(node: list) -> list[tuple]:
    """(pts (xy ..) (arc (start)(mid)(end)) ...) -> list of ('xy', x, y) / ('arc', s, m, e)."""
    p = find(node, "pts")
    out = []
    if p is None:
        return out
    for c in p[1:]:
        if not isinstance(c, list):
            continue
        if c[0] == "xy":
            out.append(("xy", float(c[1]), float(c[2])))
        elif c[0] == "arc":
            out.append(("arc", xy(find(c, "start")), xy(find(c, "mid")), xy(find(c, "end"))))
    return out
