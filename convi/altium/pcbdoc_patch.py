"""Patch values that Altium's script API cannot set straight into a saved, closed PcbDoc.

A PcbDoc is an OLE compound file. Design rules live in the stream ``Rules6/Data`` as records
``[uint16 kind][uint32 length][|KEY=VALUE|...\\0]``. The creepage rule keeps its distance in
``CHECKDISTANCE`` and whether the polygon pour honours it in ``APPLYTOPOLYGONPOUR``; neither is
exposed to DelphiScript (AD24/26), so Convi writes them here.

The stream is rewritten in place with exactly its old size (the rule's COMMENT absorbs the length
difference), so no sector of the compound file moves. Pure Python, no extra package.
"""
from __future__ import annotations

import struct
from pathlib import Path

FREESECT, ENDOFCHAIN = 0xFFFFFFFF, 0xFFFFFFFE


class _Cfb:
    def __init__(self, data: bytearray):
        self.d = data
        if bytes(data[:8]) != bytes.fromhex("D0CF11E0A1B11AE1"):
            raise ValueError("not an OLE compound file")
        self.ss = 1 << struct.unpack_from("<H", data, 0x1E)[0]
        self.mss = 1 << struct.unpack_from("<H", data, 0x20)[0]
        nfat = struct.unpack_from("<I", data, 0x2C)[0]
        self.dir0 = struct.unpack_from("<I", data, 0x30)[0]
        self.cutoff = struct.unpack_from("<I", data, 0x38)[0]
        minifat0 = struct.unpack_from("<I", data, 0x3C)[0]
        difat0, ndifat = struct.unpack_from("<II", data, 0x44)
        fat_secs = [s for s in struct.unpack_from("<109I", data, 0x4C) if s not in (FREESECT, ENDOFCHAIN)]
        s = difat0
        per = self.ss // 4 - 1
        for _ in range(ndifat):
            if s in (FREESECT, ENDOFCHAIN):
                break
            vals = struct.unpack_from(f"<{per + 1}I", data, self._off(s))
            fat_secs += [v for v in vals[:per] if v not in (FREESECT, ENDOFCHAIN)]
            s = vals[per]
        fat_secs = fat_secs[:nfat]
        self.fat = []
        for fs in fat_secs:
            self.fat += struct.unpack_from(f"<{self.ss // 4}I", data, self._off(fs))
        self.minifat = []
        for ms in self._chain(minifat0):
            self.minifat += struct.unpack_from(f"<{self.ss // 4}I", data, self._off(ms))
        self.entries = []
        for ds in self._chain(self.dir0):
            o = self._off(ds)
            for k in range(self.ss // 128):
                self.entries.append(o + k * 128)
        self.root_chain = self._chain(struct.unpack_from("<I", data, self.entries[0] + 116)[0])

    def _off(self, sector: int) -> int:
        return (sector + 1) * self.ss

    def _chain(self, start: int, table=None) -> list[int]:
        table = self.fat if table is None else table
        out, s, seen = [], start, set()
        while s not in (FREESECT, ENDOFCHAIN) and s < len(table):
            if s in seen:
                raise ValueError("sector loop")
            seen.add(s)
            out.append(s)
            s = table[s]
        return out

    def _name(self, e: int) -> str:
        n = struct.unpack_from("<H", self.d, e + 64)[0]
        return bytes(self.d[e:e + max(0, n - 2)]).decode("utf-16-le", "replace")

    def _children(self, e: int) -> list[int]:
        """Directory entries in the red-black tree under storage entry e."""
        out, stack = [], [struct.unpack_from("<I", self.d, e + 76)[0]]
        while stack:
            i = stack.pop()
            if i == FREESECT or i >= len(self.entries):
                continue
            x = self.entries[i]
            out.append(x)
            stack += [struct.unpack_from("<I", self.d, x + 68)[0], struct.unpack_from("<I", self.d, x + 72)[0]]
        return out

    def find(self, path: str) -> int:
        e = self.entries[0]
        for part in path.split("/"):
            e = next((c for c in self._children(e) if self._name(c).lower() == part.lower()), None)
            if e is None:
                raise KeyError(path)
        return e

    def _spans(self, e: int) -> list[tuple[int, int]]:
        """(file offset, length) pieces of a stream's bytes."""
        start = struct.unpack_from("<I", self.d, e + 116)[0]
        size = struct.unpack_from("<I", self.d, e + 120)[0]
        spans = []
        if size < self.cutoff:
            for ms in self._chain(start, self.minifat):
                pos = ms * self.mss
                sec = self.root_chain[pos // self.ss]
                spans.append((self._off(sec) + pos % self.ss, self.mss))
        else:
            spans = [(self._off(s), self.ss) for s in self._chain(start)]
        out, left = [], size
        for o, n in spans:
            if left <= 0:
                break
            out.append((o, min(n, left)))
            left -= n
        if left > 0:
            raise ValueError("stream shorter than its size")
        return out

    def read(self, path: str) -> bytes:
        return b"".join(bytes(self.d[o:o + n]) for o, n in self._spans(self.find(path)))

    def write_same_size(self, path: str, data: bytes):
        spans = self._spans(self.find(path))
        if sum(n for _, n in spans) != len(data):
            raise ValueError("stream size would change")
        p = 0
        for o, n in spans:
            self.d[o:o + n] = data[p:p + n]
            p += n


def _records(data: bytes):
    out, p = [], 0
    while p < len(data):
        if p + 6 > len(data):
            raise ValueError("truncated rule record")
        kind, n = struct.unpack_from("<HI", data, p)
        if p + 6 + n > len(data):
            raise ValueError("rule record runs past the stream")
        out.append((kind, data[p + 6:p + 6 + n]))
        p += 6 + n
    return out


def _params(raw: bytes) -> list[tuple[str, str]]:
    txt = raw.rstrip(b"\x00").decode("latin-1")
    return [tuple(f.split("=", 1)) if "=" in f else (f, None) for f in txt.split("|") if f != ""]


def _join(pairs) -> bytes:
    return ("|" + "|".join(k if v is None else f"{k}={v}" for k, v in pairs)).encode("latin-1") + b"\x00"


def _mil(mm_value: float) -> str:
    return f"{mm_value / 0.0254:.4f}".rstrip("0").rstrip(".") + "mil"


def set_creepage(pcb: Path, rules: dict[str, tuple[float, str]], apply_to_pour: bool = False) -> list[str]:
    """rules: Altium rule name -> (distance mm, new comment). Returns the rule names written."""
    pcb = Path(pcb)
    data = bytearray(pcb.read_bytes())
    cfb = _Cfb(data)
    stream = cfb.read("Rules6/Data")
    recs = _records(stream)
    done, out = [], bytearray()
    for kind, raw in recs:
        pairs = _params(raw)
        d = {k.upper(): v for k, v in pairs}
        name = d.get("NAME")
        if (d.get("RULEKIND") or "").lower() == "creepage" and name in rules:
            dist, comment = rules[name]
            new = []
            for k, v in pairs:
                ku = k.upper()
                if ku == "CHECKDISTANCE":
                    v = _mil(dist)
                elif ku == "APPLYTOPOLYGONPOUR":
                    v = "TRUE" if apply_to_pour else "FALSE"
                elif ku == "COMMENT":
                    v = "\x00COMMENT\x00"            # placeholder, sized below
                new.append((k, v))
            if not any(k.upper() == "CHECKDISTANCE" for k, _ in new):
                raise ValueError(f"rule '{name}' has no CHECKDISTANCE")
            if not any(k.upper() == "COMMENT" for k, _ in new):
                raise ValueError(f"rule '{name}' has no COMMENT to absorb the length change")
            room = len(raw) - len(_join([(k, "" if v == "\x00COMMENT\x00" else v) for k, v in new]))
            if room < 0:
                raise ValueError(f"rule '{name}': not enough room in the record")
            text = comment[:room].ljust(room)
            new = [(k, text if v == "\x00COMMENT\x00" else v) for k, v in new]
            raw2 = _join(new)
            assert len(raw2) == len(raw)
            raw = raw2
            done.append(name)
        out += struct.pack("<HI", kind, len(raw)) + raw
    if not done:
        return []
    if len(out) != len(stream):
        raise ValueError("rules stream size changed")
    cfb.write_same_size("Rules6/Data", bytes(out))
    if cfb.read("Rules6/Data") != bytes(out):
        raise ValueError("read-back mismatch")
    tmp = pcb.with_suffix(pcb.suffix + ".convi_tmp")
    tmp.write_bytes(bytes(data))
    tmp.replace(pcb)
    return done


def read_creepage(pcb: Path) -> dict[str, dict]:
    """Altium rule name -> {CHECKDISTANCE, APPLYTOPOLYGONPOUR, COMMENT} of every creepage rule."""
    cfb = _Cfb(bytearray(Path(pcb).read_bytes()))
    res = {}
    for _, raw in _records(cfb.read("Rules6/Data")):
        d = {k.upper(): v for k, v in _params(raw)}
        if (d.get("RULEKIND") or "").lower() == "creepage":
            res[d.get("NAME")] = {k: d.get(k) for k in ("CHECKDISTANCE", "APPLYTOPOLYGONPOUR", "COMMENT")}
    return res
