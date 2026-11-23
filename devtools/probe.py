"""Run a DelphiScript probe body inside Altium and print its log.

The probe file must define `procedure Body;`.  A prelude providing
`L(msg)` (logging) and a `Main` wrapper is prepended automatically.

Path tokens in a probe body are replaced before the run (no machine-specific paths in the probes):
  %ROOT%  this tool folder            (<CONVI>/Tool/Convi)
  %OUT%   development output folder   (<CONVI>/Outputs/convi_out/_dev)
  %WS%    CONVI workspace root        (<CONVI>)
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from convi.altium.runner import run_script, AltiumTimeout  # noqa: E402
from convi.altium import dialogs as altium_dlg  # noqa: E402
from convi.layout import TOOL_DIR, dev_out_dir, find_workspace  # noqa: E402

OUT = Path(__file__).resolve().parent / "_probe_run"
OUT.mkdir(exist_ok=True)

PRELUDE = r"""
var GLog: TStringList;
var GLogPath: String;

procedure L(S: String);
begin
  GLog.Add(S);
  GLog.SaveToFile(GLogPath);
end;
"""

MAIN = r"""
procedure Main;
var Done: TStringList;
begin
  GLog := TStringList.Create;
  GLogPath := '%LOG%';
  try
    Body;
  except
    L('EXCEPTION: ' + ExceptionMessage);
  end;
  L('END');
  Done := TStringList.Create;
  Done.Add('done');
  Done.SaveToFile('%DONE%');
end;
"""


def expand_tokens(text: str) -> str:
    ws = find_workspace() or TOOL_DIR
    return (text.replace("%ROOT%", str(TOOL_DIR)).replace("%OUT%", str(dev_out_dir()))
            .replace("%WS%", str(ws)))


def main():
    src = Path(sys.argv[1])
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 60
    log = OUT / (src.stem + ".log")
    done = OUT / (src.stem + ".done")
    for p in (log, done):
        if p.exists():
            p.unlink()
    body = expand_tokens(src.read_text(encoding="utf-8"))
    script = OUT / (src.stem + "_run.pas")
    script.write_text(PRELUDE + body + MAIN.replace("%LOG%", str(log)).replace("%DONE%", str(done)),
                      encoding="utf-8")
    t0 = time.time()
    try:
        run_script(script, "Main", done, timeout=timeout)
        print(f"[finished in {time.time()-t0:.1f}s]")
    except AltiumTimeout:
        print("[TIMEOUT] dialogs:")
        for d in altium_dlg.dialogs():
            if not altium_dlg.is_main(d):
                print("  ", altium_dlg.describe(d))
        if "--noclose" not in sys.argv:
            for c in altium_dlg.close_dialogs():
                print("  closed:", c)
    if log.exists():
        print(log.read_text(encoding="utf-8", errors="replace"))


if __name__ == "__main__":
    main()
