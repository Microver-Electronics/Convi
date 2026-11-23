"""Convi wizard (CustomTkinter, dark theme): project -> check -> options -> convert -> result.

All conversion logic stays in inspect.py / pipeline.py; this module only collects choices and shows
progress.  gui.run_gui() starts it, or the classic wizard (gui_classic) when CustomTkinter is missing.
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
from PIL import Image, ImageDraw

from . import __version__
from .inspect import Inspection, format_report, inspect_project
from .layout import default_model_dirs, default_out_dir, find_workspace, outputs_dir, project_root
from .models3d import ModelResolver

ASSETS = Path(__file__).resolve().parent / "assets"
TPL_AUTO = "Automatic (customer from the project name)"
TPL_NONE = "None (keep the KiCad paper size)"

# ---------------------------------------------------------------------------------------------- theme
BG = "#0E0F11"
SIDE = "#121317"
CARD = "#17191D"
CARD2 = "#141519"
LOGBG = "#0A0B0D"
LINE = "#262A31"
LINE2 = "#343941"
BTN = "#1E2026"
BTN_H = "#2A2D34"
TXT = "#F3F4F6"
TXT2 = "#C9CDD3"
MUTED = "#A1A7B0"
DIM = "#8B919B"
ORANGE = "#FF8A1F"
ORANGE_H = "#FFA552"
ORANGE_BG = "#1F1A14"
ORANGE_LINE = "#7A4A1C"
GREEN = "#3DD68C"
GREEN_BG = "#1C2A22"
AMBER = "#FFB15C"
AMBER_BG = "#2E2416"
BLUE = "#4EA1FF"
RED = "#FF7A7A"
RED_BG = "#2E1A1A"

UI_FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas"

STEPS = ("Project", "Check", "Options & outputs", "Convert", "Result")

# conversion stages, keyed by words of the pipeline's progress messages
STAGES = (
    ("sch", "Schematics", ("schematic", "SchLib")),
    ("pcb", "Board", ("Converting board", "PcbLib", "PcbDoc", "pouring", "via pad shapes")),
    ("check", "Checks: nets, ECO, BOM, DRC", ("Preparing manufacturing outputs", "verifying", "checking placement",
                                             "open connections")),
    ("out", "Manufacturing outputs", ("generating manufacturing outputs",)),
    ("open", "Open in Altium", ()),
)


def F(size=13, weight="normal", family=None):
    return ctk.CTkFont(family=family or UI_FAMILY, size=size, weight=weight)


def open_path(p: Path | str):
    """Open a file or folder with its Windows application (Explorer, Altium for .PrjPcb ...)."""
    p = str(p)
    if hasattr(os, "startfile"):
        os.startfile(p)
    else:   # development machines
        subprocess.Popen(["xdg-open", p])


def fmt_secs(s: float) -> str:
    s = int(round(s))
    return f"{s // 60}:{s % 60:02d}"


# ---------------------------------------------------------------------------------------------- data helpers
def workspace_projects() -> list[dict]:
    """KiCad projects in <CONVI>/Projects, newest conversion first.

    The last conversion comes from the convi_report.json files that record their source folder (written
    since the new wizard); older reports cannot be matched to a project reliably and are not used."""
    ws = find_workspace()
    if ws is None or not (ws / "Projects").is_dir():
        return []
    projects = {}
    for p in sorted((ws / "Projects").iterdir()):
        if not p.is_dir() or p.name.startswith((".", "_")):
            continue
        pro = next(iter(sorted(p.glob("*.kicad_pro"))), None) or next(iter(sorted(p.glob("*/*.kicad_pro"))), None)
        projects[p.name] = {"name": p.name, "path": p, "pro": pro, "last": None, "mtime": p.stat().st_mtime}
    out = outputs_dir(ws)
    if out and out.is_dir():
        for d in out.iterdir():
            rep = d / "convi_report.json"
            if not rep.is_file():
                continue
            try:
                with rep.open(encoding="utf-8", errors="replace") as fh:
                    head = fh.read(65536)
            except OSError:
                continue
            m = re.search(r'"source":\s*"((?:[^"\\]|\\.)*)"', head)
            if not m:
                continue
            try:
                name = project_root(Path(json.loads(f'"{m.group(1)}"')), ws).name
            except Exception:
                continue
            if name not in projects:
                continue
            when = rep.stat().st_mtime
            cur = projects[name]["last"]
            if cur and cur["when"] >= when:
                continue
            om = re.search(r"Manufacturing outputs: (\d+) of (\d+) container", head)
            projects[name]["last"] = {"when": when, "out": d,
                                      "outputs": f"{om.group(1)}/{om.group(2)} outputs" if om else ""}
    return sorted(projects.values(), key=lambda p: (-(p["last"]["when"] if p["last"] else 0), -p["mtime"]))


def parse_metrics(stats: dict) -> list[tuple[str, int, int | None, bool]]:
    """(label, value, total, ok) tiles from the pipeline's summary strings; unknown formats are skipped."""
    tiles = []

    def get(key):
        v = stats.get(key)
        return v if isinstance(v, str) else ""

    con = get("connectivity")
    for label, pat in (("Nets matched", r"nets matched (\d+)/(\d+)"), ("Pins matched", r"pins matched (\d+)/(\d+)")):
        m = re.search(pat, con)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            tiles.append((label, a, b, a == b))
    m = re.search(r"pads (\d+)/(\d+) at exact position", get("pcb_check"))
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        tiles.append(("Pads in place", a, b, a == b))
    eco = get("eco_preview")
    nums = re.findall(r"(?:add|remove|re-link|footprint|pad-net) (\d+)", eco)
    if nums:
        n = sum(int(x) for x in nums)
        tiles.append(("ECO changes", n, None, n == 0))
    m = re.search(r"BOM: (\d+)/(\d+)", get("bom"))
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        tiles.append(("BOM identical", a, b, a == b))
    m = re.search(r"(\d+) of (\d+) container\(s\) generated, (\d+) file", get("outputs"))
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        tiles.append((f"Outputs · {m.group(3)} files", a, b, a == b))
    return tiles


def circle_image(kind: str, text: str = "", size: int = 26) -> ctk.CTkImage:
    """Step badges drawn with Pillow (crisp at any DPI): active / done / todo / error / skip."""
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fill, ring, ink = {
        "active": (ORANGE, None, "#111111"), "done": ("#23332B", None, GREEN), "todo": (None, "#3A3F48", MUTED),
        "error": (RED_BG, None, RED), "skip": (None, "#2C3038", "#5C626C"), "run": (None, ORANGE, ORANGE),
    }[kind]
    if fill:
        d.ellipse((0, 0, s - 1, s - 1), fill=fill)
    if ring:
        d.ellipse((4, 4, s - 5, s - 5), outline=ring, width=6 if kind == "run" else 5)
    w = s // 10
    if kind == "done":
        d.line([(s * .28, s * .52), (s * .44, s * .68), (s * .73, s * .34)], fill=ink, width=w, joint="curve")
    elif kind == "error":
        d.line([(s * .33, s * .33), (s * .67, s * .67)], fill=ink, width=w)
        d.line([(s * .67, s * .33), (s * .33, s * .67)], fill=ink, width=w)
    elif kind == "run":
        d.ellipse((s * .36, s * .36, s * .64, s * .64), fill=ink)
    elif kind == "skip":
        d.line([(s * .34, s * .5), (s * .66, s * .5)], fill=ink, width=w)
    elif text:
        from PIL import ImageFont
        font = None
        for name in ("segoeuib.ttf", "seguisb.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
            try:
                font = ImageFont.truetype(name, int(s * .5))
                break
            except OSError:
                continue
        font = font or ImageFont.load_default()
        d.text((s / 2, s / 2), text, fill=ink, font=font, anchor="mm")
    img = img.resize((size * 2, size * 2), Image.LANCZOS)
    return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))


def rounded(img: Image.Image, radius: int) -> Image.Image:
    img = img.convert("RGBA")
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=radius, fill=255)
    img.putalpha(mask)
    return img


# ---------------------------------------------------------------------------------------------- widgets
def card(parent, **kw):
    return ctk.CTkFrame(parent, fg_color=kw.pop("fg_color", CARD), border_color=kw.pop("border_color", LINE),
                        border_width=1, corner_radius=14, **kw)


def primary(parent, text, command, **kw):
    return ctk.CTkButton(parent, text=text, command=command, fg_color=ORANGE, hover_color=ORANGE_H,
                         text_color="#111111", text_color_disabled="#5A4A3A", font=F(14, "bold"),
                         height=kw.pop("height", 44), corner_radius=12, **kw)


