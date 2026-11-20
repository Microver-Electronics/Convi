"""KiCad custom design rules (.kicad_dru): parser, condition AST, evaluation and Altium query translation.

The rule *mapping* (which Altium rule kind, priorities, redundancy) lives in rules.py; this module only
understands the KiCad language:

* ``parse_dru(path)`` -> list of ``KRule`` (name, layer, condition AST, constraints) in file order.
* ``Condition AST``: ("and", [..]) ("or", [..]) ("not", x) ("const", bool) ("pred", obj, fn, op, arg).
* ``evaluate(node, env)`` three-valued (True / False / None = depends on geometry or object type) over
  abstract items ``{"net": kicad net, "type": "pad"|"via"|..|None, "ref": footprint ref|None}``.
* ``Translator`` turns one object's part of a condition into an Altium query string.

Supported condition terms (A., B. or AB.): hasNetclass('X') / hasExactNetclass('X'), NetClass ==/!= 'X',
NetName ==/!= 'X' (wildcards), Type ==/!= 'Via|Pad|Track|Arc|Zone|Text', memberOfFootprint('R*'),
intersectsCourtyard / intersectsFrontCourtyard / intersectsBackCourtyard('R*') (Altium rooms), Layer == 'F.Cu',
existsOnLayer('F.Cu'); combined with &&, ||, ! and parentheses.  Anything else is reported, never guessed.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import sexpr as S

TRUE = ("const", True)
FALSE = ("const", False)

# KiCad Type names -> (item type key, Altium query)
TYPES = {
    "via": ("via", "IsVia"),
    "pad": ("pad", "IsPad"),
    "track": ("track", "(IsTrack or IsArc)"),
    "arc": ("arc", "IsArc"),
    # the pour's pieces as well as the polygon object: DRC tests the pieces (InPolygon), the pour the polygon
    "zone": ("zone", "(IsPolygon or InPolygon)"),
    "text": ("text", "IsText"),
}


@dataclass
class Constraint:
    kind: str
    vals: dict = field(default_factory=dict)      # min / opt / max in mm
    words: list = field(default_factory=list)     # disallow items, zone_connection style, flags


@dataclass
class KRule:
    index: int
    name: str
    layer: str | None
    cond_text: str
    cond: tuple | None            # AST, None = no condition (always)
    constraints: list
    error: str | None = None      # condition could not be parsed


def mm_value(v: str) -> float:
    v = str(v).strip().lower()
    for suf, f in (("mm", 1.0), ("mil", 0.0254), ("mils", 0.0254), ("in", 25.4), ("um", 0.001)):
        if v.endswith(suf):
            return float(v[: -len(suf)]) * f
    return float(v)


# ------------------------------------------------------------------ parsing
_TOK = re.compile(r"""
    (?P<ws>\s+)
  | (?P<str>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
  | (?P<op>&&|\|\||==|!=|<=|>=|<|>|!|\(|\)|,)
  | (?P<id>[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*)
  | (?P<num>[0-9.]+[a-z]*)
""", re.X)


def _tokens(text: str) -> list:
    out, pos = [], 0
    while pos < len(text):
        m = _TOK.match(text, pos)
        if not m:
            raise ValueError(f"unexpected text near '{text[pos:pos + 25]}'")
        pos = m.end()
        if m.group("ws"):
            continue
        if m.group("str") is not None:
            s = m.group("str")[1:-1]
            out.append(("str", re.sub(r"\\(.)", r"\1", s)))
        elif m.group("op"):
            out.append(("op", m.group("op")))
        elif m.group("id"):
            out.append(("id", m.group("id")))
        else:
            out.append(("num", m.group("num")))
    return out


class _Parser:
    def __init__(self, text: str):
        self.t = _tokens(text)
        self.i = 0

    def peek(self, k=0):
        return self.t[self.i + k] if self.i + k < len(self.t) else (None, None)

    def take(self, kind=None, val=None):
        tok = self.peek()
        if tok[0] is None or (kind and tok[0] != kind) or (val and tok[1] != val):
            raise ValueError(f"expected {val or kind} near token {self.i + 1}")
        self.i += 1
        return tok

    def parse(self):
        n = self.expr()
        if self.peek()[0] is not None:
            raise ValueError(f"unexpected '{self.peek()[1]}'")
        return n

    def expr(self):
        items = [self.conj()]
        while self.peek() == ("op", "||"):
            self.take()
            items.append(self.conj())
        return items[0] if len(items) == 1 else ("or", items)

    def conj(self):
        items = [self.unary()]
        while self.peek() == ("op", "&&"):
            self.take()
            items.append(self.unary())
        return items[0] if len(items) == 1 else ("and", items)

    def unary(self):
        if self.peek() == ("op", "!"):
            self.take()
            return ("not", self.unary())
        if self.peek() == ("op", "("):
            self.take()
            n = self.expr()
            self.take("op", ")")
            return n
        return self.term()

    def term(self):
        kind, ident = self.take("id")
        if ident.lower() in ("true", "false") and "." not in ident:
            return ("const", ident.lower() == "true")
        if "." not in ident:
            raise ValueError(f"'{ident}' is not supported")
        obj, prop = ident.split(".", 1)
        if obj not in ("A", "B", "AB"):
            raise ValueError(f"'{ident}' is not supported")
        if self.peek() == ("op", "("):                       # function call
            self.take()
            args = []
            while self.peek() != ("op", ")"):
                k, v = self.take()
                if k not in ("str", "num"):
                    raise ValueError(f"{prop}(): unsupported argument")
                args.append(v)
                if self.peek() == ("op", ","):
                    self.take()
            self.take("op", ")")
            return ("pred", obj, prop, "call", args)
        op = self.peek()
        if op[0] == "op" and op[1] in ("==", "!=", "<", ">", "<=", ">="):
            self.take()
            k, v = self.take()
            if k not in ("str", "num", "id"):
                raise ValueError(f"{ident}: unsupported value")
            return ("pred", obj, prop, op[1], v)
        raise ValueError(f"'{ident}' without comparison is not supported")


def parse_condition(text: str):
    return _Parser(text).parse()


def parse_dru(path: Path, issues=None) -> list[KRule]:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    text = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    try:
        root = S.parse("(root " + text + ")")
    except Exception as e:
        if issues:
            issues.warn("rules", f"{Path(path).name}: cannot read custom rules ({e})")
        return []
    out = []
    for i, r in enumerate(S.findall(root, "rule")):
        name = str(r[1]) if len(r) > 1 else f"rule{i + 1}"
        cond_text = str(S.val(r, "condition", "") or "")
        layer = S.val(r, "layer")
        cons = []
        for c in S.findall(r, "constraint"):
            kind = str(c[1]) if len(c) > 1 else ""
            con = Constraint(kind)
            for item in c[2:]:
                if isinstance(item, list) and item and str(item[0]) in ("min", "opt", "max"):
                    try:
                        con.vals[str(item[0])] = mm_value(str(item[1]))
                    except (ValueError, IndexError):
                        pass
                elif isinstance(item, list) and item:
                    con.words.append(str(item[0]))
                else:
                    con.words.append(str(item))
            cons.append(con)
        kr = KRule(i, name, str(layer) if layer is not None else None, cond_text, TRUE, cons)
        if cond_text.strip():
            try:
                kr.cond = parse_condition(cond_text)
            except ValueError as e:
                kr.cond, kr.error = None, str(e)
        out.append(kr)
    return out


# ------------------------------------------------------------------ AST helpers
def objects(node) -> set:
    t = node[0]
    if t == "pred":
        return {"A", "B"} if node[1] == "AB" else {node[1]}
    if t in ("and", "or"):
        s = set()
        for x in node[1]:
            s |= objects(x)
        return s
    if t == "not":
        return objects(node[1])
    return set()


def has_fn(node, names) -> bool:
    t = node[0]
    if t == "pred":
        return node[2] in names
    if t in ("and", "or"):
        return any(has_fn(x, names) for x in node[1])
    if t == "not":
        return has_fn(node[1], names)
    return False


# KiCad's area functions test a zone by its fill (pcbexpr isInsideArea: an unfilled zone is in no area), and
# the filler works on unfilled zones: a rule built on insideArea()/intersectsArea()/enclosedByArea() never
# shapes a zone fill (seen on a test board: a zone poured 4.5 mm from another by the class rule, not the 6.4 mm
# area rule, though its fill lies in both rule areas)
AREA_FNS = ("insideArea", "intersectsArea", "enclosedByArea")
NOT_POUR = "not (IsPolygon or InPolygon)"

GEOMETRIC = ("intersectsCourtyard", "intersectsFrontCourtyard", "intersectsBackCourtyard", "Layer",
             "existsOnLayer", "insideArea", "intersectsArea", "enclosedByArea")


def mirror(node):
    """Swap A and B in a condition."""
    t = node[0]
    if t == "pred":
        o = {"A": "B", "B": "A"}.get(node[1], node[1])
        return ("pred", o) + tuple(node[2:])
    if t in ("and", "or"):
        return (t, [mirror(x) for x in node[1]])
    if t == "not":
        return ("not", mirror(node[1]))
    return node


def _key(node):
    return repr(node)


def _symmetric_or(node):
    """KiCad tests two-object rules both ways, so 'X(A) || X(B)' is the same rule as 'X(A)'."""
    if node[0] != "or":
        return node
    a_items = [x for x in node[1] if objects(x) == {"A"}]
    b_items = [x for x in node[1] if objects(x) == {"B"}]
    if len(a_items) + len(b_items) != len(node[1]) or not a_items or not b_items:
        return node
    if sorted(_key(mirror(x)) for x in b_items) != sorted(_key(x) for x in a_items):
        return node
    return a_items[0] if len(a_items) == 1 else ("or", a_items)


def split_dnf(node, limit: int = 128) -> list:
    """(A-part, B-part) pairs whose OR is the condition, for a condition split_ab cannot take
    ('(A.x && B.x) || (A.y && B.y)'). Each pair becomes one Altium rule. Raises when a negated term
    mixes A and B or the expansion passes `limit` pairs."""
    def is_side(x):
        return len(objects(x)) <= 1

    def dnf(n):
        if is_side(n):
            return [[n]]
        t = n[0]
        if t == "pred" and n[1] == "AB":
            return [[("pred", "A") + tuple(n[2:]), ("pred", "B") + tuple(n[2:])]]
        if t == "and":
            out = [[]]
            for c in n[1]:
                d = dnf(c)
                out = [r + x for r in out for x in d]
                if len(out) > limit:
                    raise ValueError(f"the condition expands to more than {limit} Altium rules")
            return out
        if t == "or":
            out = []
            a_side = [c for c in n[1] if objects(c) == {"A"}]
            b_side = [c for c in n[1] if objects(c) == {"B"}]
            plain = [c for c in n[1] if not objects(c)]
            if a_side:
                out.append([a_side[0] if len(a_side) == 1 else ("or", a_side)])
            if b_side:
                out.append([b_side[0] if len(b_side) == 1 else ("or", b_side)])
            if plain:
                out.append([plain[0] if len(plain) == 1 else ("or", plain)])
            for c in n[1]:
                if len(objects(c)) > 1:
                    out += dnf(c)
            if len(out) > limit:
                raise ValueError(f"the condition expands to more than {limit} Altium rules")
            return out
        if t == "not":
            # De Morgan: '!(A.Type == 'Zone' && B.Type == 'Zone')' is 'A.Type != 'Zone' || B.Type != 'Zone''
            # (e.g. a rule 'GND 0.125 mm except zone to zone')
            neg = _negate(n[1])
            if neg is not None:
                return dnf(neg)
        raise ValueError("a negated term combining A and B cannot be expressed as Altium scopes")

    def eq_preds(x):
        # (object, property) -> value of every 'Obj.Prop == value' a term pins down (an OR of them: a set)
        if x[0] == "pred" and x[3] == "==" and x[2] in ("NetClass", "Type"):
            return {(x[1], x[2]): {str(x[4]).lower()}}
        if x[0] == "or":
            parts = [eq_preds(c) for c in x[1]]
            keys = set(parts[0]) if parts and all(len(p) == 1 for p in parts) else set()
            if keys and all(set(p) == keys for p in parts):
                k = next(iter(keys))
                return {k: set().union(*(p[k] for p in parts))}
        return {}

    def impossible(conj):
        # 'A.NetClass == X && A.NetClass == Y' (one net has one class) and the like
        want: dict = {}
        for x in conj:
            for k, v in eq_preds(x).items():
                want[k] = want[k] & v if k in want else set(v)
                if not want[k]:
                    return True
        return False

    def pad_member(side):
        # a pad 'intersecting the courtyard of R' is, but for a foreign pad reaching into it, a pad of R:
        # InComponent needs no room object (one per part otherwise) and is the stricter reading
        if not any(x[0] == "pred" and x[2] == "Type" and x[3] == "==" and str(x[4]).lower() == "pad"
                   for x in side):
            return side
        return [("pred", x[1], "memberOfFootprint", "call", [x[4][0]])
                if x[0] == "pred" and x[2] in ("intersectsCourtyard", "intersectsFrontCourtyard",
                                               "intersectsBackCourtyard") and x[3] == "call" else x
                for x in side]

    mk = lambda l: TRUE if not l else (l[0] if len(l) == 1 else ("and", l))
    pairs, seen = [], set()
    for conj in dnf(_symmetric_or(node)):
        if impossible(conj):
            continue
        a = pad_member([x for x in conj if objects(x) != {"B"}])
        b = pad_member([x for x in conj if objects(x) == {"B"}])
        pa, pb = mk(a), mk(b)
        k = (_key(pa), _key(pb))
        # an Altium rule matches both ways round, as KiCad tests both: (X, Y) and its mirror are one rule
        if k not in seen and (_key(mirror(pb)), _key(mirror(pa))) not in seen:
            seen.add(k)
            pairs.append((pa, pb))
    return pairs


def _negate(node):
    """The negation of a condition with the 'not' pushed onto single predicates, or None if a predicate has no
    plain opposite (only ==/!= on one object, and nested and/or/not, are rewritten)."""
    t = node[0]
    if t == "not":
        return node[1]
    if t == "and":
        parts = [_negate(x) for x in node[1]]
        return None if None in parts else ("or", parts)
    if t == "or":
        parts = [_negate(x) for x in node[1]]
        return None if None in parts else ("and", parts)
    if t == "pred":
        if len(objects(node)) == 1:
            if len(node) > 3 and node[3] in ("==", "!="):
                return node[:3] + ({"==": "!=", "!=": "=="}[node[3]],) + node[4:]
            return ("not", node)
        return None
    if t == "const":
        return ("const", not node[1])
    return None


def split_ab(node):
    """(A-part, B-part) of an AND-chain; raises if one conjunct mixes A and B."""
    node = _symmetric_or(node)
    items = node[1] if node[0] == "and" else [node]
    a, b = [], []
    for x in items:
        ob = objects(x)
        if ob == {"A", "B"}:
            raise ValueError("a term combining A and B cannot be expressed as Altium scopes")
        (b if ob == {"B"} else a).append(x)
    mk = lambda l: TRUE if not l else (l[0] if len(l) == 1 else ("and", l))
    return mk(a), mk(b)


def strip_type(node, typ: str):
    """Remove 'Type == typ' terms from an AND-chain (for net-level Altium rules). Returns (node, found)."""
    items = node[1] if node[0] == "and" else [node]
    keep, found = [], False
    for x in items:
        if x[0] == "pred" and x[2] == "Type" and x[3] == "==" and str(x[4]).lower() == typ:
            found = True
            continue
        keep.append(x)
    return (TRUE if not keep else keep[0] if len(keep) == 1 else ("and", keep)), found


def _wild(pat: str, s: str) -> bool:
    return fnmatch.fnmatchcase(s, pat)


def _is_text_exclusion(node) -> bool:
    return node[0] == "pred" and node[2] == "Type" and node[3] == "!=" and str(node[4]).lower() == "text"


# ------------------------------------------------------------------ evaluation
def evaluate(node, env: dict, classes_of) -> bool | None:
    """Three-valued evaluation. env: {"A": item, "B": item}; item = {"net", "type", "ref"}."""
    t = node[0]
    if t == "const":
        return node[1]
    if t == "not":
        v = evaluate(node[1], env, classes_of)
        return None if v is None else not v
    if t in ("and", "or"):
        vals = [evaluate(x, env, classes_of) for x in node[1]]
        if t == "and":
            return False if False in vals else (None if None in vals else True)
        return True if True in vals else (None if None in vals else False)
    _, obj, fn, op, arg = node
    if obj == "AB":
        va = evaluate(("pred", "A", fn, op, arg), env, classes_of)
        vb = evaluate(("pred", "B", fn, op, arg), env, classes_of)
        return False if False in (va, vb) else (None if None in (va, vb) else True)
    it = env.get(obj)
    if it is None:
        return None
    if _is_text_exclusion(node):
        return True
    if fn in ("hasNetclass", "hasExactNetclass") or fn == "NetClass":
        pat = arg[0] if op == "call" else arg
        cls = classes_of(it.get("net") or "")
        v = any(_wild(pat, c) for c in cls)
        return (not v) if op == "!=" else v
    if fn == "NetName":
        v = _wild(str(arg), it.get("net") or "")
        return (not v) if op == "!=" else v
    if fn == "Type":
        if it.get("type") is None:
            return None
        want = TYPES.get(str(arg).lower(), (str(arg).lower(), None))[0]
        have = it["type"]
        v = have == want or (want == "track" and have == "arc")
        return (not v) if op == "!=" else v
    if fn == "memberOfFootprint":
        if it.get("ref") is None:
            return None
        return _wild(arg[0], it["ref"])
    if fn in AREA_FNS and it.get("type") == "zone":
        return False  # a zone being filled is in no rule area (see AREA_FNS)
    return None      # geometry / layer: depends on where the object is


# ------------------------------------------------------------------ Altium translation
class Translator:
    """Builds Altium query text for one object's part of a condition."""

    def __init__(self, class_names, kicad_nets, net_name, refs, courtyard_sides, copper_layers, layer_name,
                 areas=None):
        self.class_names = list(class_names)          # Altium net classes that exist (have members)
        self.kicad_nets = list(kicad_nets)
        self.net_name = net_name                      # kicad net -> Altium net (or None)
        self.refs = list(refs)
        self.courtyard_sides = courtyard_sides        # ref -> set("F","B") of courtyard sides
        self.copper = list(copper_layers)             # KiCad copper layer names in stack order
        self.layer_name = layer_name                  # KiCad layer -> Altium layer name
        self.rooms: dict[str, str] = {}               # ref -> room name (requested)
        self.areas = areas or {}                      # rule area name -> [rect (x0, y0, x1, y1) mm]
        self.area_rooms: dict[str, list] = {}         # rule area name -> [(room name, rect)] (requested)
        self.notes: list[str] = []

    @staticmethod
    def q(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"

    def area_room_names(self, name: str) -> list[str]:
        rects = self.areas.get(name)
        if not rects:
            raise ValueError(f"rule area '{name}' is not on the board or is not made of rectangles")
        base = "Area_" + re.sub(r"[^A-Za-z0-9_]", "_", name)
        rooms = [(base if len(rects) == 1 else f"{base}_{i + 1}", r) for i, r in enumerate(rects)]
        self.area_rooms[name] = rooms
        return [n for n, _ in rooms]

    def room(self, ref: str) -> str:
        nm = f"Courtyard_{ref}"
        self.rooms[ref] = nm
        return nm

    def layers_matching(self, pat: str) -> list[str]:
        if pat == "outer":
            return [self.copper[0], self.copper[-1]] if self.copper else []
        if pat == "inner":
            return self.copper[1:-1]
        hits = [l for l in self.copper if _wild(pat, l)]
        if not hits and pat in self.layer_name.values():
            hits = [k for k, v in self.layer_name.items() if v == pat and k in self.copper]
        return hits

    def layer_query(self, layers: list[str]):
        if not layers:
            return FALSE
        return ("q", " or ".join(f"OnLayer({self.q(self.layer_name.get(l, l))})" for l in layers), len(layers) > 1)

    def to_query(self, node) -> str | None:
        """Altium query, 'All' for no restriction, or None if the condition never matches."""
        r = self._tr(node)
        if r == TRUE:
            return "All"
        if r == FALSE:
            return None
        return r[1]

    def _or(self, parts):
        parts = [p for p in parts if p != FALSE]
        if not parts:
            return FALSE
        if TRUE in parts:
            return TRUE
        if len(parts) == 1:
            return parts[0]
        return ("q", " or ".join(self._wrap(p) for p in parts), True)

    def _and(self, parts):
        parts = [p for p in parts if p != TRUE]
        if FALSE in parts:
            return FALSE
        if not parts:
            return TRUE
        if len(parts) == 1:
            return parts[0]
        return ("q", " and ".join(self._wrap(p) for p in parts), True)

    @staticmethod
    def _wrap(p):
        return f"({p[1]})" if p[2] else p[1]

    def _tr(self, node):
        t = node[0]
        if t == "const":
            return TRUE if node[1] else FALSE
        if t == "and":
            return self._and([self._tr(x) for x in node[1]])
        if t == "or":
            return self._or([self._tr(x) for x in node[1]])
        if t == "not":
            r = self._tr(node[1])
            if r in (TRUE, FALSE):
                return FALSE if r == TRUE else TRUE
            return ("q", f"not ({r[1]})" if r[2] else f"not {r[1]}", False)
        _, obj, fn, op, arg = node
        if _is_text_exclusion(node):
            return TRUE        # Altium copper rules do not test texts like KiCad does
        neg = op == "!="
        if fn in ("hasNetclass", "hasExactNetclass", "NetClass"):
            pat = arg[0] if op == "call" else str(arg)
            hits = [c for c in self.class_names if _wild(pat, c) and c != "Default"]
            parts = [("q", f"InNetClass({self.q(c)})", False) for c in hits]
            if _wild(pat, "Default"):
                # KiCad's Default class holds every net no other class takes: in Altium, not in any of the
                # converted net classes (which Convi fills with each net's effective class)
                others = [c for c in self.class_names if c != "Default"]
                parts.append(("q", "not (" + " or ".join(f"InNetClass({self.q(c)})" for c in others) + ")", False)
                             if others else TRUE)
            r = self._or(parts)
        elif fn == "NetName":
            hits = [n for n in self.kicad_nets if _wild(str(arg), n)]
            alt = sorted({self.net_name(n) for n in hits if self.net_name(n)})
            r = self._or([("q", f"InNet({self.q(a)})", False) for a in alt])
        elif fn == "Type":
            key = str(arg).lower()
            if key not in TYPES:
                raise ValueError(f"object type '{arg}' is not supported")
            q = TYPES[key][1]
            r = ("q", q, False)
        elif fn == "memberOfFootprint" and op == "call":
            pat = arg[0]
            if pat.startswith("${"):
                raise ValueError("component-class footprint selection is not supported")
            hits = [x for x in self.refs if _wild(pat, x)]
            r = self._or([("q", f"InComponent({self.q(x)})", False) for x in hits])
        elif fn in ("intersectsCourtyard", "intersectsFrontCourtyard", "intersectsBackCourtyard") and op == "call":
            pat = arg[0]
            if pat.startswith("${"):
                raise ValueError("component-class courtyard selection is not supported")
            side = {"intersectsFrontCourtyard": "F", "intersectsBackCourtyard": "B"}.get(fn)
            hits = [x for x in self.refs if _wild(pat, x) and self.courtyard_sides.get(x)
                    and (side is None or side in self.courtyard_sides[x])]
            r = self._or([("q", f"TouchesRoom({self.q(self.room(x))})", False) for x in hits])
        elif fn in ("insideArea", "intersectsArea") and op == "call":
            # KiCad 7: insideArea() = intersectsArea(), any part of the object inside the rule area. The
            # area becomes rooms (a rectilinear area: one room per rectangle of it)
            # area becomes rooms (a rectilinear area: one room per rectangle of it). Pours are left out (AREA_FNS)
            r = self._or([("q", f"TouchesRoom({self.q(n)})", False) for n in self.area_room_names(arg[0])])
            r = self._and([r, ("q", NOT_POUR, False)])
        elif fn == "enclosedByArea" and op == "call":
            names = self.area_room_names(arg[0])
            if len(names) != 1:
                raise ValueError(f"'{obj}.enclosedByArea()' of an area that is not one rectangle is not supported")
            r = self._and([("q", f"WithinRoom({self.q(names[0])})", False), ("q", NOT_POUR, False)])
        elif fn == "Layer" and op in ("==", "!="):
            r = self.layer_query(self.layers_matching(str(arg)))
        elif fn == "existsOnLayer" and op == "call":
            r = self.layer_query(self.layers_matching(arg[0]))
        else:
            what = f"{fn}()" if op == "call" else f"{fn} {op}"
            raise ValueError(f"'{obj}.{what}' is not supported")
        if neg:
            if r in (TRUE, FALSE):
                return FALSE if r == TRUE else TRUE
            return ("q", f"not ({r[1]})" if r[2] else f"not {r[1]}", False)
        return r
