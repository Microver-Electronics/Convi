"""Convi IR: the instruction file executed by the Altium-side DelphiScript builder.

Format: UTF-8 (with BOM), one command per line, TAB separated.  The first
field is the command, every other field is ``key=value``.  Values are
percent-escaped for '%', TAB, CR, LF and '"'.  Coordinates are Altium internal
units (1 mil = 10000) as integers.  Python floats (angles, ratios...) are written as
integer milli-units and read back with VD() in the builder.
"""
from __future__ import annotations

from pathlib import Path

MIL = 10000
MM = 10000 / 0.0254  # internal units per mm = 393700.787...


def mm(v: float) -> int:
    return int(round(v * MM))


def _esc(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        # floats travel as integer milli-units (locale independent); builder divides by 1000
        return str(int(round(v * 1000)))
    s = str(v)
    if any(c in s for c in '%\t\r\n"'):
        s = (s.replace("%", "%25").replace("\t", "%09").replace("\r", "%0D")
             .replace("\n", "%0A").replace('"', "%22"))
    return s


class IRWriter:
    def __init__(self):
        self.lines: list[str] = []
        self.counts: dict[str, int] = {}

    def cmd(self, _cmd: str, **kw):
        parts = [_cmd]
        for k, v in kw.items():
            if v is None:
                continue
            parts.append(f"{k}={_esc(v)}")
        self.lines.append("\t".join(parts))
        self.counts[_cmd] = self.counts.get(_cmd, 0) + 1

    def comment(self, text: str):
        self.lines.append("#" + text.replace("\n", " "))

    def save(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:
            for ln in self.lines:
                f.write(ln + "\n")

    def __len__(self):
        return len(self.lines)