def secondary(parent, text, command, **kw):
    return ctk.CTkButton(parent, text=text, command=command, fg_color=BTN, hover_color=BTN_H, border_color=LINE2,
                         border_width=1, text_color=TXT, text_color_disabled=DIM, font=F(13),
                         height=kw.pop("height", 40), corner_radius=10, **kw)


def label(parent, text="", size=13, color=TXT, weight="normal", **kw):
    return ctk.CTkLabel(parent, text=text, font=F(size, weight, kw.pop("family", None)), text_color=color,
                        anchor=kw.pop("anchor", "w"), justify=kw.pop("justify", "left"), **kw)


def switch(parent, text, variable, command=None):
    return ctk.CTkSwitch(parent, text=text, variable=variable, onvalue=True, offvalue=False, command=command,
                         font=F(13), text_color=TXT, progress_color=ORANGE, fg_color=LINE2,
                         button_color="#E6E8EB", button_hover_color="#FFFFFF", switch_width=42, switch_height=22)


def page_header(parent, step, title, subtitle=""):
    box = ctk.CTkFrame(parent, fg_color="transparent")
    label(box, f"STEP {step} OF 5", 11, ORANGE, "bold").pack(anchor="w")
    t = label(box, title, 26, TXT, "bold")
    t.pack(anchor="w", pady=(2, 0))
    s = label(box, subtitle, 13, MUTED, wraplength=820)
    if subtitle:
        s.pack(anchor="w", pady=(4, 0))
    box.title_label, box.subtitle_label = t, s
    return box


class Tile(ctk.CTkFrame):
    def __init__(self, parent, title, value="", total=None, color=TXT):
        super().__init__(parent, fg_color=CARD, border_color=LINE, border_width=1, corner_radius=12)
        label(self, title, 12, MUTED).pack(anchor="w", padx=16, pady=(12, 0))
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(anchor="w", padx=16, pady=(0, 12))
        label(row, str(value), 24, color, "bold", height=34).pack(side="left")
        if total is not None:
            label(row, f" / {total}", 15, DIM, height=34).pack(side="left", pady=(6, 0))


class StepItem(ctk.CTkFrame):
    """One step in the sidebar: badge + name; clickable when the step has been reached."""

    def __init__(self, parent, index, name, on_click):
        super().__init__(parent, fg_color="transparent", corner_radius=10, height=44)
        self.index, self.on_click, self.enabled, self.state = index, on_click, False, "todo"
        self.badge = ctk.CTkLabel(self, text="", width=26)
        self.badge.pack(side="left", padx=(12, 12), pady=9)
        self.text = label(self, name, 14, MUTED)
        self.text.pack(side="left", fill="x", expand=True)
        for w in (self, self.badge, self.text):
            w.bind("<Button-1>", lambda e: self.enabled and self.on_click(self.index))
            w.bind("<Enter>", lambda e: self._hover(True))
            w.bind("<Leave>", lambda e: self._hover(False))
        self.images = {k: circle_image(k, str(index + 1)) for k in ("active", "done", "todo", "error")}

    def set(self, state, enabled):
        self.state, self.enabled = state, enabled
        self.badge.configure(image=self.images[state])
        active = state == "active"
        self.configure(fg_color=BTN if active else "transparent")
        self.text.configure(text_color=TXT if active else (TXT2 if state == "done" else MUTED),
                            font=F(14, "bold" if active else "normal"))
        self.configure(cursor="hand2" if enabled and not active else "")

    def _hover(self, inside):
        if self.state != "active":
            self.configure(fg_color="#191B20" if inside and self.enabled else "transparent")


