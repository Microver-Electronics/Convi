"""Patch values that Altium's script API cannot set straight into a saved, closed SchDoc.

A harness connector's side (HarnessConnectorSide: 1 = entries on the left, harness connection on the right,
Altium's default; 0 = mirrored) has no working script property (AD25: Side / HarnessConnectorSide are
refused, the MCP bridge cannot set it either). Convi builds every connector with the default and flips the
ones whose entries face right here.  Records live in the SchDoc's 'Additional' stream as
[uint24 length][uint8 type]|KEY=VALUE|...\\0; '1' -> '0' keeps every size, so no sector moves.
"""
from __future__ import annotations

import re
from pathlib import Path

from .pcbdoc_patch import _Cfb

_REC = re.compile(rb"\|RECORD=215\|[^\x00]*")


def _coord(rec: str, key: str) -> float:
    m = re.search(r"\|" + re.escape(key) + r"=(-?\d+)", rec)
    f = re.search(r"\|" + re.escape(key) + r"_Frac=(-?\d+)", rec)
    return (int(m.group(1)) if m else 0) * 10.0 + (int(f.group(1)) / 100000.0 if f else 0.0)


def harness_connectors(schdoc: Path) -> list[dict]:
    """Every harness connector: location (mil, top-left), size (mil), side, from the 'Additional' stream."""
    data = bytearray(Path(schdoc).read_bytes())
    cfb = _Cfb(data)
    out = []
    for name in ("Additional", "FileHeader"):
        try:
            s = cfb.read(name)
        except KeyError:
            continue
        for m in _REC.finditer(s):
            r = m.group(0).decode("latin-1")
            side = re.search(r"\|HarnessConnectorSide=(\d)", r)
            out.append({"stream": name, "x": _coord(r, "Location.X"), "y": _coord(r, "Location.Y"),
                        "w": _coord(r, "XSize"), "h": _coord(r, "YSize"), "side": int(side.group(1)) if side else 0})
    return out


def set_harness_sides(schdoc: Path, flips: list[tuple[float, float]], tol: float = 0.5) -> int:
    """Mirror the harness connectors whose top-left corner (mil) is in flips: HarnessConnectorSide 1 -> 0.
    Returns how many records were changed."""
    schdoc = Path(schdoc)
    data = bytearray(schdoc.read_bytes())
    cfb = _Cfb(data)
    done = 0
    for name in ("Additional", "FileHeader"):
        try:
            s = cfb.read(name)
        except KeyError:
            continue
        new = bytearray(s)
        for m in _REC.finditer(s):
            r = m.group(0).decode("latin-1")
            x, y = _coord(r, "Location.X"), _coord(r, "Location.Y")
            if not any(abs(x - fx) <= tol and abs(y - fy) <= tol for fx, fy in flips):
                continue
            k = r.find("|HarnessConnectorSide=1")
            if k < 0:
                continue
            p = m.start() + k + len("|HarnessConnectorSide=")
            new[p:p + 1] = b"0"
            done += 1
        if bytes(new) != s:
            cfb.write_same_size(name, bytes(new))
            if cfb.read(name) != bytes(new):
                raise ValueError("read-back mismatch")
    if done:
        tmp = schdoc.with_suffix(schdoc.suffix + ".convi_tmp")
        tmp.write_bytes(bytes(data))
        tmp.replace(schdoc)
    return done
