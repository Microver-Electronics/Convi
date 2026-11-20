"""Dev: convert one KiCad board to PcbDoc + PcbLib in Altium and print the builder log."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from convi.altium.build import run_builder  # noqa: E402
from convi.layout import dev_out_dir  # noqa: E402
from convi.altium.ir import IRWriter  # noqa: E402
from convi.convert.pcb_convert import PcbConverter  # noqa: E402
from convi.kicad.common import Issues  # noqa: E402
from convi.kicad.pcb import parse_board  # noqa: E402
from convi.models3d import ModelResolver  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from convi.altium import dialogs as altium_dlg  # noqa: E402

src = Path(sys.argv[1]).resolve()
what = sys.argv[2] if len(sys.argv) > 2 else "both"
out = dev_out_dir() / src.stem
out.mkdir(parents=True, exist_ok=True)
iss = Issues()
b = parse_board(src, iss)
res = ModelResolver(src.parent, embed_dir=out / "3D")
from convi.kicad.project import discover, load_settings  # noqa: E402
prj = load_settings(discover(src).pro)
conv = PcbConverter(b, res, iss, lib_name=f"{src.stem}.PcbLib", project=prj)
ir = IRWriter()
conv.build_pcb(ir, out / f"{src.stem}.PcbDoc")
mech = IRWriter()
conv.emit_mech(mech)
# mech layers must exist before geometry: insert after LAYERS/CU/DIEL block
idx = next(i for i, l in enumerate(ir.lines) if l.startswith("NET\t") or l.startswith("OUTLINE"))
ir.lines[idx:idx] = mech.lines
ir.save(out / "pcb.cvi")
lib = IRWriter()
conv.build_pcblib(lib, out / f"{src.stem}.PcbLib")
lib.lines[2:2] = mech.lines
lib.save(out / "lib.cvi")
print("IR:", len(ir), "lines; lib", len(lib), "lines; issues:", len(iss.items))
for i in iss.items[:15]:
    print("  ", i.level, i.area, i.message)
for name in (["pcb", "lib"] if what == "both" else [what]):
    t = time.time()
    try:
        r = run_builder("builder_pcb.pas", out / f"{name}.cvi", out / "_run", timeout=1800,
                        progress=lambda p, e: None)
    except Exception as e:
        print("RUN FAILED:", e)
        for d in altium_dlg.dialogs():
            if not altium_dlg.is_main(d):
                print("  dialog:", altium_dlg.describe(d))
        sys.exit(1)
    print(f"--- {name}: {r.seconds:.1f}s errors={r.errors} stats={r.stats}")
    lines = r.log.splitlines()
    for ln in lines[:60]:
        print("   ", ln)