# ---------------------------------------------------------------------------------------------- application
class App(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode("dark")
        # no withdraw() here: CustomTkinter on Windows keeps the window hidden until mainloop() and never
        # shows it again if withdraw() was called before (the window stayed invisible, c91)
        super().__init__(fg_color=BG)
        self.title(f"Convi {__version__} - KiCad to Altium Designer")
        self.geometry("1200x820")
        self.minsize(1020, 720)
        self._set_icon()
        self.q: queue.Queue = queue.Queue()
        self.ins: Inspection | None = None
        self.resolver: ModelResolver | None = None
        self.result = None
        self.search_dirs: list[Path] = []
        self.extra_vars: dict[str, str] = {}
        self.manual: dict[str, Path] = {}
        self.project_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Choose a KiCad 10 project.")
        self.global_labels = tk.StringVar(value="auto")
        self.teardrops = tk.BooleanVar(value=True)
        self.open_after = tk.BooleanVar(value=True)
        self.download = tk.BooleanVar(value=True)
        self.placeholders = tk.BooleanVar(value=False)
        self.sheet_template = tk.StringVar(value=TPL_AUTO)
        self.make_outputs = tk.BooleanVar(value=False)
        self.output_vars: dict[str, tk.BooleanVar] = {}
        self._tpl_paths: dict[str, Path] = {}
        self.busy = False
        self._conv_failed = False
        self._last_project = ""
        self.step = 0
        self.reached = 0
        threading.Thread(target=self._scan_projects, daemon=True).start()
        self._style_ttk()
        self._build()
        self.show(0)
        self.after(100, self._poll)

    def _set_icon(self):
        try:
            self._icon_photo = tk.PhotoImage(file=str(ASSETS / "convi_logo.png"))
            self.iconphoto(True, self._icon_photo)
        except Exception:
            pass
        if sys.platform == "win32":   # set now, so CustomTkinter does not put its own icon on the window
            try:
                self.iconbitmap(str(ASSETS / "convi.ico"))
            except Exception:
                pass

    def _style_ttk(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure("Convi.Treeview", background=CARD2, fieldbackground=CARD2, foreground=TXT2, rowheight=30,
                     borderwidth=0, font=(UI_FAMILY, 10))
        st.configure("Convi.Treeview.Heading", background="#1A1C21", foreground=MUTED, relief="flat",
                     borderwidth=0, font=(UI_FAMILY, 10))
        st.map("Convi.Treeview", background=[("selected", "#3A2A18")], foreground=[("selected", TXT)])
        st.map("Convi.Treeview.Heading", background=[("active", "#22252B")])
        st.layout("Convi.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    # ------------------------------------------------------------------ layout
    def _build(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        body = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        body.grid(row=0, column=1, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        # pages are built when first needed (CustomTkinter widgets are slow to create): only the
        # project page at start; check and options while the inspection runs; convert/result on demand
        self._body = body
        self._builders = (self._page_project, self._page_check, self._page_options, self._page_convert,
                          self._page_result)
        self.pages: list = [None] * len(self._builders)
        self._ensure(0)

    def _ensure(self, i):
        if self.pages[i] is None:
            pg = ctk.CTkFrame(self._body, fg_color=BG, corner_radius=0)
            pg.grid(row=0, column=0, sticky="nsew", padx=36, pady=(28, 24))
            self._builders[i](pg)
            self.pages[i] = pg
            if self.pages[self.step] is not None and self.step != i:
                self.pages[self.step].tkraise()   # building must not cover the page on screen
        return self.pages[i]

    def _prebuild(self, *pages):
        """Build pages one per idle moment so the window stays responsive."""
        todo = [i for i in pages if self.pages[i] is None]
        if todo:
            self._ensure(todo[0])
            if todo[1:]:
                self.after(30, lambda: self._prebuild(*todo[1:]))

    def _build_sidebar(self):
        side = ctk.CTkFrame(self, fg_color=SIDE, corner_radius=0, width=260, border_width=0)
        side.grid(row=0, column=0, sticky="nsw")
        side.grid_propagate(False)
        side.pack_propagate(False)
        brand = ctk.CTkFrame(side, fg_color="transparent")
        brand.pack(fill="x", padx=20, pady=(24, 26))
        try:
            logo = rounded(Image.open(ASSETS / "convi_logo.png"), 40)
            self._logo = ctk.CTkImage(light_image=logo, dark_image=logo, size=(46, 46))
            ctk.CTkLabel(brand, image=self._logo, text="").pack(side="left")
        except Exception:
            pass
        names = ctk.CTkFrame(brand, fg_color="transparent")
        names.pack(side="left", padx=(12, 0))
        label(names, "Convi", 22, TXT, "bold").pack(anchor="w")
        label(names, "KiCad 10 → Altium Designer", 11, DIM).pack(anchor="w")
        self.step_items = []
        for i, name in enumerate(STEPS):
            it = StepItem(side, i, name, self._nav)
            it.pack(fill="x", padx=14, pady=2)
            self.step_items.append(it)
        foot = ctk.CTkFrame(side, fg_color="transparent")
        foot.pack(side="bottom", fill="x", padx=16, pady=18)
        self.proj_chip = ctk.CTkFrame(foot, fg_color=CARD, border_color=LINE, border_width=1, corner_radius=10)
        label(self.proj_chip, "PROJECT", 10, DIM, "bold").pack(anchor="w", padx=12, pady=(10, 0))
        self.proj_chip_name = label(self.proj_chip, "", 13, TXT, "bold", wraplength=200)
        self.proj_chip_name.pack(anchor="w", padx=12, pady=(0, 10))
        status = ctk.CTkFrame(foot, fg_color=CARD, border_color=LINE, border_width=1, corner_radius=10)
        status.pack(fill="x", side="bottom")
        self.status_dot = ctk.CTkLabel(status, text="●", text_color=GREEN, font=F(12), width=14)
        self.status_dot.pack(side="left", padx=(12, 6), pady=10, anchor="n")
        label(status, textvariable=self.status_var, size=12, color=TXT2, wraplength=180).pack(
            side="left", fill="x", expand=True, padx=(0, 10), pady=10)
        label(foot, f"v{__version__}", 11, "#6E7580").pack(side="bottom", anchor="w", pady=(0, 8))

    def _set_project_chip(self):
        p = self.project_var.get().strip().strip('"')
        if p:
            self.proj_chip_name.configure(text=Path(p).stem if Path(p).suffix else Path(p).name)
            self.proj_chip.pack(fill="x", side="bottom", pady=(0, 10))
        else:
            self.proj_chip.pack_forget()

    def _status(self, text, kind="idle"):
        self.status_var.set(text)
        self.status_dot.configure(text_color={"idle": GREEN, "busy": ORANGE, "error": RED}[kind])

    def show(self, step):
        self.step = step
        self.reached = max(self.reached, step)
        self._ensure(step).tkraise()
        for it in self.step_items:
            i = it.index
            if i == step:
                state = "active"
            elif i == 3 and self._conv_failed:
                state = "error"
            elif i <= self.reached:
                state = "done"
            else:
                state = "todo"
            enabled = not self.busy and i <= self.reached and i != step and (i != 4 or self.result is not None)
            it.set(state, enabled)

    def _nav(self, i):
        if self.busy:
            return
        if i == 4 and self.result is None:
            return
        self.show(i)

    # ================================================================== page 1: project
    def _page_project(self, pg):
        pg.grid_columnconfigure(0, weight=1)
        pg.grid_rowconfigure(2, weight=1)
        page_header(pg, 1, "Choose a KiCad project",
                    "A project folder or its .kicad_pro. Convi reads everything first and lists what is missing "
                    "before it converts.").grid(row=0, column=0, sticky="ew")

        src = card(pg)
        src.grid(row=1, column=0, sticky="ew", pady=(20, 0))
        src.grid_columnconfigure(0, weight=1)
        label(src, "Source and output", 15, TXT, "bold").grid(row=0, column=0, sticky="w", padx=22, pady=(18, 10))
        label(src, "KiCad project", 12, MUTED).grid(row=1, column=0, sticky="w", padx=22)
        r1 = ctk.CTkFrame(src, fg_color="transparent")
        r1.grid(row=2, column=0, sticky="ew", padx=22, pady=(4, 12))
        r1.grid_columnconfigure(0, weight=1)
        self.proj_entry = ctk.CTkEntry(r1, textvariable=self.project_var, height=42, corner_radius=10,
                                       fg_color=BG, border_color=LINE2, text_color=TXT,
                                       font=F(12, family=MONO_FAMILY),
                                       placeholder_text="Folder or .kicad_pro of the KiCad 10 project")
        self.proj_entry.grid(row=0, column=0, sticky="ew")
        self.proj_entry.bind("<FocusOut>", lambda e: self._project_changed())
        secondary(r1, "Folder…", self._pick_folder, width=100, height=42).grid(row=0, column=1, padx=(8, 0))
        secondary(r1, ".kicad_pro…", self._pick_pro, width=110, height=42).grid(row=0, column=2, padx=(8, 0))
        label(src, "Output folder", 12, MUTED).grid(row=3, column=0, sticky="w", padx=22)
        r2 = ctk.CTkFrame(src, fg_color="transparent")
        r2.grid(row=4, column=0, sticky="ew", padx=22, pady=(4, 4))
        r2.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(r2, textvariable=self.out_var, height=42, corner_radius=10, fg_color=BG, border_color=LINE2,
                     text_color=TXT, font=F(12, family=MONO_FAMILY)).grid(row=0, column=0, sticky="ew")
        secondary(r2, "Browse…", self._pick_out, width=100, height=42).grid(row=0, column=1, padx=(8, 0))
        label(src, "A new folder for every conversion: an earlier conversion is never overwritten.", 12, DIM).grid(
            row=5, column=0, sticky="w", padx=22, pady=(0, 18))

        lst = card(pg)
        lst.grid(row=2, column=0, sticky="nsew", pady=(16, 0))
        lst.grid_columnconfigure(0, weight=1)
        lst.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(lst, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=22, pady=(16, 8))
        label(head, "Projects in the workspace", 15, TXT, "bold").pack(side="left")
        self.filter_entry = ctk.CTkEntry(head, width=220, height=34, corner_radius=9, fg_color=BG,
                                         border_color=LINE2, text_color=TXT, font=F(12),
                                         placeholder_text="Filter projects", placeholder_text_color=DIM)
        self.filter_entry.pack(side="right")
        self.filter_entry.bind("<KeyRelease>", self._filter_later)
        tv = ctk.CTkFrame(lst, fg_color=CARD2, corner_radius=10, border_color=LINE, border_width=1)
        tv.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 16))
        tv.grid_columnconfigure(0, weight=1)
        tv.grid_rowconfigure(0, weight=1)
        self.proj_tree = ttk.Treeview(tv, columns=("name", "pro", "last"), show="headings", selectmode="browse",
                                      style="Convi.Treeview")
        for c, w, t in (("name", 260, "Project"), ("pro", 260, "KiCad project file"),
                        ("last", 300, "Last conversion")):
            self.proj_tree.heading(c, text=t, anchor="w")
            self.proj_tree.column(c, width=w, stretch=True, anchor="w")
        self.proj_tree.tag_configure("dim", foreground=DIM)
        self.proj_tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        psb = ctk.CTkScrollbar(tv, command=self.proj_tree.yview, button_color=LINE2, button_hover_color="#4A505A")
        psb.grid(row=0, column=1, sticky="ns", pady=6)
        self.proj_tree.configure(yscrollcommand=psb.set)
        self.proj_tree.bind("<<TreeviewSelect>>", self._tree_pick)
        self.proj_tree.bind("<Double-Button-1>", lambda e: self._tree_pick() and self._inspect())
        self.proj_tree.insert("", "end", iid="_msg", values=("Reading the workspace…", "", ""), tags=("dim",))
        self.projects: list[dict] | None = None
        self._by_iid: dict = {}

        foot = ctk.CTkFrame(pg, fg_color="transparent")
        foot.grid(row=3, column=0, sticky="ew", pady=(16, 0))
        self.inspect_btn = primary(foot, "Inspect project  →", self._inspect, width=190)
        self.inspect_btn.pack(side="right")
        label(foot, "Nothing is written until the conversion starts.", 13, DIM).pack(side="right", padx=14)

    def _filter_later(self, event=None):
        if getattr(self, "_filter_job", None):
            self.after_cancel(self._filter_job)
        self._filter_job = self.after(150, self._fill_projects)

    def _scan_projects(self):
        try:
            self.q.put(("projects", workspace_projects()))
        except Exception as e:
            self.q.put(("projects", e))

    def _fill_projects(self):
        if self.projects is None or self.pages[0] is None:
            return
        tree = self.proj_tree
        tree.delete(*tree.get_children())
        self._by_iid = {}
        if isinstance(self.projects, Exception) or not self.projects:
            msg = (f"The workspace could not be read: {self.projects}" if isinstance(self.projects, Exception)
                   else "No CONVI workspace (Tool, Projects, Outputs) found. Use Folder… to choose a project.")
            tree.insert("", "end", iid="_msg", values=(msg, "", ""), tags=("dim",))
            return
        flt = self.filter_entry.get().strip().lower()
        cur = self.project_var.get().strip().strip('"')
        sel = None
        for k, p in enumerate(self.projects):
            if flt and flt not in p["name"].lower():
                continue
            last = ""
            if p["last"]:
                when = datetime.fromtimestamp(p["last"]["when"]).strftime("%d %b %Y %H:%M")
                last = f"{when} → {p['last']['out'].name}" + (f"  ·  {p['last']['outputs']}"
                                                              if p["last"]["outputs"] else "")
            iid = f"p{k}"
            tree.insert("", "end", iid=iid, values=(p["name"], p["pro"].name if p["pro"] else "no .kicad_pro found",
                                                    last), tags=() if p["pro"] else ("dim",))
            self._by_iid[iid] = p
            if cur and sel is None and Path(cur) == p["path"]:
                sel = iid
        if sel:
            self._filling = True
            tree.selection_set(sel)
            tree.see(sel)
            self._filling = False
        if not self._by_iid:
            tree.insert("", "end", iid="_msg", values=("No project matches the filter.", "", ""), tags=("dim",))

    def _tree_pick(self, event=None):
        if getattr(self, "_filling", False):
            return False
        sel = self.proj_tree.selection()
        p = self._by_iid.get(sel[0]) if sel else None
        if p is None:
            return False
        if self.project_var.get().strip().strip('"') != str(p["path"]):
            self._choose_project(p["path"], refill=False)
        return True

    def _choose_project(self, path: Path, refill=True):
        self.project_var.set(str(path))
        self.out_var.set(str(default_out_dir(path)))
        self._project_changed()
        if refill:
            self._fill_projects()

    def _project_changed(self):
        self._set_project_chip()
        cur = self.project_var.get().strip()
        if cur == self._last_project:
            return
        self._last_project = cur
        self.ins = None
        self.result = None
        self.reached = 0
        self.show(0)

    def _project_folder(self) -> Path | None:
        p = self.project_var.get().strip().strip('"')
        if not p:
            return None
        path = Path(p)
        return path if path.is_dir() else path.parent

    def _pick_folder(self):
        d = filedialog.askdirectory(title="KiCad project folder")
        if d:
            self._choose_project(Path(d))

    def _pick_pro(self):
        f = filedialog.askopenfilename(title="KiCad project", filetypes=[("KiCad project", "*.kicad_pro"),
                                                                          ("KiCad board", "*.kicad_pcb"),
                                                                          ("KiCad schematic", "*.kicad_sch")])
        if f:
            self.project_var.set(f)
            self.out_var.set(str(default_out_dir(Path(f).parent)))
            self._project_changed()
            self._fill_projects()

    def _pick_out(self):
        d = filedialog.askdirectory(title="Output folder")
        if d:
            self.out_var.set(d)

    # ================================================================== page 2: check
    def _page_check(self, pg):
        pg.grid_columnconfigure(0, weight=1)
        pg.grid_rowconfigure(3, weight=1)
        self.check_head = page_header(pg, 2, "Project check",
                                      "Read from the KiCad files only. Nothing in the source project is changed.")
        self.check_head.grid(row=0, column=0, sticky="ew")
        self.block_banner = ctk.CTkFrame(pg, fg_color=RED_BG, border_color="#5A2A2A", border_width=1,
                                         corner_radius=12)
        self.block_label = label(self.block_banner, "", 13, RED, wraplength=820)
        self.block_label.pack(anchor="w", padx=16, pady=12)
        self.tiles = ctk.CTkFrame(pg, fg_color="transparent")
        self.tiles.grid(row=2, column=0, sticky="ew", pady=(18, 0))

        row = ctk.CTkFrame(pg, fg_color="transparent")
        row.grid(row=3, column=0, sticky="nsew", pady=(16, 0))
        row.grid_columnconfigure(0, weight=3)
        row.grid_columnconfigure(1, weight=2)
        row.grid_rowconfigure(0, weight=1)

        mc = card(row)
        mc.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        mc.grid_columnconfigure(0, weight=1)
        mc.grid_rowconfigure(2, weight=1)
        h = ctk.CTkFrame(mc, fg_color="transparent")
        h.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 10))
        label(h, "3D models", 15, TXT, "bold").pack(side="left")
        self.model_badge = ctk.CTkLabel(h, text="", font=F(12, "bold"), corner_radius=11, height=24)
        self.model_badge.pack(side="right")
        switch(h, "Fetch KiCad library models online", self.download).pack(side="right", padx=(0, 14))
        bar = ctk.CTkFrame(mc, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 10))
        secondary(bar, "Add STEP folder…", self._add_folder, height=36).pack(side="left")
        secondary(bar, "Assign STEP to selected…", self._assign_step, height=36).pack(side="left", padx=6)
        secondary(bar, "Path variable…", self._define_var, height=36).pack(side="left")
        tv = ctk.CTkFrame(mc, fg_color=CARD2, corner_radius=10, border_color=LINE, border_width=1)
        tv.grid(row=2, column=0, sticky="nsew", padx=20)
        tv.grid_columnconfigure(0, weight=1)
        tv.grid_rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(tv, columns=("model", "used_by", "status"), show="headings",
                                 selectmode="extended", style="Convi.Treeview")
        for c, w, t in (("model", 300, "3D model referenced in KiCad"), ("used_by", 130, "Used by"),
                        ("status", 130, "Status")):
            self.tree.heading(c, text=t, anchor="w")
            self.tree.column(c, width=w, stretch=(c == "model"), anchor="w")
        self.tree.tag_configure("missing", foreground=AMBER)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        sb = ctk.CTkScrollbar(tv, command=self.tree.yview, button_color=LINE2, button_hover_color="#4A505A")
        sb.grid(row=0, column=1, sticky="ns", pady=6)
        self.tree.configure(yscrollcommand=sb.set)
        self.model_hint = label(mc, "", 12, DIM, wraplength=560)
        self.model_hint.grid(row=3, column=0, sticky="w", padx=20, pady=(8, 14))

        nc = card(row)
        nc.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        nc.grid_columnconfigure(0, weight=1)
        nc.grid_rowconfigure(1, weight=1)
        label(nc, "Found in the project", 15, TXT, "bold").grid(row=0, column=0, sticky="w", padx=20, pady=(16, 8))
        self.notes_box = ctk.CTkTextbox(nc, fg_color=CARD, text_color=TXT2, font=F(12), wrap="word",
                                        border_width=0, scrollbar_button_color=LINE2)
        self.notes_box.grid(row=1, column=0, sticky="nsew", padx=12)
        for tag, col in (("err", RED), ("warn", AMBER), ("info", BLUE), ("dim", DIM)):
            self.notes_box.tag_config(tag, foreground=col)
        ctk.CTkButton(nc, text="Full inspection report", command=self._show_report, fg_color="transparent",
                      hover_color=BTN, text_color=ORANGE, font=F(13), height=34, anchor="w").grid(
            row=2, column=0, sticky="w", padx=12, pady=(6, 12))

        foot = ctk.CTkFrame(pg, fg_color="transparent")
        foot.grid(row=4, column=0, sticky="ew", pady=(16, 0))
        secondary(foot, "Back", lambda: self.show(0), height=44, width=90).pack(side="left")
        self.check_next = primary(foot, "Continue  →", lambda: self.show(2), width=150)
        self.check_next.pack(side="right")
        self.check_hint = label(foot, "", 13, DIM)
        self.check_hint.pack(side="right", padx=14)

    def _show_inspection(self, ins: Inspection):
        self._ensure(1)
        self.ins = ins
        pf = ins.files
        parts = [p.name for p in (pf.pro, pf.root_sch, pf.pcb) if p]
        self.check_head.title_label.configure(text=f"Project check · {(pf.pro or pf.root_sch or pf.pcb).stem}"
                                              if parts else "Project check")
        self.check_head.subtitle_label.configure(
            text="Read from " + ", ".join(parts) + ". Nothing in the source project is changed." if parts else
            "Nothing in the source project is changed.")
        # tiles
        for w in self.tiles.winfo_children():
            w.destroy()
        c = ins.counts
        spec = (("Sheets", "sheets"), ("Symbols", "symbols"), ("Footprints", "footprints"), ("Nets", "nets"),
                ("Copper layers", "copper_layers"), ("Vias", "vias"))
        shown = [(t, c[k]) for t, k in spec if k in c]
        for i, (t, v) in enumerate(shown):
            self.tiles.grid_columnconfigure(i, weight=1, uniform="tile")
            Tile(self.tiles, t, v).grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 0))
        # blocking
        if ins.blocking:
            self.block_label.configure(text="Cannot convert: " + "; ".join(ins.blocking))
            self.block_banner.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        else:
            self.block_banner.grid_forget()
        # models
        self.tree.delete(*self.tree.get_children())
        for n in ins.model_needs:
            self.tree.insert("", "end", iid=n.raw, tags=("missing",),
                             values=(n.raw, ", ".join(n.refs[:8]) + (" …" if len(n.refs) > 8 else ""), n.note))
        if ins.model_needs:
            self.model_badge.configure(text=f"  {len(ins.model_needs)} to resolve  ", fg_color=AMBER_BG,
                                       text_color=AMBER)
        elif ins.board is not None:
            self.model_badge.configure(text="  all found  ", fg_color=GREEN_BG, text_color=GREEN)
        else:
            self.model_badge.configure(text="  no board  ", fg_color=BTN, text_color=MUTED)
        lab = {"ok": "found", "embedded": "embedded", "downloaded": "KiCad library", "by_name": "by file name",
               "missing": "missing", "unresolved_var": "undefined variable", "wrl_only": "VRML only",
               "not_in_library": "not in the KiCad library"}
        stats = ", ".join(f"{lab.get(k, k)} {v}" for k, v in ins.model_stats.items())
        hint = (stats + ". " if stats else "") + ("Missing models are left out; give a folder or assign files, then "
                                                  "the project is checked again." if ins.model_needs else "")
        self.model_hint.configure(text=hint)
        # notes
        box = self.notes_box
        box.configure(state="normal")
        box.delete("1.0", "end")
        items = ins.issues.items
        order = (("error", "err", "■"), ("missing", "err", "■"), ("warning", "warn", "▲"), ("info", "info", "●"))
        for level, tag, mark in order:
            for i in items:
                if i.level == level:
                    box.insert("end", mark + "  ", tag)
                    box.insert("end", i.message + "\n\n")
        if ins.undefined_vars:
            box.insert("end", "▲  ", "warn")
            box.insert("end", "Undefined path variables: " + ", ".join(sorted(ins.undefined_vars)) + "\n\n")
        if not items and not ins.undefined_vars:
            box.insert("end", "Nothing to report.", "dim")
        box.configure(state="disabled")
        self.check_next.configure(state="disabled" if ins.blocking else "normal")
        self.check_hint.configure(text="Missing models can stay missing." if ins.model_needs and not ins.blocking
                                  else "")
        msg = "Inspection done." + (" Blocking problems found." if ins.blocking else
                                    f" {len(ins.model_needs)} 3D model(s) missing." if ins.model_needs else "")
        self._status(msg, "error" if ins.blocking else "idle")
        self.reached = max(self.reached, 1)
        self.show(1)

    def _show_report(self):
        if self.ins is None:
            return
        win = ctk.CTkToplevel(self, fg_color=BG)
        win.title("Inspection report")
        win.geometry("900x640")
        win.transient(self)
        t = ctk.CTkTextbox(win, fg_color=LOGBG, text_color=TXT2, font=F(12, family=MONO_FAMILY), wrap="word")
        t.pack(fill="both", expand=True, padx=16, pady=16)
        t.insert("end", format_report(self.ins))
        t.configure(state="disabled")
        win.after(100, win.lift)

    def _add_folder(self):
        d = filedialog.askdirectory(title="Folder that contains STEP models")
        if d:
            self.search_dirs.append(Path(d))
            self._inspect()

    def _assign_step(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Convi", "Select one or more rows first.")
            return
        f = filedialog.askopenfilename(title="STEP model", filetypes=[("STEP", "*.step *.stp *.STEP *.STP")])
        if f:
            for raw in sel:
                self.manual[raw] = Path(f)
            self._inspect()

    def _define_var(self):
        names = sorted(self.ins.undefined_vars) if self.ins else []
        win = ctk.CTkToplevel(self, fg_color=BG)
        win.title("Define KiCad path variable")
        win.transient(self)
        win.grid_columnconfigure(1, weight=1)
        label(win, "Variable name", 12, MUTED).grid(row=0, column=0, sticky="w", padx=16, pady=(16, 4))
        name = ctk.CTkComboBox(win, values=names or [""], width=320, fg_color=CARD, border_color=LINE2,
                               button_color=BTN, dropdown_fg_color=CARD)
        name.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(0, 16), pady=(16, 4))
        name.set(names[0] if names else "")
        label(win, "Folder", 12, MUTED).grid(row=1, column=0, sticky="w", padx=16, pady=4)
        val = tk.StringVar()
        ctk.CTkEntry(win, textvariable=val, width=380, fg_color=CARD, border_color=LINE2).grid(
            row=1, column=1, sticky="ew", pady=4)
        secondary(win, "…", lambda: val.set(filedialog.askdirectory() or val.get()), width=40, height=28).grid(
            row=1, column=2, padx=(6, 16))

        def ok():
            if name.get() and val.get():
                self.extra_vars[name.get()] = val.get()
                win.destroy()
                self._inspect()
        primary(win, "OK", ok, width=90, height=36).grid(row=2, column=2, columnspan=1, sticky="e", padx=16,
                                                          pady=16)
        win.after(100, win.lift)

    # ================================================================== page 3: options
    def _page_options(self, pg):
        pg.grid_columnconfigure(0, weight=1)
        pg.grid_rowconfigure(1, weight=1)
        page_header(pg, 3, "Options and manufacturing outputs").grid(row=0, column=0, sticky="ew")
        row = ctk.CTkFrame(pg, fg_color="transparent")
        row.grid(row=1, column=0, sticky="nsew", pady=(20, 0))
        row.grid_columnconfigure(0, weight=5, uniform="opt")
        row.grid_columnconfigure(1, weight=6, uniform="opt")
        row.grid_rowconfigure(0, weight=1)

        cc = card(row)
        cc.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        cc.grid_columnconfigure(0, weight=1)
        label(cc, "Conversion", 15, TXT, "bold").grid(row=0, column=0, sticky="w", padx=20, pady=(16, 12))
        label(cc, "KiCad global labels become", 12, MUTED).grid(row=1, column=0, sticky="w", padx=20)
        modes = {"Automatic": "auto", "Power ports": "power", "Ports": "port"}
        seg = ctk.CTkSegmentedButton(cc, values=list(modes), height=36, font=F(13), fg_color=BG,
                                     selected_color=BTN_H, selected_hover_color=BTN_H, unselected_color=BG,
                                     unselected_hover_color=BTN, text_color=TXT, corner_radius=9,
                                     command=lambda v: self.global_labels.set(modes[v]))
        seg.set("Automatic")
        seg.grid(row=2, column=0, sticky="ew", padx=20, pady=(4, 2))
        label(cc, "Automatic: ports; power ports where sheet pins are used.", 12, DIM, wraplength=360).grid(
            row=3, column=0, sticky="w", padx=20, pady=(0, 14))
        label(cc, "Schematic sheet template", 12, MUTED).grid(row=4, column=0, sticky="w", padx=20)
        tr = ctk.CTkFrame(cc, fg_color="transparent")
        tr.grid(row=5, column=0, sticky="ew", padx=20, pady=(4, 2))
        tr.grid_columnconfigure(0, weight=1)
        from .convert.sheet_template import customers, find_templates_dir
        self._tpl_customers = customers(find_templates_dir(Path(__file__).parent))
        self.tpl_menu = ctk.CTkOptionMenu(tr, variable=self.sheet_template, values=self._tpl_values(), height=38,
                                          font=F(13), fg_color=BTN, button_color=BTN_H,
                                          button_hover_color=LINE2, dropdown_fg_color=CARD,
                                          dropdown_hover_color=BTN_H, dropdown_font=F(13), text_color=TXT,
                                          corner_radius=10, dynamic_resizing=False)
        self.tpl_menu.grid(row=0, column=0, sticky="ew")
        secondary(tr, "Browse…", self._pick_template, width=90, height=38).grid(row=0, column=1, padx=(8, 0))
        label(cc, "Each sheet gets the smallest size it fits on, from A3 up.", 12, DIM, wraplength=360).grid(
            row=6, column=0, sticky="w", padx=20, pady=(0, 14))
        for r, (text, var, hint) in enumerate((
                ("Keep KiCad teardrops", self.teardrops, "as copper regions"),
                ("Grey box bodies", self.placeholders, "for parts whose 3D model is missing"),
                ("Open in Altium when done", self.open_after, "the project opens after the checks"))):
            f = ctk.CTkFrame(cc, fg_color="transparent")
            f.grid(row=7 + r, column=0, sticky="ew", padx=20, pady=6)
            switch(f, text, var).pack(anchor="w")
            label(f, hint, 12, DIM).pack(anchor="w", padx=(52, 0))

        oc = card(row)
        oc.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        oc.grid_columnconfigure(0, weight=1)
        oc.grid_rowconfigure(3, weight=1)
        h = ctk.CTkFrame(oc, fg_color="transparent")
        h.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 4))
        h.grid_columnconfigure(0, weight=1)
        label(h, "Manufacturing outputs", 15, TXT, "bold").grid(row=0, column=0, sticky="w")
        label(h, "Runs an OutJob template after the conversion and adds the OutJob and the Draftsman drawing to the "
                 "project.", 12, DIM, wraplength=430).grid(row=1, column=0, sticky="w")
        self._build_outputs(oc)

        foot = ctk.CTkFrame(pg, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", pady=(16, 0))
        secondary(foot, "Back", lambda: self.show(1), height=44, width=90).pack(side="left")
        self.convert_btn = primary(foot, "Convert to Altium  →", self._convert, width=210)
        self.convert_btn.pack(side="right")
        label(foot, "Altium Designer must be running; leave it alone until Convi is finished.", 13, DIM).pack(
            side="right", padx=14)

    def _build_outputs(self, parent):
        """Template choice, on/off and one check box per container of the chosen OutJob template."""
        from .outputs import builtin_templates, find_output_templates
        self._output_checks = []
        self._out_rows = {}
        self.output_template = "auto"
        self._out_parent = parent
        own = find_output_templates(Path(__file__).parent)
        self._tpl_options: dict[str, str] = {}
        if own.outjob is not None and not own.builtin:
            self._tpl_options[f"Your template ({own.outjob.stem})"] = str(own.outjob.parent)
        if builtin_templates().outjob is not None:
            self._tpl_options["Built-in template (Convi)"] = "builtin"
        self._tpl_options["Other folder…"] = "browse"
        trow = ctk.CTkFrame(parent, fg_color="transparent")
        trow.grid(row=1, column=0, sticky="ew", padx=20, pady=(10, 0))
        label(trow, "Template", 12, MUTED).pack(side="left", padx=(0, 10))
        first = next(iter(self._tpl_options))
        self.out_tpl_var = tk.StringVar(value=first)
        self.out_tpl_menu = ctk.CTkOptionMenu(trow, variable=self.out_tpl_var, values=list(self._tpl_options),
                                              command=self._template_changed, height=34, font=F(13), fg_color=BTN,
                                              button_color=BTN_H, button_hover_color=LINE2, dropdown_fg_color=CARD,
                                              dropdown_hover_color=BTN_H, dropdown_font=F(13), text_color=TXT,
                                              corner_radius=9, dynamic_resizing=False, width=300)
        self.out_tpl_menu.pack(side="left", fill="x", expand=True)
        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.grid(row=2, column=0, sticky="ew", padx=20, pady=(10, 8))
        switch(top, "Generate manufacturing outputs", self.make_outputs, self._outputs_state).pack(side="left")
        self._out_buttons = []
        for text, val in (("None", False), ("All", True)):
            bt = secondary(top, text, lambda val=val: self._set_all_outputs(val), width=56, height=32)
            bt.pack(side="right", padx=(6, 0))
            self._out_buttons.append(bt)
        self.out_count = label(top, "", 12, MUTED)
        self.out_count.pack(side="right", padx=8)
        self._out_grid = ctk.CTkFrame(parent, fg_color="transparent")
        self._out_grid.grid(row=3, column=0, sticky="new", padx=(16, 16), pady=(0, 14))
        self._out_grid.grid_columnconfigure((0, 1), weight=1, uniform="oc")
        if self._tpl_options[first] == "browse":   # no template at all: wait for the user to pick a folder
            self._output_checks = list(self._out_buttons)
            label(self._out_grid, "No OutJob template found. Choose the folder of your .OutJob (and its Draftsman "
                                  ".DwfDot) under Template.", 13, AMBER, wraplength=430).grid(
                row=0, column=0, columnspan=2, sticky="w", padx=4, pady=8)
            self.out_tpl_var.set("")
            self._outputs_state()
        else:
            self._template_changed(first)

    def _template_changed(self, value):
        """Show the containers of the chosen template."""
        from .outputs import find_output_templates, list_template_containers
        choice = self._tpl_options.get(value, "auto")
        if choice == "browse":
            d = filedialog.askdirectory(title="Folder with your .OutJob (and its Draftsman .DwfDot)")
            if not d:
                self.out_tpl_var.set(getattr(self, "_last_tpl_value", ""))
                return
            key = f"Folder: {Path(d).name}"
            self._tpl_options = {key: d, **{k: v for k, v in self._tpl_options.items() if not k.startswith("Folder:")}}
            self.out_tpl_menu.configure(values=list(self._tpl_options))
            self.out_tpl_var.set(key)
            value, choice = key, d
        self._last_tpl_value = value
        self.output_template = choice
        for w in self._out_grid.winfo_children():
            w.destroy()
        self.output_vars.clear()
        self._out_rows = {}
        self._output_checks = list(self._out_buttons)
        try:
            tpl = find_output_templates(Path(__file__).parent, choice=choice)
            conts = list_template_containers(tpl)
        except Exception as e:
            tpl, conts = None, []
            label(self._out_grid, f"The template could not be read: {e}", 13, AMBER, wraplength=430).grid(
                row=0, column=0, columnspan=2, sticky="w", padx=4, pady=8)
        if tpl is not None and (tpl.outjob is None or not conts):
            label(self._out_grid, "No .OutJob in this folder.", 13, AMBER, wraplength=430).grid(
                row=0, column=0, columnspan=2, sticky="w", padx=4, pady=8)
        for k, c in enumerate(conts):
            v = tk.BooleanVar(value=True)
            self.output_vars[c.name] = v
            f = ctk.CTkFrame(self._out_grid, fg_color=CARD2, border_color=LINE, border_width=1, corner_radius=10)
            f.grid(row=k // 2, column=k % 2, sticky="ew", padx=4, pady=4)
            cb = ctk.CTkCheckBox(f, text=c.name, variable=v, onvalue=True, offvalue=False, font=F(13, "bold"),
                                 text_color=TXT, text_color_disabled=DIM, fg_color=ORANGE, hover_color=ORANGE_H,
                                 border_color="#4A505A", checkmark_color="#111111", corner_radius=6,
                                 checkbox_width=20, checkbox_height=20, command=self._outputs_state)
            cb.pack(anchor="w", padx=12, pady=(10, 10 if not c.label.count(",") else 0))
            if c.label.count(","):   # contents only where a container holds several kinds of output (GERBER)
                inner = c.label[len(c.name):].strip().strip("()")
                label(f, inner, 12, DIM, wraplength=180).pack(anchor="w", padx=(42, 10), pady=(0, 10))
            self._output_checks.append(cb)
            self._out_rows[c.name] = f
        self._outputs_state()

    def _set_all_outputs(self, val):
        for v in self.output_vars.values():
            v.set(val)
        self._outputs_state()

    def _outputs_state(self):
        on = self.make_outputs.get()
        for w in self._output_checks:
            w.configure(state="normal" if on else "disabled")
        for name, f in getattr(self, "_out_rows", {}).items():
            sel = on and self.output_vars[name].get()
            f.configure(fg_color=ORANGE_BG if sel else CARD2, border_color=ORANGE_LINE if sel else LINE)
        if hasattr(self, "out_count"):
            n = sum(v.get() for v in self.output_vars.values())
            self.out_count.configure(text=f"{n}/{len(self.output_vars)}" if on else "off")

    def _selected_outputs(self):
        if not self.make_outputs.get():
            return False, None
        names = [n for n, v in self.output_vars.items() if v.get()]
        return bool(names), names

    def _tpl_values(self) -> list[str]:
        return [TPL_AUTO, TPL_NONE, *self._tpl_customers, *self._tpl_paths]

    def _pick_template(self):
        f = filedialog.askopenfilename(title="Altium sheet template (smallest size to use)",
                                       filetypes=[("Altium sheet template", "*.SchDot")])
        if f:
            p = Path(f)
            self._tpl_paths[p.name] = p
            self.tpl_menu.configure(values=self._tpl_values())
            self.sheet_template.set(p.name)

    def _template_choice(self) -> str:
        v = self.sheet_template.get()
        if v == TPL_NONE:
            return "none"
        if v in self._tpl_paths:
            return str(self._tpl_paths[v])
        if v in self._tpl_customers:
            return v
        return "auto"

    # ================================================================== page 4: convert
    def _page_convert(self, pg):
        pg.grid_columnconfigure(0, weight=1)
        pg.grid_rowconfigure(2, weight=1)
        top = ctk.CTkFrame(pg, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(0, weight=1)
        self.conv_head = page_header(top, 4, "Converting…")
        self.conv_head.grid(row=0, column=0, sticky="w")
        right = ctk.CTkFrame(top, fg_color="transparent")
        right.grid(row=0, column=1, sticky="se")
        self.stage_big = label(right, "", 26, ORANGE, "bold", anchor="e")
        self.stage_big.pack(anchor="e")
        self.elapsed = label(right, "", 13, MUTED, anchor="e")
        self.elapsed.pack(anchor="e")
        self.pbar = ctk.CTkProgressBar(pg, height=8, corner_radius=4, fg_color=LINE, progress_color=ORANGE)
        self.pbar.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        self.pbar.set(0)

        row = ctk.CTkFrame(pg, fg_color="transparent")
        row.grid(row=2, column=0, sticky="nsew", pady=(18, 0))
        row.grid_columnconfigure(0, weight=2, uniform="cv")
        row.grid_columnconfigure(1, weight=3, uniform="cv")
        row.grid_rowconfigure(0, weight=1)
        sc = card(row)
        sc.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        label(sc, "Stages", 15, TXT, "bold").pack(anchor="w", padx=20, pady=(16, 8))
        self.stage_rows = {}
        self._stage_imgs = {k: circle_image(k) for k in ("done", "todo", "error", "skip", "run")}
        for key, name, _ in STAGES:
            f = ctk.CTkFrame(sc, fg_color="transparent", height=46)
            f.pack(fill="x", padx=16)
            icon = ctk.CTkLabel(f, text="", image=self._stage_imgs["todo"], width=26)
            icon.pack(side="left", pady=10)
            nm = label(f, name, 14, DIM)
            nm.pack(side="left", padx=12)
            meta = label(f, "", 12, DIM, family=MONO_FAMILY, anchor="e")
            meta.pack(side="right")
            ctk.CTkFrame(sc, fg_color="#1F2227", height=1).pack(fill="x", padx=20)
            self.stage_rows[key] = (icon, nm, meta)

        lc = card(row, fg_color=LOGBG)
        lc.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        lc.grid_columnconfigure(0, weight=1)
        lc.grid_rowconfigure(1, weight=1)
        h = ctk.CTkFrame(lc, fg_color="transparent")
        h.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 6))
        label(h, "Live log", 15, TXT, "bold").pack(side="left")
        secondary(h, "Copy", self._copy_log, width=64, height=30).pack(side="right")
        self.log = ctk.CTkTextbox(lc, fg_color=LOGBG, text_color="#B8BDC5", font=F(12, family=MONO_FAMILY),
                                  wrap="word", border_width=0, scrollbar_button_color=LINE2)
        self.log.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 10))
        for tag, col in (("time", "#6E7580"), ("ok", GREEN), ("warn", AMBER), ("err", RED)):
            self.log.tag_config(tag, foreground=col)
        self.log.configure(state="disabled")

        foot = ctk.CTkFrame(pg, fg_color="transparent")
        foot.grid(row=3, column=0, sticky="ew", pady=(16, 0))
        self.conv_back = secondary(foot, "Back to options", lambda: self.show(2), height=44, width=150)
        self.conv_out = label(foot, "", 12, DIM, family=MONO_FAMILY)
        self.conv_out.pack(side="left")
        self.conv_result_btn = primary(foot, "Show result  →", lambda: self.show(4), width=170)

    def _log(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M:%S") + "  ", "time")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _copy_log(self):
        self.clipboard_clear()
        self.clipboard_append(self.log.get("1.0", "end"))

    def _stage_set(self, key, state, meta=""):
        icon, nm, mt = self.stage_rows[key]
        icon.configure(image=self._stage_imgs[state])
        nm.configure(text_color={"done": TXT, "run": AMBER, "todo": DIM, "skip": "#5C626C", "error": RED}[state])
        mt.configure(text=meta)

    def _active_stages(self) -> list[str]:
        keys = [k for k, _, _ in STAGES]
        if self.ins is not None and self.ins.files.root_sch is None:
            keys.remove("sch")
        if self.ins is not None and self.ins.board is None:
            keys.remove("pcb")
        if not self._selected_outputs()[0]:
            keys.remove("out")
        if not self.open_after.get():
            keys.remove("open")
        return keys

    def _stage_advance(self, msg):
        keys = self._run_stages
        target = None
        for key, _, words in STAGES:
            if key in keys and any(w.lower() in msg.lower() for w in words):
                target = keys.index(key)
        if target is None or target <= self._stage_i:
            if target == self._stage_i:
                d = re.search(r"\(([^()]*)\)\s*$", msg)
                if d:
                    self.stage_rows[keys[target]][2].configure(text=d.group(1)[:22])
            return
        now = time.time()
        for i in range(self._stage_i, target):
            self._stage_set(keys[i], "done", fmt_secs(now - self._stage_t0))
            self._stage_t0 = now
        self._stage_i = target
        self._stage_set(keys[target], "run")
        self._update_stage_header()

    def _update_stage_header(self):
        n = len(self._run_stages)
        self.stage_big.configure(text=f"Stage {self._stage_i + 1} of {n}")
        self.pbar.set((self._stage_i + 0.5) / n)

    def _tick(self):
        if not self.busy:
            return
        self.elapsed.configure(text=f"elapsed {fmt_secs(time.time() - self._t0)}")
        self.after(1000, self._tick)

    # ------------------------------------------------------------------ inspection / conversion work
    def _make_resolver(self) -> ModelResolver:
        folder = self._project_folder()
        out = Path(self.out_var.get() or default_out_dir(folder))
        dirs = list(self.search_dirs) + [d for d in default_model_dirs(folder) if d not in self.search_dirs]
        r = ModelResolver(folder, extra_vars=self.extra_vars, search_dirs=dirs,
                          embed_dir=out / "_convi" / "3D", download=self.download.get())
        for raw, p in self.manual.items():
            r.set_manual(raw, p)
        return r

    def _inspect(self):
        if self.busy:
            return
        if not self._project_folder() or not self._project_folder().exists():
            messagebox.showwarning("Convi", "Choose an existing KiCad project first.")
            return
        if not self.out_var.get():
            self.out_var.set(str(default_out_dir(self._project_folder())))
        self._set_project_chip()
        self._busy(True)
        self._status("Inspecting…", "busy")
        self.inspect_btn.configure(text="Inspecting…")
        path = Path(self.project_var.get().strip().strip('"'))

        def work():
            try:
                self.resolver = self._make_resolver()
                ins = inspect_project(path, self.resolver, progress=lambda m: self.q.put(("status", m)))
                self.q.put(("inspected", ins))
            except Exception as e:
                self.q.put(("error", f"Inspection failed: {e}"))
        threading.Thread(target=work, daemon=True).start()
        self.after(50, lambda: self._prebuild(1, 2, 3))

    def _busy(self, on):
        self.busy = on
        for name in ("inspect_btn", "convert_btn", "check_next"):
            b = getattr(self, name, None)
            if b is not None:
                b.configure(state="disabled" if on else "normal")
        if not on:
            self.inspect_btn.configure(text="Inspect project  →")
            if self.ins is not None and self.ins.blocking and hasattr(self, "check_next"):
                self.check_next.configure(state="disabled")
        self.show(self.step)

    def _convert(self):
        if self.ins is None or self.busy:
            return
        self._ensure(3)
        from .pipeline import ConvertOptions, convert
        out = Path(self.out_var.get())
        if out.exists() and any(out.glob("*.PrjPcb")):
            if not messagebox.askyesno("Convi", f"{out} already contains an Altium project.\n"
                                                "Overwrite the converted files?"):
                return
        self.result = None
        self._conv_failed = False
        self._run_stages = self._active_stages()
        self._stage_i, self._t0 = 0, time.time()
        self._stage_t0 = self._t0
        for key, _, _ in STAGES:
            self._stage_set(key, "todo" if key in self._run_stages else "skip",
                            "" if key in self._run_stages else "off")
        if self._run_stages:
            self._stage_set(self._run_stages[0], "run")
            self._update_stage_header()
        self.conv_head.title_label.configure(text="Converting…")
        self.stage_big.configure(text_color=ORANGE)
        self.conv_back.pack_forget()
        self.conv_result_btn.pack_forget()
        self.conv_out.configure(text=str(out))
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self._log("Altium Designer writes the files; please do not use Altium until Convi is finished.", "warn")
        opts = ConvertOptions(global_labels=self.global_labels.get(), sheet_template=self._template_choice(),
                              teardrops="regions" if self.teardrops.get() else "skip",
                              open_in_altium=self.open_after.get(),
                              placeholder_bodies=self.placeholders.get(),
                              make_outputs=self._selected_outputs()[0],
                              outputs=self._selected_outputs()[1],
                              output_template=getattr(self, "output_template", "auto"))
        ins, resolver = self.ins, self.resolver
        self.reached = 3
        self.step = 3
        self._busy(True)
        self._status("Converting… leave Altium alone.", "busy")
        self._tick()

        def work():
            try:
                res = convert(ins, out, resolver, opts, progress=lambda m: self.q.put(("progress", m)))
                self.q.put(("converted", res))
            except Exception as e:
                import traceback
                self.q.put(("error", f"Conversion failed: {e}\n{traceback.format_exc()}"))
        threading.Thread(target=work, daemon=True).start()

    def _conversion_done(self, res):
        now = time.time()
        for i in range(self._stage_i, len(self._run_stages)):
            self._stage_set(self._run_stages[i], "done", fmt_secs(now - self._stage_t0) if i == self._stage_i else "")
            self._stage_t0 = now
        self.pbar.set(1)
        self.stage_big.configure(text="Done", text_color=GREEN)
        self.elapsed.configure(text=f"finished in {fmt_secs(res.seconds)}")
        self.conv_head.title_label.configure(text="Conversion finished")
        n_err = sum(i.level in ("error", "missing") for i in res.issues.items)
        n_warn = sum(i.level == "warning" for i in res.issues.items)
        self._log(f"Done in {fmt_secs(res.seconds)}: {n_err} error(s), {n_warn} warning(s).",
                  "err" if n_err else ("warn" if n_warn else "ok"))
        self.result = res
        self._ensure(4)
        self._show_result(res)
        self.conv_result_btn.pack(side="right")
        self._busy(False)
        self.reached = 4
        self.show(4)
        self._status(f"Finished: {Path(res.project).stem if res.project else 'done'}", "idle")

    def _conversion_failed(self, text):
        self._conv_failed = True
        if self._run_stages and self._stage_i < len(self._run_stages):
            self._stage_set(self._run_stages[self._stage_i], "error")
        self.stage_big.configure(text="Stopped", text_color=RED)
        self.conv_head.title_label.configure(text="Conversion stopped")
        self._log(text, "err")
        self.conv_back.pack(side="right")

    # ================================================================== page 5: result
    def _page_result(self, pg):
        pg.grid_columnconfigure(0, weight=1)
        pg.grid_rowconfigure(3, weight=1)
        hero = card(pg, fg_color=CARD2, border_color="#2A2E35")
        hero.grid(row=0, column=0, sticky="ew")
        hero.grid_columnconfigure(0, weight=1)
        txt = ctk.CTkFrame(hero, fg_color="transparent")
        txt.grid(row=0, column=0, sticky="ew", padx=24, pady=20)
        self.res_badge = ctk.CTkLabel(txt, text="", font=F(12, "bold"), corner_radius=11, height=24,
                                      fg_color=GREEN_BG, text_color=GREEN)
        self.res_badge.pack(anchor="w")
        self.res_title = label(txt, "", 22, TXT, "bold", wraplength=560)
        self.res_title.pack(anchor="w", pady=(8, 2))
        self.res_path = label(txt, "", 12, MUTED, family=MONO_FAMILY, wraplength=560)
        self.res_path.pack(anchor="w")
        try:
            ban = rounded(Image.open(ASSETS / "convi_banner.png"), 18)
            self._banner = ctk.CTkImage(light_image=ban, dark_image=ban, size=(ban.width * 90 // ban.height, 90))
            ctk.CTkLabel(hero, image=self._banner, text="").grid(row=0, column=1, padx=24, pady=20)
        except Exception:
            pass
        self.res_tiles = ctk.CTkFrame(pg, fg_color="transparent")
        self.res_tiles.grid(row=1, column=0, sticky="ew", pady=(16, 0))

        groups = ctk.CTkFrame(pg, fg_color="transparent")
        groups.grid(row=2, column=0, sticky="ew", pady=(16, 0))
        groups.grid_columnconfigure((0, 1, 2), weight=1, uniform="grp")
        self.res_groups = {}
        for i, (key, title, col) in enumerate((("err", "Errors", RED), ("warn", "Warnings", AMBER),
                                               ("info", "Notes", BLUE))):
            f = ctk.CTkFrame(groups, fg_color=CARD, border_color=LINE, border_width=1, corner_radius=12)
            f.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0))
            bar = ctk.CTkFrame(f, fg_color=col, height=3, corner_radius=2)
            bar.pack(fill="x", padx=12, pady=(8, 0))
            r = ctk.CTkFrame(f, fg_color="transparent")
            r.pack(fill="x", padx=16, pady=(6, 12))
            t = label(r, title, 14, TXT, "bold")
            t.pack(side="left")
            n = label(r, "0", 22, col, "bold")
            n.pack(side="right")
            for w in (f, r, t, n, bar):
                w.bind("<Button-1>", lambda e, k=key: self._show_issues(k))
                w.configure(cursor="hand2")
            self.res_groups[key] = (f, n, col)

        lc = card(pg)
        lc.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        lc.grid_columnconfigure(0, weight=1)
        lc.grid_rowconfigure(0, weight=1)
        self.res_list = ctk.CTkTextbox(lc, fg_color=CARD, text_color=TXT2, font=F(12), wrap="word", border_width=0,
                                       scrollbar_button_color=LINE2)
        self.res_list.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        for tag, col in (("err", RED), ("warn", AMBER), ("info", BLUE), ("dim", DIM)):
            self.res_list.tag_config(tag, foreground=col)

        foot = ctk.CTkFrame(pg, fg_color="transparent")
        foot.grid(row=4, column=0, sticky="ew", pady=(16, 0))
        primary(foot, "Open in Altium", self._open_project, width=160).pack(side="left")
        secondary(foot, "Output folder", lambda: self.result and open_path(self.result.out_dir), height=44,
                  width=130).pack(side="left", padx=8)
        secondary(foot, "Report (JSON)", self._open_report, height=44, width=130).pack(side="left")
        ctk.CTkButton(foot, text="Convert another project", command=self._restart, fg_color="transparent",
                      hover_color=BTN, text_color=ORANGE, font=F(14), height=44).pack(side="right")

    def _show_result(self, res):
        name = Path(res.project).stem if res.project else Path(res.out_dir).name
        n_err = sum(i.level in ("error", "missing") for i in res.issues.items)
        self.res_badge.configure(text=f"  ✓  Finished in {fmt_secs(res.seconds)}  " if not n_err else
                                 f"  Finished in {fmt_secs(res.seconds)} with {n_err} error(s)  ",
                                 fg_color=GREEN_BG if not n_err else RED_BG, text_color=GREEN if not n_err else RED)
        self.res_title.configure(text=f"{name} is ready in Altium")
        self.res_path.configure(text=str(res.out_dir))
        for w in self.res_tiles.winfo_children():
            w.destroy()
        tiles = parse_metrics(res.stats)
        for i, (t, v, tot, ok) in enumerate(tiles):
            self.res_tiles.grid_columnconfigure(i, weight=1, uniform="rt")
            Tile(self.res_tiles, t, v, tot, TXT if ok else AMBER).grid(row=0, column=i, sticky="nsew",
                                                                      padx=(0 if i == 0 else 6, 0))
        counts = {"err": n_err, "warn": sum(i.level == "warning" for i in res.issues.items),
                  "info": sum(i.level == "info" for i in res.issues.items)}
        for k, (f, n, col) in self.res_groups.items():
            n.configure(text=str(counts[k]))
        self._show_issues("err" if counts["err"] else ("warn" if counts["warn"] else "info"))

    def _show_issues(self, key):
        if self.result is None:
            return
        levels = {"err": ("error", "missing"), "warn": ("warning",), "info": ("info",)}[key]
        for k, (f, n, col) in self.res_groups.items():
            f.configure(border_color=col if k == key else LINE)
        box = self.res_list
        box.configure(state="normal")
        box.delete("1.0", "end")
        mark = {"err": "■", "warn": "▲", "info": "●"}[key]
        items = [i for i in self.result.issues.items if i.level in levels]
        for i in items:
            box.insert("end", mark + "  ", key)
            box.insert("end", i.message + "\n\n")
        if not items:
            box.insert("end", "None.", "dim")
        box.configure(state="disabled")

    def _open_project(self):
        if self.result and self.result.project and Path(self.result.project).exists():
            open_path(self.result.project)

    def _open_report(self):
        if self.result:
            rep = Path(self.result.out_dir) / "convi_report.json"
            if rep.exists():
                open_path(rep)

    def _restart(self):
        self.ins = None
        self.result = None
        self._conv_failed = False
        self.reached = 0
        self.search_dirs, self.extra_vars, self.manual = [], {}, {}
        self.project_var.set("")
        self.out_var.set("")
        self._set_project_chip()
        self._status("Choose a KiCad 10 project.")
        threading.Thread(target=self._scan_projects, daemon=True).start()
        self.show(0)

    # ------------------------------------------------------------------ queue
    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "status":
                    self._status(payload, "busy")
                elif kind == "projects":
                    self.projects = payload
                    self._fill_projects()
                elif kind == "progress":
                    self._status(payload, "busy")
                    self._stage_advance(payload)
                    base = payload.split(" (")[0]
                    if base != getattr(self, "_last_progress", None):
                        self._last_progress = base
                        self._log(base)
                elif kind == "inspected":
                    self._busy(False)
                    self._show_inspection(payload)
                elif kind == "converted":
                    self._conversion_done(payload)
                elif kind == "error":
                    was_converting = self.step == 3
                    self._busy(False)
                    self._status(payload.splitlines()[0], "error")
                    if was_converting:
                        self._conversion_failed(payload)
                    messagebox.showerror("Convi", payload.splitlines()[0])
        except queue.Empty:
            pass
        self.after(100, self._poll)


def run_gui():
    App().mainloop()
    return 0
