"""Dev: open a document in Altium, zoom to fit, capture the Altium window.

  py devtools/show.py <file.SchDoc|PcbDoc|SchLib|PcbLib> [out.png] [--3d]
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from convi.altium import dialogs  # noqa: E402

doc = str(Path(sys.argv[1]).resolve())
out = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else str(ROOT / "devtools/_probe_run/alt.png")
kind = {".schdoc": "SCH", ".pcbdoc": "PCB", ".schlib": "SCHLIB", ".pcblib": "PCBLIB"}[Path(doc).suffix.lower()]
zoomproc = "Sch:Zoom" if kind.startswith("SCH") else "PCB:Zoom"
action = "Document" if kind == "SCH" else "All"
body = f"""procedure Body;
var D: Variant;
begin
  D := Client.OpenDocument('{kind}', '{doc}');
  Client.ShowDocument(D);
  ResetParameters; AddStringParameter('Action', '{action}'); RunProcess('{zoomproc}');
  L('shown');
end;
"""
probe = ROOT / "devtools/probes/_show.pas"
probe.write_text(body, encoding="utf-8")
subprocess.run([sys.executable, str(ROOT / "devtools/probe.py"), str(probe), "30"], capture_output=True)
time.sleep(1.5)
if "--3d" in sys.argv:
    for h in dialogs.dialogs():
        if dialogs._cls(h) == "TDocumentForm":
            for c in dialogs.children(h):
                if dialogs._cls(c) == "TPCBView_GraphicalForm":
                    dialogs.user32.PostMessageW(c, 0x0100, 0x33, 0x00040001)
                    dialogs.user32.PostMessageW(c, 0x0101, 0x33, 0xC0040001)
                    break
    time.sleep(5)
mains = [h for h in dialogs.dialogs() if dialogs._cls(h) == "TDocumentForm"]
print("saved" if mains and dialogs.capture_window(mains[0], out) else "capture failed", out)
