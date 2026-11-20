"""Convi GUI wizard (Tkinter): choose project -> review missing files -> convert."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .inspect import Inspection, format_report, inspect_project
from .layout import default_model_dirs, default_out_dir
from .models3d import ModelResolver

PAD = 8
TPL_AUTO = "Automatic (customer from the project name)"
TPL_NONE = "None (keep the KiCad paper size)"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Convi {__version__} - KiCad to Altium Designer")
        self.geometry("1000x700")
        self.minsize(820, 560)
        self.q: queue.Queue = queue.Queue()
        self.ins: Inspection | None = None
        self.resolver: ModelResolver | None = None
        self.search_dirs: list[Path] = []
        self.extra_vars: dict[str, str] = {}
        self.manual: dict[str, Path] = {}
        self.project_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Choose a KiCad 10 project (folder or .kicad_pro).")
        self.global_labels = tk.StringVar(value="auto")
        self.teardrops = tk.BooleanVar(value=True)
        self.open_after = tk.BooleanVar(value=True)
        self.download = tk.BooleanVar(value=True)
        self.placeholders = tk.BooleanVar(value=False)
        self.sheet_template = tk.StringVar(value=TPL_AUTO)
        self._tpl_paths: dict[str, Path] = {}
        self._build()
        self.after(100, self._poll)

    # ------------------------------------------------------------------ layout
    def _build(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        top = ttk.Frame(self, padding=PAD)
        top.pack(fill="x")
        ttk.Label(top, text="KiCad project:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.project_var).grid(row=0, column=1, sticky="ew", padx=PAD)
        ttk.Button(top, text="Folder...", command=self._pick_folder).grid(row=0, column=2)
        ttk.Button(top, text=".kicad_pro...", command=self._pick_pro).grid(row=0, column=3, padx=(PAD, 0))
        ttk.Label(top, text="Output folder:").grid(row=1, column=0, sticky="w", pady=(PAD, 0))
        ttk.Entry(top, textvariable=self.out_var).grid(row=1, column=1, sticky="ew", padx=PAD, pady=(PAD, 0))
        ttk.Button(top, text="Browse...", command=self._pick_out).grid(row=1, column=2, pady=(PAD, 0))
        self.inspect_btn = ttk.Button(top, text="1. Inspect", command=self._inspect)
        self.inspect_btn.grid(row=1, column=3, padx=(PAD, 0), pady=(PAD, 0))
        top.columnconfigure(1, weight=1)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=PAD, pady=PAD)
        self.nb = nb

        # --- report tab
        rep = ttk.Frame(nb, padding=PAD)
        self.report = tk.Text(rep, wrap="word", font=("Consolas", 10))
        sb = ttk.Scrollbar(rep, command=self.report.yview)
        self.report.configure(yscrollcommand=sb.set, state="disabled")
        self.report.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        nb.add(rep, text="Inspection report")

        # --- missing files tab
        miss = ttk.Frame(nb, padding=PAD)
        bar = ttk.Frame(miss)
        bar.pack(fill="x")
        ttk.Button(bar, text="Add folder with STEP models...", command=self._add_folder).pack(side="left")
        ttk.Button(bar, text="Assign STEP file to selected...", command=self._assign_step).pack(side="left", padx=PAD)
        ttk.Button(bar, text="Define path variable...", command=self._define_var).pack(side="left")
        ttk.Checkbutton(bar, text="Fetch KiCad library models online", variable=self.download).pack(side="right")
        cols = ("model", "used_by", "status")
        self.tree = ttk.Treeview(miss, columns=cols, show="headings", selectmode="extended")
        for c, w, t in (("model", 520, "3D model referenced in KiCad"), ("used_by", 220, "Used by"),
                        ("status", 200, "Status")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, stretch=(c == "model"))
        self.tree.pack(fill="both", expand=True, pady=(PAD, 0))
        self.miss_label = ttk.Label(miss, text="")
        self.miss_label.pack(fill="x", pady=(PAD, 0))
        nb.add(miss, text="Missing files")

        # --- convert tab
        conv = ttk.Frame(nb, padding=PAD)
        opts = ttk.LabelFrame(conv, text="Options", padding=PAD)
        opts.pack(fill="x")
        ttk.Label(opts, text="KiCad global labels become:").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(opts, text="Automatic (recommended: ports; power ports if sheet pins are used)",
                        value="auto", variable=self.global_labels).grid(row=0, column=1, sticky="w")
        ttk.Radiobutton(opts, text="Power ports", value="power",
                        variable=self.global_labels).grid(row=0, column=2, sticky="w", padx=PAD)
        ttk.Radiobutton(opts, text="Ports", value="port",
                        variable=self.global_labels).grid(row=0, column=3, sticky="w", padx=PAD)
        ttk.Checkbutton(opts, text="Keep KiCad teardrops (as copper regions)", variable=self.teardrops).grid(
            row=1, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(opts, text="Open the project in Altium when done", variable=self.open_after).grid(
            row=2, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(opts, text="Grey box bodies for parts whose 3D model is missing",
                        variable=self.placeholders).grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Label(opts, text="Sheet template:").grid(row=4, column=0, sticky="w", pady=(PAD, 0))
        tpl_row = ttk.Frame(opts)
        tpl_row.grid(row=4, column=1, columnspan=3, sticky="w", pady=(PAD, 0))
        from .convert.sheet_template import customers, find_templates_dir
        self._tpl_customers = customers(find_templates_dir(Path(__file__).parent))
        self.tpl_combo = ttk.Combobox(tpl_row, textvariable=self.sheet_template, state="readonly", width=48,
                                      values=self._tpl_values())
        self.tpl_combo.pack(side="left")
        ttk.Button(tpl_row, text="Browse...", command=self._pick_template).pack(side="left", padx=(PAD, 0))
        ttk.Label(tpl_row, text="each sheet: smallest size it fits on, from A3 up (A2, A1 ...)").pack(
            side="left", padx=(PAD, 0))
        self.convert_btn = ttk.Button(conv, text="2. Convert to Altium", command=self._convert, state="disabled")
        self.convert_btn.pack(anchor="w", pady=PAD)
        self.progress = ttk.Progressbar(conv, mode="indeterminate")
        self.progress.pack(fill="x")
        self.log = tk.Text(conv, wrap="word", height=18, font=("Consolas", 10), state="disabled")
        self.log.pack(fill="both", expand=True, pady=(PAD, 0))
        nb.add(conv, text="Convert")

        st = ttk.Frame(self, padding=(PAD, 0, PAD, PAD))
        st.pack(fill="x")
        ttk.Label(st, textvariable=self.status_var).pack(side="left")

    # ------------------------------------------------------------------ helpers
    def _set_text(self, widget: tk.Text, text: str, append=False):
        widget.configure(state="normal")
        if not append:
            widget.delete("1.0", "end")
        widget.insert("end", text)
        widget.see("end")
        widget.configure(state="disabled")

    def _project_folder(self) -> Path | None:
        p = self.project_var.get().strip().strip('"')
        if not p:
            return None
        path = Path(p)
        return path if path.is_dir() else path.parent

    def _pick_folder(self):
        d = filedialog.askdirectory(title="KiCad project folder")
        if d:
            self.project_var.set(d)
            self.out_var.set(str(default_out_dir(Path(d))))

    def _pick_pro(self):
        f = filedialog.askopenfilename(title="KiCad project", filetypes=[("KiCad project", "*.kicad_pro"),
                                                                          ("KiCad board", "*.kicad_pcb"),
                                                                          ("KiCad schematic", "*.kicad_sch")])
        if f:
            self.project_var.set(f)
            self.out_var.set(str(default_out_dir(Path(f).parent)))

    def _tpl_values(self) -> list[str]:
        return [TPL_AUTO, TPL_NONE, *self._tpl_customers, *self._tpl_paths]

    def _pick_template(self):
        f = filedialog.askopenfilename(title="Altium sheet template (smallest size to use)",
                                       filetypes=[("Altium sheet template", "*.SchDot")])
        if f:
            p = Path(f)
            self._tpl_paths[p.name] = p
            self.tpl_combo.configure(values=self._tpl_values())
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

    def _pick_out(self):
        d = filedialog.askdirectory(title="Output folder")
        if d:
            self.out_var.set(d)

    def _make_resolver(self) -> ModelResolver:
        folder = self._project_folder()
        out = Path(self.out_var.get() or default_out_dir(folder))
        dirs = list(self.search_dirs) + [d for d in default_model_dirs(folder) if d not in self.search_dirs]
        r = ModelResolver(folder, extra_vars=self.extra_vars, search_dirs=dirs,
                          embed_dir=out / "_convi" / "3D", download=self.download.get())
        for raw, p in self.manual.items():
            r.set_manual(raw, p)
        return r

    # ------------------------------------------------------------------ inspection
    def _inspect(self):
        if not self._project_folder():
            messagebox.showwarning("Convi", "Choose a KiCad project first.")
            return
        if not self.out_var.get():
            self.out_var.set(str(default_out_dir(self._project_folder())))
        self.inspect_btn.configure(state="disabled")
        self.convert_btn.configure(state="disabled")
        self.status_var.set("Inspecting...")
        self.progress.start(12)
        path = Path(self.project_var.get().strip().strip('"'))

        def work():
            try:
                self.resolver = self._make_resolver()
                ins = inspect_project(path, self.resolver, progress=lambda m: self.q.put(("status", m)))
                self.q.put(("inspected", ins))
            except Exception as e:
                self.q.put(("error", f"Inspection failed: {e}"))
        threading.Thread(target=work, daemon=True).start()

    def _show_inspection(self, ins: Inspection):
        self.ins = ins
        self._set_text(self.report, format_report(ins))
        self.tree.delete(*self.tree.get_children())
        for n in ins.model_needs:
            self.tree.insert("", "end", iid=n.raw, values=(n.raw, ", ".join(n.refs[:8]) +
                                                           (" ..." if len(n.refs) > 8 else ""), n.note))
        if ins.model_needs:
            self.miss_label.configure(text=f"{len(ins.model_needs)} model(s) missing. Give a folder or assign files, "
                                           "then press Inspect again. Missing models are simply left out.")
        else:
            self.miss_label.configure(text="All 3D models were found.")
        self.convert_btn.configure(state="disabled" if ins.blocking else "normal")
        msg = "Inspection done."
        if ins.blocking:
            msg += " Blocking problems found - see the report."
        elif ins.model_needs:
            msg += f" {len(ins.model_needs)} 3D model(s) missing (see 'Missing files')."
        self.status_var.set(msg)
        if ins.model_needs:
            self.nb.select(1)

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
        win = tk.Toplevel(self)
        win.title("Define KiCad path variable")
        ttk.Label(win, text="Variable name:").grid(row=0, column=0, padx=PAD, pady=PAD, sticky="w")
        name = ttk.Combobox(win, values=names, width=30)
        name.grid(row=0, column=1, padx=PAD, pady=PAD)
        if names:
            name.set(names[0])
        ttk.Label(win, text="Folder:").grid(row=1, column=0, padx=PAD, sticky="w")
        val = tk.StringVar()
        ttk.Entry(win, textvariable=val, width=50).grid(row=1, column=1, padx=PAD)
        ttk.Button(win, text="...", command=lambda: val.set(filedialog.askdirectory() or val.get())).grid(
            row=1, column=2, padx=(0, PAD))

        def ok():
            if name.get() and val.get():
                self.extra_vars[name.get()] = val.get()
                win.destroy()
                self._inspect()
        ttk.Button(win, text="OK", command=ok).grid(row=2, column=1, pady=PAD, sticky="e")

    # ------------------------------------------------------------------ conversion
    def _convert(self):
        if self.ins is None:
            return
        from .pipeline import ConvertOptions, convert
        out = Path(self.out_var.get())
        if out.exists() and any(out.glob("*.PrjPcb")):
            if not messagebox.askyesno("Convi", f"{out} already contains an Altium project.\n"
                                                "Overwrite the converted files?"):
                return
        self.convert_btn.configure(state="disabled")
        self.inspect_btn.configure(state="disabled")
        self.nb.select(2)
        self._set_text(self.log, "Converting - Altium Designer writes the files; please do not use Altium "
                                 "until Convi is finished.\n\n")
        self.progress.start(12)
        opts = ConvertOptions(global_labels=self.global_labels.get(), sheet_template=self._template_choice(),
                              teardrops="regions" if self.teardrops.get() else "skip",
                              open_in_altium=self.open_after.get(),
                              placeholder_bodies=self.placeholders.get())
        ins, resolver = self.ins, self.resolver

        def work():
            try:
                res = convert(ins, out, resolver, opts, progress=lambda m: self.q.put(("progress", m)))
                self.q.put(("converted", res))
            except Exception as e:
                import traceback
                self.q.put(("error", f"Conversion failed: {e}\n{traceback.format_exc()}"))
        threading.Thread(target=work, daemon=True).start()

    def _show_result(self, res):
        lines = [f"\nDone in {res.seconds:.0f} s.", f"Project: {res.project}", ""]
        for k, v in res.stats.items():
            if isinstance(v, str):
                lines.append(f"{k}: {v}")
        lines.append("")
        for i in res.issues.items:
            if i.level in ("error", "warning"):
                lines.append(f"[{i.level}] {i.message}")
        notes = [i for i in res.issues.items if i.level == "info"]
        if notes:
            lines.append("")
            lines += [f"- {i.message}" for i in notes]
        self._set_text(self.log, "\n".join(lines) + "\n", append=True)
        self.status_var.set(f"Finished: {res.project}")
        if messagebox.askyesno("Convi", "Conversion finished.\nOpen the output folder?"):
            os.startfile(res.out_dir)

    # ------------------------------------------------------------------ queue
    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "status":
                    self.status_var.set(payload)
                elif kind == "progress":
                    self.status_var.set(payload)
                    base = payload.split(" (")[0]
                    if base != getattr(self, "_last_progress", None):
                        self._last_progress = base
                        self._set_text(self.log, base + "\n", append=True)
                elif kind == "inspected":
                    self.progress.stop()
                    self.inspect_btn.configure(state="normal")
                    self._show_inspection(payload)
                elif kind == "converted":
                    self.progress.stop()
                    self.inspect_btn.configure(state="normal")
                    self.convert_btn.configure(state="normal")
                    self._show_result(payload)
                elif kind == "error":
                    self.progress.stop()
                    self.inspect_btn.configure(state="normal")
                    self.convert_btn.configure(state="normal" if self.ins else "disabled")
                    self._set_text(self.log, payload + "\n", append=True)
                    messagebox.showerror("Convi", payload.splitlines()[0])
        except queue.Empty:
            pass
        self.after(100, self._poll)


def run_gui():
    App().mainloop()
    return 0
