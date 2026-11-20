"""Dev: convert a KiCad schematic hierarchy to SchDocs + SchLib in Altium."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from convi.altium.build import run_builder  # noqa: E402
from convi.layout import dev_out_dir  # noqa: E402
from convi.altium.ir import IRWriter  # noqa: E402
from convi.convert.sch_convert import SchConverter  # noqa: E402
from convi.kicad.common import Issues  # noqa: E402
from convi.kicad.project import discover, load_settings  # noqa: E402

src = Path(sys.argv[1]).resolve()
pf = discover(src)
out = dev_out_dir() / pf.root_sch.stem
out.mkdir(parents=True, exist_ok=True)
iss = Issues()
conv = SchConverter(pf.root_sch, iss, project=load_settings(pf.pro), lib_name=f"{pf.root_sch.stem}.SchLib",
                    pcblib_name=f"{pf.root_sch.stem}.PcbLib")
conv.load()
ir = IRWriter()
for inst in conv.instances:
    conv.build_schdoc(ir, inst, out / inst.out_name)
ir.save(out / "sch.cvi")
lib = IRWriter()
conv.build_schlib(lib, out / f"{pf.root_sch.stem}.SchLib")
lib.save(out / "schlib.cvi")
print("sheets:", [i.out_name for i in conv.instances], "symbols:", len(conv.symdefs), "IR", len(ir), len(lib))
for i in iss.items[:20]:
    print("  ", i.level, i.area, i.message)
for name in ("schlib", "sch"):
    r = run_builder("builder_sch.pas", out / f"{name}.cvi", out / "_run", timeout=3600)
    print(f"--- {name}: {r.seconds:.1f}s errors={r.errors} stats={r.stats}")
    for ln in r.log.splitlines()[:40]:
        print("   ", ln)
