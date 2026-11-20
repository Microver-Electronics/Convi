"""Run DelphiScript code inside a running (or freshly started) Altium Designer instance.

Altium accepts ``X2.EXE -RScriptingSystem:RunScriptFile(FileName=...|ProcName=...)``;
when an instance is already running, the command is forwarded to it.  Scripts
signal completion by writing a marker file, which we poll for.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

DEFAULT_X2_CANDIDATES = [
    r"C:\Program Files\Altium\ADDevelop\X2.EXE",
    r"C:\Program Files\Altium\AD26\X2.EXE",
    r"C:\Program Files\Altium\AD25\X2.EXE",
    r"C:\Program Files\Altium\AD24\X2.EXE",
]


class AltiumNotFound(RuntimeError):
    pass


class AltiumTimeout(RuntimeError):
    pass


class AltiumNotRunning(RuntimeError):
    pass


class AltiumScriptError(RuntimeError):
    """A script error dialog appeared in Altium (it was dismissed automatically)."""


def find_x2(explicit: str | None = None) -> Path:
    """Locate X2.EXE (explicit path, CONVI_ALTIUM env var, registry, default folders)."""
    cands: list[str] = []
    if explicit:
        cands.append(explicit)
    if os.environ.get("CONVI_ALTIUM"):
        cands.append(os.environ["CONVI_ALTIUM"])
    cands.extend(_registry_candidates())
    cands.extend(DEFAULT_X2_CANDIDATES)
    base = Path(r"C:\Program Files\Altium")
    if base.is_dir():
        for sub in sorted(base.iterdir(), reverse=True):
            cands.append(str(sub / "X2.EXE"))
    for c in cands:
        p = Path(c)
        if p.is_dir():
            p = p / "X2.EXE"
        if p.is_file():
            return p
    raise AltiumNotFound("Altium Designer (X2.EXE) not found. Use --altium to give its path.")


def _registry_candidates() -> list[str]:
    out: list[str] = []
    try:
        import winreg
    except ImportError:
        return out
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(root, r"SOFTWARE\Altium\Builds")
        except OSError:
            continue
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(key, i)
            except OSError:
                break
            i += 1
            try:
                k2 = winreg.OpenKey(key, sub)
                prog, _ = winreg.QueryValueEx(k2, "ProgramsInstallPath")
                out.append(str(Path(prog) / "X2.EXE"))
            except OSError:
                pass
    return out


def x2_instances() -> int:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq X2.EXE", "/NH"],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return 1
    return sum(1 for ln in out.splitlines() if ln.upper().startswith("X2.EXE"))


def is_altium_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq X2.EXE", "/NH"],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return False
    return "X2.EXE" in out.upper()


LOCK = Path(os.environ.get("TEMP", ".")) / "convi_altium.lock"


class _AltiumLock:
    """Only one Convi script may run inside Altium at a time (scripts share Altium's UI thread)."""

    def __enter__(self):
        t0 = time.time()
        while True:
            try:
                self.fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                # stale lock (owner process gone)?
                try:
                    pid = int(LOCK.read_text() or "0")
                    alive = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True,
                                           text=True).stdout
                    if str(pid) not in alive:
                        LOCK.unlink(missing_ok=True)
                        continue
                except (OSError, ValueError):
                    pass
                if time.time() - t0 > 7200:
                    raise AltiumTimeout("Another Convi job is still using Altium")
                time.sleep(1.0)

    def __exit__(self, *a):
        os.close(self.fd)
        LOCK.unlink(missing_ok=True)


def run_script(script: Path, proc: str, done_marker: Path, **kw) -> None:
    """Start ``proc`` of ``script`` in Altium and wait until ``done_marker`` exists (serialized)."""
    with _AltiumLock():
        _run_script(script, proc, done_marker, **kw)


