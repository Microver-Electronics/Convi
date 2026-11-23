"""Convi command line.

  convi inspect <kicad project folder | .kicad_pro>
  convi convert <kicad project> [-o OUT] [--models DIR ...] [--map RAW=STEP ...] [--var NAME=PATH ...]
  convi gui

Workspace (see convi/layout.py): KiCad projects in <CONVI>/Projects, output by default in
<CONVI>/Outputs/convi_out/<project name> (an existing conversion is kept: _v2, _v3, ...).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .inspect import format_report, inspect_project
from .layout import default_model_dirs, default_out_dir
from .models3d import ModelResolver


def _resolver(args, folder: Path) -> ModelResolver:
    extra = {}
    for kv in args.var or []:
        if "=" in kv:
            k, v = kv.split("=", 1)
            extra[k.strip()] = v.strip()
    dirs = [Path(d) for d in args.models or []]
    if not args.no_auto_models:
        dirs += [d for d in default_model_dirs(folder) if d not in dirs]
    r = ModelResolver(folder, extra_vars=extra, search_dirs=dirs,
                      embed_dir=Path(args.out or default_out_dir(folder)).resolve() / "_convi" / "3D",
                      download=not args.offline)
    for kv in args.map or []:
        if "=" in kv:
            raw, step = kv.split("=", 1)
            r.set_manual(raw, Path(step))
    return r


def _ask_missing(ins, resolver: ModelResolver) -> bool:
    """Interactive: ask for STEP files / folders for missing models. Returns True if something changed."""
    if not ins.model_needs or not sys.stdin.isatty():
        return False
    print()
    print(f"{len(ins.model_needs)} 3D model(s) could not be found.")
    print("You can give a folder that contains STEP files (searched by file name),")
    print("a STEP file for each model, or press Enter to continue without them.")
    changed = False
    folder = input("Folder with STEP models (Enter to skip): ").strip().strip('"')
    if folder:
        resolver.search_dirs.append(Path(folder))
        resolver._index = None
        resolver.cache.clear()
        changed = True
    for n in ins.model_needs:
        ref = resolver.resolve(n.raw)
        if ref.step is not None:
            continue
        ans = input(f"STEP file for {Path(n.raw).name} ({', '.join(n.refs[:5])}) [Enter=skip]: ").strip().strip('"')
        if ans and Path(ans).is_file():
            resolver.set_manual(n.raw, Path(ans))
            changed = True
    return changed


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="convi", description="KiCad 10 to Altium Designer converter")
    ap.add_argument("--version", action="version", version=f"Convi {__version__}")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("inspect", "convert"):
        p = sub.add_parser(name)
        p.add_argument("project", help="KiCad project folder or .kicad_pro/.kicad_sch/.kicad_pcb")
        p.add_argument("--models", action="append", help="extra folder(s) searched for STEP models")
        p.add_argument("--no-auto-models", action="store_true",
                       help="do not search the project's own 3dmodels folders automatically")
        p.add_argument("--map", action="append", help="RAW_MODEL_PATH=STEP_FILE for a specific model")
        p.add_argument("--var", action="append", help="NAME=PATH to define a KiCad path variable")
        p.add_argument("--offline", action="store_true", help="do not fetch KiCad library 3D models")
        p.add_argument("-o", "--out", help="output folder (default: <CONVI>/Outputs/convi_out/<project>[_vN]; "
                                           "outside a CONVI workspace: <project>/Altium)")
        if name == "convert":
            p.add_argument("-y", "--yes", action="store_true", help="do not ask questions")
            p.add_argument("--global-labels", choices=["auto", "power", "port"], default="auto",
                           help="how KiCad global labels are converted (default auto: ports, or power "
                                "ports when the design uses hierarchical sheet pins)")
            p.add_argument("--sheet-template", default="auto", metavar="auto|none|CUSTOMER|FILE.SchDot",
                           help="customer sheet templates from the templates folder (default auto: the customer "
                                "named in the project name). Each sheet gets the smallest size it fits "
                                "on, from A3 up; a FILE.SchDot sets the customer and the smallest size")
            p.add_argument("--altium", help="path to Altium X2.EXE")
            p.add_argument("--no-teardrops", action="store_true", help="skip KiCad teardrop areas")
            p.add_argument("--placeholder-bodies", action="store_true",
                           help="grey box bodies for parts whose 3D model is missing")
            p.add_argument("--outputs", nargs="?", const="all", metavar="all|NAME,NAME",
                           help="also generate the manufacturing outputs of your OutJob template "
                                "(all containers, or the named ones, e.g. GERBER,BOM); see --list-outputs")
            p.add_argument("--list-outputs", action="store_true",
                           help="list the OutJob template's output containers and exit")
    sub.add_parser("gui")
    args = ap.parse_args(argv)
    if args.cmd in (None, "gui"):
        from .gui import run_gui
        return run_gui()
    folder = Path(args.project).resolve()
    folder = folder if folder.is_dir() else folder.parent
    if args.cmd == "convert" and args.list_outputs:
        from .outputs import find_output_templates, list_template_containers
        tpl = find_output_templates(folder)
        if tpl.outjob is None:
            print("No OutJob template found. Looked in: " + ", ".join(str(d) for d in tpl.searched))
            return 1
        print(f"OutJob template   : {tpl.outjob}")
        print(f"Draftsman template: {tpl.draftsman or '-'}")
        for c in list_template_containers(tpl):
            print(f"  {c.label}")
        return 0
    if not args.out:
        args.out = str(default_out_dir(folder))
    resolver = _resolver(args, folder)
    print(f"Convi {__version__} - inspecting {args.project}")
    if args.cmd == "convert":
        print(f"Output folder  : {args.out}")
    for d in resolver.search_dirs:
        if d != resolver.project_dir:
            print(f"3D model folder: {d}")
    ins = inspect_project(Path(args.project), resolver, progress=lambda m: print("  " + m, end="\r"))
    print(" " * 70, end="\r")
    print(format_report(ins))
    if args.cmd == "inspect":
        return 0
    if ins.blocking:
        print("\nCannot convert: fix the blocking problems above.")
        return 2
    if not args.yes and _ask_missing(ins, resolver):
        ins = inspect_project(Path(args.project), resolver)
        print()
        print(format_report(ins))
    from .pipeline import ConvertOptions, convert
    opts = ConvertOptions(global_labels=args.global_labels, x2=args.altium,
                          sheet_template=args.sheet_template,
                          teardrops="skip" if args.no_teardrops else "regions",
                          placeholder_bodies=args.placeholder_bodies,
                          make_outputs=bool(args.outputs),
                          outputs=None if args.outputs in (None, "all") else
                          [x.strip() for x in args.outputs.split(",") if x.strip()])
    print("\nConverting (Altium Designer will be used to write the files)...")
    res = convert(ins, Path(args.out), resolver, opts, progress=lambda m: print("  " + m.ljust(70), end="\r"))
    print(" " * 80, end="\r")
    print(f"\nDone in {res.seconds:.0f} s -> {res.project}")
    for i in res.issues.items:
        if i.level in ("error", "warning"):
            print(f"  [{i.level}] {i.message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
