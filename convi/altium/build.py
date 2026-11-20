"""Run a Convi builder script on an IR file inside Altium and collect the log."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from .runner import run_script

HERE = Path(__file__).resolve().parent


@dataclass
class BuildResult:
    log: str
    errors: int
    stats: dict
    seconds: float


def run_builder(template: str, ir_path: Path, work_dir: Path, timeout: float = 7200, x2=None,
                progress=None) -> BuildResult:
    ir_path = Path(ir_path).resolve()
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    stem = ir_path.stem
    log = work_dir / f"{stem}.log"
    done = work_dir / f"{stem}.done"
    prog = work_dir / f"{stem}.progress"
    started = work_dir / f"{stem}.started"
    for p in (log, done, prog):
        if p.exists():
            p.unlink()
    parts = ("builder_common_head.pas", template, "builder_common_main.pas")
    src = "\n".join((HERE / f).read_text(encoding="utf-8") for f in parts)
    src = (src.replace("%IR%", str(ir_path)).replace("%LOG%", str(log))
           .replace("%DONE%", str(done)).replace("%PROGRESS%", str(prog))
           .replace("%STARTED%", str(started)))
    script = work_dir / f"{stem}_builder.pas"
    script.write_text(src, encoding="utf-8")

    def on_poll(elapsed):
        if progress and prog.exists():
            try:
                progress(prog.read_text(encoding="utf-8", errors="ignore").strip(), elapsed)
            except OSError:
                pass

    t0 = time.time()
    run_script(script, "Main", done, timeout=timeout, x2=x2, on_poll=on_poll, started=started)
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    errors = 0
    stats = {}
    for ln in text.splitlines():
        if ln.startswith("errors: "):
            errors = int(ln.split(":")[1])
        elif ln.startswith("stats: "):
            for kv in ln[7:].split(","):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    stats[k.strip('"')] = int(v.strip('"')) if v.strip('"').isdigit() else v
    return BuildResult(log=text, errors=errors, stats=stats, seconds=time.time() - t0)