def _run_script(script: Path, proc: str, done_marker: Path, *, x2: Path | None = None,
                timeout: float = 3600.0, poll: float = 0.5, on_poll=None, started: Path | None = None) -> None:
    x2 = x2 or find_x2()
    script = Path(script).resolve()
    done_marker = Path(done_marker)
    if done_marker.exists():
        done_marker.unlink()
    started_fresh = not is_altium_running()
    arg = f"-RScriptingSystem:RunScriptFile(FileName={script}|ProcName={proc})"
    if started is not None and Path(started).exists():
        Path(started).unlink()
    subprocess.Popen([str(x2), arg], close_fds=True)
    t0 = time.time()
    sent_at = t0
    resends = 0
    # A freshly started Altium needs time to boot (license, extensions).
    limit = timeout + (600 if started_fresh else 0)
    last_dlg_check = 0.0
    while not done_marker.exists():
        if time.time() - last_dlg_check > 2.0:
            last_dlg_check = time.time()
            msg = _check_error_dialogs(script.parent)
            if msg:
                raise AltiumScriptError(msg)
        if time.time() - t0 > limit:
            raise AltiumTimeout(f"Altium did not finish within {limit:.0f} s (no {done_marker.name}).")
        # Altium silently ignores a run request while a previous script is still finishing:
        # if the script did not report that it started, ask again - but only when exactly one
        # Altium instance is running (a second X2.EXE would wait forever on Altium's start-up mutex).
        if started is not None and not Path(started).exists():
            if time.time() - sent_at > (90 if started_fresh else 20):
                n = x2_instances()
                if n == 0 and not started_fresh:
                    raise AltiumNotRunning("Altium Designer was closed while Convi was working. "
                                           "Start Altium again and re-run the conversion.")
                if n == 1 and resends < 30:
                    subprocess.Popen([str(x2), arg], close_fds=True)
                    resends += 1
                sent_at = time.time()
        if on_poll:
            on_poll(time.time() - t0)
        time.sleep(poll)
    # let the file be fully flushed
    time.sleep(0.2)


def _check_error_dialogs(shot_dir: Path | None = None) -> str | None:
    """Watch Altium dialogs while a script runs.

    Script errors ("Continue execution?", access violations) abort the run and their
    text is returned.  Other informational dialogs (e.g. Altium's database-library
    notice) are acknowledged with OK so the conversion keeps going.
    """
    try:
        from . import dialogs
    except Exception:
        return None
    try:
        for h in dialogs.dialogs():
            if dialogs._cls(h) == "TRemoveUnusedPadsOptions":
                # Tools > Remove Unused Pad Shapes, started by unusedpads.pas: answered as KiCad strips vias
                UNUSED_PADS["tries"] = UNUSED_PADS.get("tries", 0) + 1
                if UNUSED_PADS["tries"] > 2:
                    # UI Automation could not answer it: Escape (= Cancel; Enter would be OK with whatever
                    # scope Altium remembers)
                    dialogs.user32.PostMessageW(h, 0x0100, 0x1B, 0)
                    dialogs.user32.PostMessageW(h, 0x0101, 0x1B, 0)
                    UNUSED_PADS["result"] = "cancelled: the dialog could not be answered"
                else:
                    UNUSED_PADS["result"] = _answer_unused_pads(h, UNUSED_PADS.get("preserve", True))
                continue
            if dialogs._text(h) == "Unsaved Changes":
                if _dismiss_unsaved_changes(h):
                    IGNORED_DIALOGS.append("Unsaved Changes -> Don't Save")
                continue
            if dialogs.is_main(h):
                continue
            desc = dialogs.describe(h)
            if "Send and Close" in desc:
                # Altium's internal crash dialog: leave it to the user (it offers to send a report)
                return ("Altium reported an internal error (crash dialog with 'Send and Close'). "
                        "Close that dialog in Altium, restart Altium and run Convi again. " + desc)
            if ("Continue execution" in desc or "Access violation" in desc
                    or (dialogs._cls(h) == "TMessageForm" and dialogs._text(h) == "Error")):
                # next to the script (the _convi work folder, kept after a failure) so it can be inspected
                shot = (Path(shot_dir) if shot_dir else Path(os.environ.get("TEMP", "."))) / "altium_error.png"
                try:
                    dialogs.capture_window(h, str(shot))
                    desc += f" [picture: {shot}]"
                except Exception:
                    pass
                dialogs.close_dialogs()
                # Delphi message boxes ignore BM_CLICK on their bitmap buttons: press Enter as well
                dialogs.user32.PostMessageW(h, 0x0100, 0x0D, 0)
                dialogs.user32.PostMessageW(h, 0x0101, 0x0D, 0)
                return desc
            texts = {dialogs._text(c).replace("&", ""): c for c in dialogs.children(h)}
            if "No" in texts and "Yes" in texts:
                # save-changes prompts for Convi's temporary documents/projects
                dialogs.user32.SendMessageW(texts["No"], 0x00F5, 0, 0)
                IGNORED_DIALOGS.append(desc)
                continue
            buttons = [c for c in dialogs.children(h) if dialogs._text(c).replace("&", "") in ("OK", "Ok")]
            if buttons:
                dialogs.user32.SendMessageW(buttons[0], 0x00F5, 0, 0)
                IGNORED_DIALOGS.append(desc)
    except Exception:
        return None
    return None


IGNORED_DIALOGS: list[str] = []
UNUSED_PADS: dict = {}     # options for the Unused Pad Shapes dialog (pcbcheck.remove_unused_pads)

