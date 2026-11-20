"""Dev: render KiCad copper fill + tracks + pads of one layer around a pad (PNG)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw  # noqa: E402
from convi.geom import rot  # noqa: E402
from convi.kicad.pcb import parse_board  # noqa: E402

pcb, ref, num, layer = sys.argv[1:5]
out = sys.argv[5] if len(sys.argv) > 5 else "devtools/_probe_run/kicad_fill.png"
b = parse_board(pcb)
fp0 = next(f for f in b.footprints if f.ref == ref)
cx, cy = next(p for p in fp0.pads if p.number == num).pos
S, W = int(sys.argv[6]) if len(sys.argv) > 6 else 120, 1000
ox, oy = cx - W / 2 / S, cy - W / 2 / S
T = lambda p: ((p[0] - ox) * S, (p[1] - oy) * S)  # noqa: E731
im = Image.new("RGB", (W, W), "black")
d = ImageDraw.Draw(im)
for z in b.zones:
    for p in z.filled_polys.get(layer, []):
        d.polygon([T(q) for q in p], fill=(40, 80, 255) if z.net == "GND" else (0, 150, 0))
for t in b.tracks:
    if t.layer == layer:
        d.line([T(t.start), T(t.end)], fill=(255, 0, 255), width=max(1, int(t.width * S)))
for fp in b.footprints:
    for p in fp.pads:
        if layer not in p.layers and "*.Cu" not in p.layers:
            continue
        w, h = p.size
        pts = [rot(sx * w / 2, sy * h / 2, p.angle) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        d.polygon([T((p.pos[0] + q[0], p.pos[1] + q[1])) for q in pts], outline=(255, 255, 255),
                  fill=(0, 255, 255) if p.net == "GND" else (255, 140, 0))
        d.text(T(p.pos), f"{fp.ref}-{p.number}", fill="white")
im.save(out)
print(out)
