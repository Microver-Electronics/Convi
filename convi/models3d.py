"""Resolve KiCad 3D model references to local STEP files."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

STEP_EXT = (".step", ".stp", ".STEP", ".STP", ".Step", ".Stp")

# Official KiCad 3D model library (used to fetch standard-library models that are not installed locally)
KICAD_LIB_URLS = [
    "https://gitlab.com/kicad/libraries/kicad-packages3D/-/raw/master/{lib}/{name}",
    "https://raw.githubusercontent.com/KiCad/kicad-packages3D/master/{lib}/{name}",
]
KICAD_STD_VAR = re.compile(r"^\$[{(](KICAD\d*_3DMODEL_DIR|KISYS3DMOD)[})][/\\]", re.I)
STD_TAIL = re.compile(r"([^/\\]+\.3dshapes)[/\\]([^/\\]+)$", re.I)


def default_cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "Convi" / "kicad-3dmodels"


def fetch_kicad_std_model(raw: str, cache_dir: Path, timeout: float = 30.0) -> Path | None:
    """Download a KiCad standard-library model (STEP) referenced as ${KICADn_3DMODEL_DIR}/Lib.3dshapes/Name.ext."""
    if not KICAD_STD_VAR.match(raw):
        return None
    m = STD_TAIL.search(raw)
    if not m:
        return None
    lib, fname = m.group(1), m.group(2)
    stem = fname.rsplit(".", 1)[0]
    target = cache_dir / lib / (stem + ".step")
    if target.is_file() and target.stat().st_size > 0:
        return target
    import urllib.parse
    import urllib.request
    for tmpl in KICAD_LIB_URLS:
        url = tmpl.format(lib=urllib.parse.quote(lib), name=urllib.parse.quote(stem + ".step"))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Convi-KiCad-to-Altium"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            if len(data) > 100 and b"ISO-10303" in data[:200]:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                return target
        except Exception:
            continue
    return None
# KiCad library of a standard model file name, for references through a path variable the converting PC does
# not have (a designer's own copy of the library: ${VENDOR3DMDL}/R_0402_1005Metric.step).  Used to
# fetch the model when the local KiCad has no 3D library; the local library is searched by name first.
STD_NAME_LIBS = [
    (re.compile(r"^R_\d{4}_\d{4}Metric$"), "Resistor_SMD.3dshapes"),
    (re.compile(r"^C_\d{4}_\d{4}Metric$"), "Capacitor_SMD.3dshapes"),
    (re.compile(r"^L_\d{4}_\d{4}Metric$"), "Inductor_SMD.3dshapes"),
    (re.compile(r"^LED_\d{4}_\d{4}Metric$"), "LED_SMD.3dshapes"),
    (re.compile(r"^D_\d{4}_\d{4}Metric$"), "Diode_SMD.3dshapes"),
    (re.compile(r"^R_Array_"), "Resistor_SMD.3dshapes"),
    (re.compile(r"^D_SO[DT]-"), "Diode_SMD.3dshapes"),
    (re.compile(r"^(SOT|SC|TO)-\d"), "Package_TO_SOT_SMD.3dshapes"),
    (re.compile(r"^(SOIC|SO|TSSOP|SSOP|MSOP|HTSSOP|VSSOP)-\d"), "Package_SO.3dshapes"),
    (re.compile(r"^(QFN|DFN|VQFN|WQFN|UQFN|TQFN|TDFN|WDFN|UDFN)-\d"), "Package_DFN_QFN.3dshapes"),
    (re.compile(r"^(LQFP|TQFP|QFP)-\d"), "Package_QFP.3dshapes"),
    (re.compile(r"^(USON|SON|VSON|WSON|X2SON)-\d"), "Package_SON.3dshapes"),
    (re.compile(r"^Vishay_PowerPAK_"), "Package_SO.3dshapes"),
    (re.compile(r"^Oscillator_"), "Oscillator.3dshapes"),
    (re.compile(r"^(Crystal|Resonator)_"), "Crystal.3dshapes"),
    (re.compile(r"^USB_C_"), "Connector_USB.3dshapes"),
]


# Designer libraries name standard packages their own way (e.g. sot23-6, SOD523F, C_0603_1608Metric_EIA,
# FB_0805_...).  These KiCad library files have the same body; tried in order, the first that exists wins.
MODEL_ALIASES = {
    "xdfn-2_1x0.60mm": ["D_SOD-882"],
    "c_0603_1608metric_eia": ["C_0603_1608Metric"],
    "fb_0805_2012metric": ["L_0805_2012Metric"],
    "fb_0603_1608metric": ["L_0603_1608Metric"],
    "fb_0402_1005metric": ["L_0402_1005Metric"],
    "c_pol_eia-x": ["CP_EIA-7343-43_Kemet-X"],
    "sot23-3": ["SOT-23-3", "SOT-23"],
    "sot23-5": ["SOT-23-5"],
    "sot23-6": ["SOT-23-6"],
    "sod-923": ["D_SOD-923"],
    "sod523f": ["D_SOD-523"],
    "sod-523": ["D_SOD-523"],
    "sot-523": ["SOT-523"],
    "exb18v": ["R_Array_Convex_4x0201"],
    "r_array_panasonic_exb28": ["R_Array_Convex_4x0402"],
    "usb-c_receptacle_gct_usb4105-gf-a": ["USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal"],
    "conn_jst_sh_1x4_sm04b-srss-tb-lf-sn": ["JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal"],
    "conn_molex_picoblade_1x4_0533980471": ["Molex_PicoBlade_53398-0471_1x04-1MP_P1.25mm_Vertical"],
    "oscillator_smd_ecs_2520mv-2.5x2mm": ["Oscillator_SMD_ECS_2520MV-xxx-xx-4Pin_2.5x2.0mm"],
}


def _norm_name(stem: str) -> str:
    """Model name compared loosely: case, '-' / '_' / spaces ignored, '1.0mm' = '1mm', '0.60' = '0.6'."""
    t = stem.lower()
    t = re.sub(r"(\d+\.\d*?)0+(?=\D|$)", r"\1", t)
    t = re.sub(r"(\d+)\.(?=\D|$)", r"\1", t)
    return re.sub(r"[^a-z0-9.]", "", t)


def nearest_std_names(stem: str, stems) -> list[str]:
    """KiCad library file stems (from `stems`, lower case) a designer's model name most likely means, best first:
    aliases, the same name with a diode 'D_' prefix or without a trailing F / _EIA, the same name compared loosely,
    then the shortest library name that starts with it (a package name without the exposed pad size)."""
    low = stem.lower()
    have = set(stems)
    out = []

    def add(x):
        x = x.lower()
        if x in have and x not in out:
            out.append(x)
    for a in MODEL_ALIASES.get(low, []):
        add(a)
    for v in (low, "d_" + low, re.sub(r"f$", "", low), "d_" + re.sub(r"f$", "", low), re.sub(r"_eia$", "", low)):
        add(v)
    if out:
        return out
    key = _norm_name(stem)
    if len(key) < 6:
        return out
    by_norm = [x for x in have if _norm_name(x) == key]
    for x in sorted(by_norm):
        add(x)
    if out:
        return out
    base = re.sub(r"_ep[\d.x]+mm$", "", low)
    for k in {key, _norm_name(base)}:
        if len(k) < 10:
            continue
        pref = sorted((x for x in have if _norm_name(x).startswith(k) and _norm_name(x) != k), key=lambda x: (len(x), x))
        for x in pref[:1]:
            add(x)
    return out


def std_lib_for_name(stem: str) -> str | None:
    for rx, lib in STD_NAME_LIBS:
        if rx.match(stem):
            return lib
    return None


VAR_RE = re.compile(r"\$\{([^}]+)\}|\$\(([^)]+)\)")


def kicad_config_vars() -> dict[str, str]:
    """Path variables from KiCad's kicad_common.json (newest version first) + defaults."""
    out: dict[str, str] = {}
    appdata = os.environ.get("APPDATA")
    cands = []
    if appdata:
        base = Path(appdata) / "kicad"
        if base.is_dir():
            vers = sorted([p for p in base.iterdir() if p.is_dir()], key=lambda p: p.name, reverse=True)
            cands = [v / "kicad_common.json" for v in vers]
    for c in reversed(cands):
        try:
            data = json.loads(c.read_text(encoding="utf-8"))
            vars_ = (data.get("environment") or {}).get("vars") or {}
            for k, v in vars_.items():
                if v:
                    out[k] = v
        except Exception:
            pass
    # default install locations
    for ver in ("10.0", "9.0", "8.0", "7.0", "6.0"):
        major = ver.split(".")[0]
        root = Path(rf"C:\Program Files\KiCad\{ver}\share\kicad")
        if root.is_dir():
            out.setdefault(f"KICAD{major}_3DMODEL_DIR", str(root / "3dmodels"))
            out.setdefault(f"KICAD{major}_FOOTPRINT_DIR", str(root / "footprints"))
            out.setdefault(f"KICAD{major}_SYMBOL_DIR", str(root / "symbols"))
    return out


