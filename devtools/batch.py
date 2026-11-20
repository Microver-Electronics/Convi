"""Dev: convert many KiCad demo projects and summarise the results."""
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from convi.inspect import inspect_project  # noqa: E402
from convi.models3d import ModelResolver  # noqa: E402
from convi.pipeline import ConvertOptions, convert  # noqa: E402
from convi.layout import dev_out_dir  # noqa: E402

DEMOS = ROOT / "testdata/kicad-src/demos"
names = sys.argv[1:] or sorted(p.name for p in DEMOS.iterdir() if p.is_dir() and any(p.glob("*.kicad_pro")))
DEV_OUT = dev_out_dir()
DEV_OUT.mkdir(parents=True, exist_ok=True)
summary = DEV_OUT / "batch_summary.txt"
with open(summary, "a", encoding="utf-8") as out:
    for n in names:
        folder = DEMOS / n
        pros = sorted(folder.glob("*.kicad_pro"))
        for pro in pros[:1]:
            t = time.time()
            try:
                dst = DEV_OUT / "batch" / pro.stem
                res = ModelResolver(folder, embed_dir=dst / "_convi" / "3D")
                ins = inspect_project(pro, res)
                r = convert(ins, dst, res, ConvertOptions(open_in_altium=False))
                errs = [i.message for i in r.issues.items if i.level in ("error", "warning")]
                line = (f"{pro.stem}: {time.time()-t:.0f}s | {r.stats.get('connectivity')} | "
                        f"{r.stats.get('eco_preview')} | {r.stats.get('pcb_check')} | bodies {r.stats.get('bodies')}/"
                        f"{r.stats.get('bodies', 0) + r.stats.get('bodies_missing', 0)} | issues: {errs[:6]}")
            except Exception as e:
                line = f"{pro.stem}: FAILED {e!r}\n{traceback.format_exc()}"
            print(line, flush=True)
            out.write(line + "\n")
            out.flush()
