"""Screenshots of the Windows setup wizard, for the manual.

Needs Inno Setup 6. Note: Windows Defender has been blocking setups compiled
on a developer's machine (a false positive - see RELEASING.md); this one too.

Builds a copy of NishroLink.iss that needs no administrator rights and does
nothing when run (no [Run], no [Code], no shortcuts), walks its pages by
clicking its own buttons, captures each page with PrintWindow, and cancels
before anything is copied."""
import ctypes
import os
import re
import subprocess
import sys
import time
from ctypes import wintypes

from PIL import Image

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ISS = os.path.join(REPO, "link", "packaging", "windows", "NishroLink.iss")
SHOTS_ISS = os.path.join(REPO, "link", "packaging", "windows", "_shots.iss")
OUT = os.path.join(REPO, "docs", "manual")
HERE = __import__("tempfile").mkdtemp(prefix="nishro-link-setup-shots-")
ISCC = [p for p in (os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
                    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
                    r"C:\Program Files\Inno Setup 6\ISCC.exe") if os.path.exists(p)][0]
ctypes.windll.user32.SetProcessDPIAware()
U32, G32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")

# ------------------------------------------------------------ the variant
src = open(ISS, encoding="utf-8").read()
src = src.replace("{{6D3B5E2A-4C1F-4B8E-9E2D-5A7F1C3B8D40}",
                  "{{0F0F0F0F-5C1F-4B8E-9E2D-5A7F1C3B8D41}")
src = src.replace("PrivilegesRequired=admin",
                  "PrivilegesRequired=lowest\nDirExistsWarning=no")
src = src.replace("DefaultDirName={autopf}\\Nishro Link",
                  "DefaultDirName=C:\\Program Files\\Nishro Link")
src = src.replace("OutputBaseFilename=NishroLink-Setup-{#AppVersion}",
                  "OutputBaseFilename=NishroLink-Setup-shots")
src = src.replace("OutputDir=..\\..\\..\\dist", "OutputDir=" + HERE)
for section in ("Icons", "Run", "Code"):          # nothing that does anything
    src = re.sub(r"\[" + section + r"\].*?(?=^\[|\Z)", "", src, flags=re.S | re.M)
open(SHOTS_ISS, "w", encoding="utf-8").write(src)
try:
    r = subprocess.run([ISCC, "/Q", "/DAppVersion=1.0.0", SHOTS_ISS],
                       capture_output=True, text=True)
    print("compile:", r.returncode, r.stderr[-400:])
finally:
    os.remove(SHOTS_ISS)
exe = os.path.join(HERE, "NishroLink-Setup-shots.exe")

# ----------------------------------------------------------- window helpers
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
U32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
U32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
U32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
U32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
U32.IsWindowVisible.argtypes = [wintypes.HWND]
U32.IsWindowEnabled.argtypes = [wintypes.HWND]
U32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
U32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
U32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
U32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
U32.GetDC.restype = wintypes.HDC
U32.GetDC.argtypes = [wintypes.HWND]
U32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
G32.CreateCompatibleDC.restype = wintypes.HDC
G32.CreateCompatibleDC.argtypes = [wintypes.HDC]
G32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
G32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
G32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
G32.SelectObject.restype = wintypes.HGDIOBJ
G32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
BM_CLICK = 0x00F5


def text(h):
    b = ctypes.create_unicode_buffer(256)
    U32.GetWindowTextW(h, b, 256)
    return b.value


def cls(h):
    b = ctypes.create_unicode_buffer(128)
    U32.GetClassNameW(h, b, 128)
    return b.value


def top_windows(prefix):
    out = []

    def cb(h, _):
        if U32.IsWindowVisible(h) and text(h).startswith(prefix):
            out.append(h)
        return True
    U32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def children(h):
    out = []

    def cb(c, _):
        out.append(c)
        return True
    U32.EnumChildWindows(h, WNDENUMPROC(cb), 0)
    return out


def button(h, *labels):
    """By caption, ignoring '&' and a trailing '>' (Inno Setup 6 has none)."""
    want = {x.replace(">", "").strip() for x in labels}
    for c in children(h):
        if U32.IsWindowVisible(c) and text(c).replace("&", "").replace(">", "").strip() in want:
            return c
    return None


def shot(h, name):
    time.sleep(0.6)
    r = wintypes.RECT()
    U32.GetWindowRect(h, ctypes.byref(r))
    w, hh = r.right - r.left, r.bottom - r.top
    sdc = U32.GetDC(None)
    dc = G32.CreateCompatibleDC(sdc)
    bmp = G32.CreateCompatibleBitmap(sdc, w, hh)
    old = G32.SelectObject(dc, bmp)
    U32.PrintWindow(h, dc, 2)

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]
    bih = BIH(ctypes.sizeof(BIH), w, -hh, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * hh * 4)
    G32.SelectObject(dc, old)
    G32.GetDIBits(dc, bmp, 0, hh, buf, ctypes.byref(bih), 0)
    Image.frombuffer("RGB", (w, hh), buf.raw, "raw", "BGRX", 0, 1).save(
        os.path.join(OUT, name + ".png"), optimize=True)
    print("saved", name)


def click(c):
    """Posted, not sent: a click that opens a message box must not hang us."""
    if c:
        U32.PostMessageW(c, BM_CLICK, 0, 0)
    time.sleep(0.8)


# ------------------------------------------------------------------- walk it
proc = subprocess.Popen([exe])
wiz = None
for _ in range(60):
    ws = top_windows("Setup - Nishro Link")
    if ws:
        wiz = ws[0]
        break
    time.sleep(0.25)
if wiz is None:
    sys.exit("no wizard window")
time.sleep(1.0)
page = 1
for _ in range(8):
    t = [text(c) for c in children(wiz) if U32.IsWindowVisible(c)]
    joined = " | ".join(t)
    print("PAGE", page, [x for x in t if x][:30])
    if "License Agreement" in joined:
        shot(wiz, f"setup-{page}-license")
        acc = button(wiz, "I accept the agreement")
        if acc:
            click(acc)
    elif "Select Destination Location" in joined:
        shot(wiz, f"setup-{page}-folder")
    elif "Select Additional Tasks" in joined:
        shot(wiz, f"setup-{page}-tasks")
    elif "Ready to Install" in joined:
        shot(wiz, f"setup-{page}-ready")
        break
    else:
        shot(wiz, f"setup-{page}-page")
    page += 1
    nxt = button(wiz, "Next >")
    if not nxt:
        break
    click(nxt)

# Cancel, and confirm: nothing has been copied.
click(button(wiz, "Cancel"))
for _ in range(20):
    for d in top_windows("Exit Setup"):
        b = button(d, "Yes")
        if b:
            click(b)
    if proc.poll() is not None:
        break
    time.sleep(0.25)
time.sleep(0.5)
if proc.poll() is None:
    proc.kill()
os.remove(exe)
print("done")