@dataclass
class ModelRef:
    raw: str
    expanded: str = ""
    step: Path | None = None
    status: str = "missing"   # ok / embedded / downloaded / by_name / missing / wrl_only / unresolved_var
    note: str = ""
    candidates: list = field(default_factory=list)


class ModelResolver:
    def __init__(self, project_dir: Path, extra_vars: dict | None = None, search_dirs=None,
                 embed_dir: Path | None = None, download: bool = True, cache_dir: Path | None = None):
        self.project_dir = Path(project_dir).resolve()
        self.vars = {k: v for k, v in os.environ.items()}
        self.vars.update(kicad_config_vars())
        self.vars["KIPRJMOD"] = str(self.project_dir)
        if extra_vars:
            self.vars.update({k: v for k, v in extra_vars.items() if v})
        # the project folder is always searched by file name (models moved / foreign absolute paths)
        self.search_dirs = [Path(p) for p in (search_dirs or [])] + [self.project_dir]
        self.embed_dir = Path(embed_dir).resolve() if embed_dir else None
        self.manual: dict[str, Path] = {}   # raw ref -> user supplied STEP file
        self.cache: dict[tuple, ModelRef] = {}
        self.download = download
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self._index: dict[str, Path] | None = None
        self._std: dict[str, tuple] | None = None
        # Models behind a designer's undefined path variable (e.g. ${VENDOR3DMDL}) replaced by KiCad's
        # library file of that name / package: OFF.  The footprint's model offset / rotation belong to the
        # designer's own STEP (an R_0603: offset -0.8 -0.4 0.45, rotate 90), so KiCad's file came out
        # shifted and turned.  Such parts get no body (or the grey box option).
        self.by_name = False

    def set_manual(self, raw: str, path: Path):
        self.manual[raw] = Path(path)
        self.cache = {k: v for k, v in self.cache.items() if k[0] != raw}

    def _expand(self, s: str) -> tuple[str, list[str]]:
        missing = []

        def rep(m):
            name = m.group(1) or m.group(2)
            if name in self.vars:
                return self.vars[name]
            # tolerate version-specific names: KICAD9_3DMODEL_DIR -> any KICADn_3DMODEL_DIR
            mm = re.match(r"KICAD\d*_3DMODEL_DIR", name)
            if mm:
                for k in sorted(self.vars, reverse=True):
                    if re.match(r"KICAD\d*_3DMODEL_DIR$", k):
                        return self.vars[k]
            missing.append(name)
            return m.group(0)
        return VAR_RE.sub(rep, s), missing

    def _search_index(self) -> dict[str, Path]:
        if self._index is None:
            self._index = {}
            for d in self.search_dirs:
                if d.is_dir():
                    for p in d.rglob("*"):
                        if p.suffix.lower() in (".step", ".stp"):
                            self._index.setdefault(p.stem.lower(), p)
        return self._index

    def _std_index(self) -> dict[str, tuple]:
        """File stem (lower case) -> (library folder name, STEP path or None) over the local KiCad 3D libraries."""
        if self._std is None:
            self._std = {}
            dirs = []
            for k, v in sorted(self.vars.items(), reverse=True):
                if re.match(r"(KICAD\d*_3DMODEL_DIR|KISYS3DMOD)$", k) and v and Path(v).is_dir():
                    if Path(v).resolve() not in dirs:
                        dirs.append(Path(v).resolve())
            for d in dirs:
                try:
                    libs = [e for e in os.scandir(d) if e.is_dir() and e.name.lower().endswith(".3dshapes")]
                except OSError:
                    continue
                for lib in libs:
                    try:
                        for f in os.scandir(lib.path):
                            stem, _, ext = f.name.rpartition(".")
                            if ext.lower() in ("step", "stp", "wrl"):
                                old = self._std.get(stem.lower())
                                step = Path(f.path) if ext.lower() in ("step", "stp") else None
                                if old is None or (old[1] is None and step is not None):
                                    self._std[stem.lower()] = (lib.name, step)
                    except OSError:
                        continue
        return self._std

    def _by_name(self, stem: str, ref: "ModelRef", why: str) -> bool:
        """A model the reference cannot reach, found by its file name in KiCad's own 3D library."""
        lib, step = self._std_index().get(stem.lower(), (None, None))
        if step is not None:
            ref.step, ref.status = step, "by_name"
            ref.note = f"{why}; same file name in the local KiCad 3D library ({lib})"
            return True
        # not under that name: the KiCad model of the same package under KiCad's name (sot23-6 = SOT-23-6)
        idx = self._std_index()
        for cand in nearest_std_names(stem, [k for k, v in idx.items() if v[1] is not None]):
            lib2, step2 = idx[cand]
            ref.step, ref.status = step2, "by_name"
            ref.note = f"{why}; KiCad library model of the same package: {step2.stem} ({lib2})"
            return True
        lib = lib or std_lib_for_name(stem)
        if lib and self.download:
            got = fetch_kicad_std_model("${KICAD10_3DMODEL_DIR}/" + lib + "/" + stem + ".step", self.cache_dir)
            if got is not None:
                ref.step, ref.status = got, "by_name"
                ref.note = f"{why}; same file name in the official KiCad 3D library ({lib}, downloaded)"
                return True
        return False

    @staticmethod
    def _step_variants(p: Path) -> list[Path]:
        out = []
        if p.suffix.lower() in (".step", ".stp"):
            out.append(p)
        base = p.with_suffix("") if p.suffix else p
        for ext in STEP_EXT:
            out.append(base.with_name(base.name + ext))
        return out

    def resolve(self, raw: str, embedded=None) -> ModelRef:
        """embedded: dict or list of dicts (footprint first, then board) of EmbeddedFile."""
        dicts = embedded if isinstance(embedded, (list, tuple)) else ([embedded] if embedded else [])
        key = (raw, tuple(id(d) for d in dicts))
        if key in self.cache:
            return self.cache[key]
        ref = ModelRef(raw=raw)
        self.cache[key] = ref
        if raw in self.manual and self.manual[raw].is_file():
            ref.step, ref.status, ref.note = self.manual[raw], "ok", "user supplied"
            return ref
        if raw.startswith("kicad-embed://"):
            name = raw[len("kicad-embed://"):]
            ef = None
            for d in dicts:
                cand = (d or {}).get(name)
                if cand is not None and cand.has_data:
                    ef = cand
                    break
            if ef is not None and self.embed_dir is not None:
                out = self.embed_dir / name
                if not out.exists():
                    try:
                        ef.extract(self.embed_dir)
                    except Exception as e:
                        ref.status, ref.note = "missing", f"cannot extract embedded file: {e}"
                        return ref
                if out.suffix.lower() in (".step", ".stp"):
                    ref.step, ref.status = out, "embedded"
                else:
                    ref.status, ref.note = "wrl_only", "embedded model is not STEP"
                return ref
            ref.status, ref.note = "missing", "embedded file data not found"
            return ref
        exp, missing_vars = self._expand(raw)
        ref.expanded = exp
        p = Path(exp)
        if not p.is_absolute():
            p = self.project_dir / p
        cands = self._step_variants(p)
        ref.candidates = [str(c) for c in cands]
        for c in cands:
            if c.is_file():
                ref.step, ref.status = c, "ok"
                if c.suffix.lower() != p.suffix.lower():
                    ref.note = f"using STEP instead of {p.suffix}"
                return ref
        # search user-provided folders by file name
        idx = self._search_index()
        hit = idx.get(p.stem.lower())
        if hit:
            ref.step, ref.status, ref.note = hit, "ok", "found in search folder"
            return ref
        if self.download:
            got = fetch_kicad_std_model(raw, self.cache_dir)
            if got is not None:
                ref.step, ref.status, ref.note = got, "downloaded", "fetched from the official KiCad 3D library"
                return ref
            if KICAD_STD_VAR.match(raw):
                ref.status = "not_in_library"
                ref.note = "not available in the official KiCad 3D library (KiCad shows no model either)"
                return ref
        if missing_vars and self.by_name and self._by_name(p.stem, ref, "path variable(s) " +
                                                           ", ".join(missing_vars) + " not defined on this PC"):
            return ref
        if missing_vars:
            ref.status, ref.note = "unresolved_var", "undefined path variable(s): " + ", ".join(missing_vars)
        elif p.is_file():
            ref.status, ref.note = "wrl_only", "only a VRML model exists; Altium needs STEP"
        else:
            ref.status, ref.note = "missing", "file not found"
        return ref