_UIA_UNUSED_PADS = r"""
$src = @'
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using Accessibility;

public static class ConviUnusedPads {
  delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr p, EnumProc f, IntPtr l);
  [DllImport("user32.dll")] static extern bool GetClientRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("oleacc.dll")] static extern int AccessibleObjectFromWindow(IntPtr h, uint id, ref Guid iid,
      [MarshalAs(UnmanagedType.IUnknown)] out object o);
  public struct RECT { public int L, T, R, B; }
  static Dictionary<string, IntPtr> map;

  static IAccessible Acc(IntPtr h) {
    Guid g = new Guid("618736E0-3C3D-11CF-810C-00AA00389B71");
    object o;
    AccessibleObjectFromWindow(h, 0xFFFFFFFC, ref g, out o);
    return (IAccessible)o;
  }
  static bool On(IntPtr h) { return (((int)Acc(h).get_accState(0)) & 0x10) != 0; }
  static void Mouse(IntPtr h) {
    RECT r; GetClientRect(h, out r);
    IntPtr p = (IntPtr)(((r.B / 2) << 16) | (r.R / 2));
    PostMessage(h, 0x0201, (IntPtr)1, p);
    PostMessage(h, 0x0202, IntPtr.Zero, p);
    System.Threading.Thread.Sleep(300);
  }
  static void Press(IntPtr h) {
    try { Acc(h).accDoDefaultAction(0); System.Threading.Thread.Sleep(300); }
    catch { Mouse(h); }
  }
  static void Set(string n, bool want) {
    IntPtr h = map[n];
    if (On(h) != want) Press(h);
    if (On(h) != want) Mouse(h);
    if (On(h) != want) throw new Exception("cannot set " + n);
  }
  public static string Run(long dlg, bool preserve) {
    IntPtr d = new IntPtr(dlg);
    map = new Dictionary<string, IntPtr>();
    EnumChildWindows(d, delegate(IntPtr h, IntPtr l) {
      try { string n = Acc(h).get_accName(0); if (n != null && !map.ContainsKey(n)) map[n] = h; } catch { }
      return true;
    }, IntPtr.Zero);
    foreach (string n in new string[] { "Vias", "Remove unused", "Selected only",
                                        "Preserve pads on start and end layers", "OK", "Cancel" })
      if (!map.ContainsKey(n)) return "cancelled: control not found: " + n;
    try {
      Set("Vias", true);
      Set("Remove unused", true);
      Set("Selected only", false);
      Set("Preserve pads on start and end layers", preserve);
    } catch (Exception e) {
      Press(map["Cancel"]);
      if (IsWindowVisible(d)) Mouse(map["Cancel"]);
      return "cancelled: " + e.Message;
    }
    Press(map["OK"]);
    if (IsWindowVisible(d)) Mouse(map["OK"]);
    System.Threading.Thread.Sleep(500);
    return IsWindowVisible(d) ? "set, but OK did not close the dialog" : "answered";
  }
}
'@
Add-Type -TypeDefinition $src -ReferencedAssemblies Accessibility -Language CSharp
[ConviUnusedPads]::Run(%HWND%, %PRESERVE%)
"""


def _answer_unused_pads(hwnd: int, preserve: bool) -> str:
    """Set the Unused Pad Shapes dialog (vias only, remove, start/end layers as asked, not 'selected only')
    and press OK; anything that cannot be set presses Cancel instead, so pads are never stripped by a
    remembered scope.  Its controls (TXPGroupButton, TXPCheckBox, TXPBitBtn) are windows that answer MSAA
    (state 'checked', default action); managed UI Automation offers no pattern for the radio buttons
    ('LegacyIAccessiblePattern' missing, nothing selectable), so this goes through oleacc."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                              _UIA_UNUSED_PADS.replace("%HWND%", str(hwnd))
                              .replace("%PRESERVE%", "$true" if preserve else "$false")],
                             capture_output=True, text=True, timeout=120)
        so, se = out.stdout.strip(), out.stderr.strip()
        if so:
            return so.splitlines()[-1]
        return "no answer from the dialog driver" + (": " + " ".join(se.split())[:300] if se else "")
    except Exception as e:
        return f"failed: {e}"

_UIA_DONT_SAVE = r"""
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$win = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]%HWND%)
$c = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)
foreach ($b in $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $c)) {
  if ($b.Current.Name -like "Don*t Save") { $b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke(); "ok" }
}
"""


def _dismiss_unsaved_changes(hwnd: int) -> bool:
    """Answer "Don't Save" in Altium's WPF 'Unsaved Changes' dialog (Convi's own documents)."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                              _UIA_DONT_SAVE.replace("%HWND%", str(hwnd))],
                             capture_output=True, text=True, timeout=60).stdout
        return "ok" in out
    except Exception:
        return False
