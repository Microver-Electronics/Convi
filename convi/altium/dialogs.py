"""Developer helper: inspect / dismiss modal dialogs of the running Altium (X2.exe).

Usage:
  py devtools/altium_dlg.py list          # list visible X2 windows with texts
  py devtools/altium_dlg.py close         # press the default button of every dialog
  py devtools/altium_dlg.py shot out.png  # screenshot of the primary screen
"""
import ctypes
import ctypes.wintypes as wt
import sys

user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass
kernel32 = ctypes.windll.kernel32

EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def _text(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _cls(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _pid(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def x2_pids():
    import subprocess
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq X2.EXE", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    pids = set()
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def children(hwnd):
    res = []

    def cb(h, _):
        res.append(h)
        return True
    user32.EnumChildWindows(hwnd, EnumWindowsProc(cb), 0)
    return res


def dialogs():
    pids = x2_pids()
    found = []

    def cb(h, _):
        if user32.IsWindowVisible(h) and _pid(h) in pids:
            found.append(h)
        return True
    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found


def describe(h):
    texts = []
    for c in children(h):
        t = _text(c)
        if t:
            texts.append(f"{_cls(c)}:{t}")
    return f"hwnd={h} class={_cls(h)} title={_text(h)!r} children={texts}"


DIALOG_CLASSES = ("TMessageForm", "#32770", "TScriptMessageForm", "TDialogForm")


def is_main(h):
    """True for anything that must never be closed (main frame, TApplication...)."""
    return _cls(h) not in DIALOG_CLASSES


def capture_window(hwnd, out_path) -> bool:
    """Render a window via PrintWindow into a PNG (content of that window only)."""
    from PIL import Image
    gdi32 = ctypes.windll.gdi32
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return False
    hdc = user32.GetWindowDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mdc, bmp)
    ok = user32.PrintWindow(hwnd, mdc, 2)  # PW_RENDERFULLCONTENT
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]
    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth, bi.biHeight, bi.biPlanes, bi.biBitCount = w, -h, 1, 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(hwnd, hdc)
    if not ok:
        return False
    Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB").save(out_path)
    return True


def close_dialogs():
    """Click OK/Close on message dialogs only. Never sends WM_CLOSE to anything."""
    BM_CLICK = 0x00F5
    closed = []
    for h in dialogs():
        if is_main(h):
            continue
        desc = describe(h)
        wanted = ("OK", "Ok", "Close", "Abort")
        if "Continue execution?" in desc:
            wanted = ("Cancel",)
        for c in children(h):
            if _text(c).replace("&", "") in wanted:
                user32.SendMessageW(c, BM_CLICK, 0, 0)
                closed.append(desc)
                break
    return closed


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        for h in dialogs():
            print(describe(h))
    elif cmd == "close":
        for d in close_dialogs():
            print("closed:", d)
    elif cmd == "shot":
        # Render ONLY the Altium window itself (PrintWindow), never the screen,
        # so other applications are never captured even if they cover Altium.
        target = sys.argv[3] if len(sys.argv) > 3 else "main"
        hs = [h for h in dialogs() if (_cls(h) == "TDocumentForm") == (target == "main") and not
              (target != "main" and is_main(h))]
        if not hs:
            print("window not found")
            sys.exit(1)
        print("saved" if capture_window(hs[0], sys.argv[2]) else "capture failed", sys.argv[2])
