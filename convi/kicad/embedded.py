"""KiCad 9+ embedded files: (embedded_files (file (name ..) (type ..) (data |zstd+base64|) (checksum ..)))."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

from .. import sexpr as S


@dataclass
class EmbeddedFile:
    name: str
    type: str
    data_b64: str
    checksum: str = ""

    @property
    def has_data(self) -> bool:
        return bool(self.data_b64)

    def decode(self) -> bytes:
        """Return the decompressed file content."""
        raw = base64.b64decode(self.data_b64)
        if raw[:4] == b"\x28\xb5\x2f\xfd":
            try:
                import zstandard
            except ImportError as e:  # pragma: no cover
                raise RuntimeError("Python package 'zstandard' is required to extract embedded files "
                                   "(pip install zstandard)") from e
            return zstandard.ZstdDecompressor().decompress(raw, max_output_size=1 << 31)
        return raw

    def extract(self, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / Path(self.name).name
        out.write_bytes(self.decode())
        return out


def parse_embedded_files(node) -> dict[str, EmbeddedFile]:
    out: dict[str, EmbeddedFile] = {}
    for f in S.findall(node, "file"):
        name = str(S.val(f, "name", ""))
        data = S.find(f, "data")
        blob = ""
        if data is not None and len(data) > 1:
            blob = "".join(str(x) for x in data[1:] if not isinstance(x, list))
        out[name] = EmbeddedFile(name=name, type=str(S.val(f, "type", "")), data_b64=blob,
                                 checksum=str(S.val(f, "checksum", "")))
    return out
