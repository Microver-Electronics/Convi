"""Parts that are never assembled: test points, mounting holes, fiducials.

House rule: they are always DNP and never in the BOM.  In Altium they become
'Standard (No BOM)' (they stay on the PCB and in the netlist) and 'Not Fitted' in the DNP
variant (so pick-and-place and assembly outputs leave them out too).  A board-only one (no
schematic symbol, typical for fiducials) becomes 'Graphical': not in the BOM and not
synchronised, so Update PCB does not remove it.
"""
from __future__ import annotations

import re

_NAME_RULES = (
    ("testpoint", re.compile(r"test[\s_-]?point|testpad|test[\s_-]?pad", re.I)),
    ("mountinghole", re.compile(r"mounting[\s_-]?hole|mount[\s_-]?hole", re.I)),
    ("fiducial", re.compile(r"fiducial", re.I)),
)
_REF_RULES = (
    ("testpoint", re.compile(r"^TP\d+$", re.I)),
    ("fiducial", re.compile(r"^(FID|FD)\d+$", re.I)),
    ("mountinghole", re.compile(r"^MH\d+$", re.I)),
)


def marker_kind(ref: str = "", *names: str) -> str | None:
    """'testpoint' / 'mountinghole' / 'fiducial' when the part is one of them, else None.

    names: library symbol id/name, footprint name, value...  A name match is enough; a reference
    match (TP1, FID1, MH1) also counts.  A plain 'H1' counts as a mounting hole only when one of
    the names mentions a hole."""
    for kind, rx in _NAME_RULES:
        if any(n and rx.search(n) for n in names):
            return kind
    for kind, rx in _REF_RULES:
        if ref and rx.match(ref):
            return kind
    if ref and re.match(r"^H\d+$", ref, re.I) and any(n and "hole" in n.lower() for n in names):
        return "mountinghole"
    return None
