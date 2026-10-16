"""Feasibility, laptop side, for moving windows between computers
(docs/moving-windows.md, section 3): can a window leave this screen and still
draw, be captured, and take typing?  Only windows this script starts itself (a
Tk window, Notepad, Edge with a throwaway profile); all closed at the end.

    python tools/win-window-spike.py

For each window and each state - shown, covered, ghost (layered, alpha 1,
click-through), off-screen, minimized - capture it 6 times with PrintWindow
(PW_RENDERFULLCONTENT) and count how many different pictures came back: an
animated window still drawing gives several, a frozen one gives 1. Writes a
report, the test files and the throwaway Edge profile to a temp folder, not
the repository.
"""
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import pathlib
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
import zlib

HERE = pathlib.Path(tempfile.gettempdir()) / "nl-win-window-spike"
HERE.mkdir(exist_ok=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")
kernel32 = ctypes.WinDLL("kernel32")
ole32 = ctypes.WinDLL("ole32")
gdiplus = ctypes.WinDLL("gdiplus")
user32.SetProcessDPIAware()

HWND = wt.HWND
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.restype = ctypes.c_long
user32.GetForegroundWindow.restype = HWND
user32.FindWindowExW.restype = HWND
user32.GetDC.restype = wt.HDC
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p,
                            ctypes.c_void_p, wt.UINT]
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
user32.GetDC.argtypes = [HWND]
user32.ReleaseDC.argtypes = [HWND, wt.HDC]
user32.PrintWindow.argtypes = [HWND, wt.HDC, wt.UINT]
user32.GetWindowRect.argtypes = [HWND, ctypes.c_void_p]
user32.GetWindowLongW.argtypes = [HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [HWND, ctypes.c_int, ctypes.c_long]
user32.SetLayeredWindowAttributes.argtypes = [HWND, wt.DWORD, wt.BYTE, wt.DWORD]
user32.RedrawWindow.argtypes = [HWND, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
user32.SetWindowPos.argtypes = [HWND, HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wt.UINT]
user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [HWND]
user32.IsWindowVisible.argtypes = [HWND]
user32.GetWindowTextLengthW.argtypes = [HWND]
user32.GetWindowTextW.argtypes = [HWND, ctypes.c_wchar_p, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.c_void_p]
user32.FindWindowExW.argtypes = [HWND, HWND, ctypes.c_wchar_p, ctypes.c_wchar_p]
user32.SendMessageW.argtypes = [HWND, wt.UINT, wt.WPARAM, ctypes.c_void_p]
gdiplus.GdipCreateBitmapFromScan0.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                              ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p]
gdiplus.GdipSaveImageToStream.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                          ctypes.c_void_p]
gdiplus.GdipDisposeImage.argtypes = [ctypes.c_void_p]
ole32.CreateStreamOnHGlobal.argtypes = [ctypes.c_void_p, wt.BOOL, ctypes.c_void_p]
ole32.GetHGlobalFromStream.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]

GWL_EXSTYLE = -20
WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x80000, 0x20
LWA_ALPHA = 2
SW_MINIMIZE, SW_SHOWNOACTIVATE = 6, 4
SWP_NOZORDER, SWP_NOACTIVATE, SWP_NOSIZE = 0x4, 0x10, 0x1
PW_RENDERFULLCONTENT = 2


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


def rect(hwnd):
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def _grab(w, h, draw):
    screen = user32.GetDC(None)
    dc = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    old = gdi32.SelectObject(dc, bmp)
    ok = draw(dc, screen)
    bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(dc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.SelectObject(dc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(dc)
    user32.ReleaseDC(None, screen)
    return bool(ok), buf.raw


def capture(hwnd):
    """The window's own picture, wherever it is and whatever covers it."""
    _, _, w, h = rect(hwnd)
    t = time.perf_counter()
    ok, raw = _grab(w, h, lambda dc, _s: user32.PrintWindow(hwnd, dc, PW_RENDERFULLCONTENT))
    return ok, raw, w, h, (time.perf_counter() - t) * 1000


def on_screen(hwnd):
    """What the screen itself shows where the window is."""
    x, y, w, h = rect(hwnd)
    _, raw = _grab(w, h, lambda dc, s: gdi32.BitBlt(dc, 0, 0, w, h, s, x, y, 0x00CC0020))
    return raw


def changes(hwnd, n=6, gap=0.15):
    seen, ms, ok_all = set(), [], True
    for _ in range(n):
        ok, raw, w, h, t = capture(hwnd)
        ok_all &= ok
        ms.append(t)
        seen.add(hashlib.md5(raw).hexdigest())
        time.sleep(gap)
    return {"ok": ok_all, "distinct": len(seen), "ms": round(statistics.median(ms), 1),
            "size": f"{w}x{h}"}


def similar(a, b):
    """Share of sampled pixels that match closely: 1.0 = the same picture."""
    if len(a) != len(b) or not a:
        return 0.0
    step = max(4, (len(a) // 4 // 4000) * 4)
    same = total = 0
    for i in range(0, len(a) - 3, step):
        total += 1
        if all(abs(a[i + k] - b[i + k]) < 24 for k in range(3)):
            same += 1
    return round(same / total, 2)


def find(title, timeout=15.0):
    end = time.time() + timeout
    found = []
    cb_t = ctypes.WINFUNCTYPE(wt.BOOL, HWND, wt.LPARAM)

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                b = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, b, n + 1)
                if title in b.value:
                    found.append(hwnd)
        return True
    while time.time() < end:
        found.clear()
        user32.EnumWindows(cb_t(cb), 0)
        if found:
            return found[0]
        time.sleep(0.3)
    raise SystemExit(f"no window titled {title!r}")


def find_pid(pid, timeout=15.0):
    """The visible top-level window of THIS process - never another Notepad."""
    end = time.time() + timeout
    cb_t = ctypes.WINFUNCTYPE(wt.BOOL, HWND, wt.LPARAM)
    found = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and pid_of(hwnd) == pid:
            found.append(hwnd)
        return True
    while time.time() < end:
        found.clear()
        user32.EnumWindows(cb_t(cb), 0)
        if found:
            return found[0]
        time.sleep(0.3)
    raise SystemExit(f"no window of process {pid}")


def pid_of(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def ghost(hwnd, on, clickthrough=True):
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if on:
        add = WS_EX_LAYERED | (WS_EX_TRANSPARENT if clickthrough else 0)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | add)
        user32.SetLayeredWindowAttributes(hwnd, 0, 1, LWA_ALPHA)
    else:
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex & ~(WS_EX_LAYERED | WS_EX_TRANSPARENT))
        user32.RedrawWindow(hwnd, None, None, 0x0001 | 0x0004 | 0x0080 | 0x0100)


def move(hwnd, x, y):
    user32.SetWindowPos(hwnd, None, x, y, 0, 0, SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOSIZE)


# ------------------------------------------------------------ GDI+ encoding
class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, s):
        g = cls()
        ctypes.memmove(ctypes.byref(g), uuid.UUID(s).bytes_le, 16)
        return g


class EncoderParameter(ctypes.Structure):
    _fields_ = [("Guid", GUID), ("NumberOfValues", ctypes.c_ulong),
                ("Type", ctypes.c_ulong), ("Value", ctypes.c_void_p)]


class EncoderParameters(ctypes.Structure):
    _fields_ = [("Count", ctypes.c_uint), ("Parameter", EncoderParameter * 1)]


class StartupInput(ctypes.Structure):
    _fields_ = [("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", ctypes.c_void_p),
                ("SuppressBackgroundThread", wt.BOOL), ("SuppressExternalCodecs", wt.BOOL)]


JPEG = GUID.of("557CF401-1A04-11D3-9A73-0000F81EF32E")
PNG = GUID.of("557CF406-1A04-11D3-9A73-0000F81EF32E")
QUALITY = GUID.of("1D5BE4B5-FA4A-452D-9CDD-5DB35105E7EB")
_token = ctypes.c_size_t()
gdiplus.GdiplusStartup(ctypes.byref(_token), ctypes.byref(StartupInput(1, None, False, False)), None)


def encode(raw, w, h, fmt, quality=None):
    bitmap = ctypes.c_void_p()
    gdiplus.GdipCreateBitmapFromScan0(w, h, w * 4, 0x00022009, ctypes.c_char_p(raw),
                                      ctypes.byref(bitmap))
    stream = ctypes.c_void_p()
    ole32.CreateStreamOnHGlobal(None, True, ctypes.byref(stream))
    params = None
    if quality is not None:
        q = ctypes.c_ulong(quality)
        params = EncoderParameters(1)
        params.Parameter[0] = EncoderParameter(QUALITY, 1, 4, ctypes.cast(ctypes.byref(q), ctypes.c_void_p))
    st = gdiplus.GdipSaveImageToStream(bitmap, stream, ctypes.byref(fmt),
                                       ctypes.byref(params) if params else None)
    vtbl = ctypes.cast(stream, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    seek = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_longlong, ctypes.c_ulong,
                              ctypes.POINTER(ctypes.c_ulonglong))(vtbl[5])
    size = ctypes.c_ulonglong()
    seek(stream, 0, 1, ctypes.byref(size))
    hg = ctypes.c_void_p()
    ole32.GetHGlobalFromStream(stream, ctypes.byref(hg))
    p = kernel32.GlobalLock(hg)
    data = ctypes.string_at(p, size.value)
    kernel32.GlobalUnlock(hg)
    ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])(stream)
    gdiplus.GdipDisposeImage(bitmap)
    return st, data


def timed(fn, n=5):
    out, ms = None, []
    for _ in range(n):
        t = time.perf_counter()
        out = fn()
        ms.append((time.perf_counter() - t) * 1000)
    return out, round(statistics.median(ms), 1)


# ------------------------------------------------------------ the windows
TK = r'''
import tkinter as tk, time
r = tk.Tk(); r.title("NL spike tk"); r.geometry("520x300+40+60")
l = tk.Label(r, font=("Segoe UI", 40), bg="#123", fg="#fd0"); l.pack(fill="both", expand=1)
def tick():
    l.config(text="%.2f" % time.perf_counter()); r.after(40, tick)
tick(); r.mainloop()
'''
PAGE = """<!doctype html><title>NL spike edge</title>
<body style="margin:0;background:#202830;color:#9f9;font:56px Segoe UI">
<div id=t style="padding:40px"></div><canvas id=c width=700 height=200></canvas>
<script>
const x=document.getElementById('c').getContext('2d');
function f(now){t.textContent=now.toFixed(1);x.fillStyle='hsl('+(now/10%360)+',80%,50%)';
x.fillRect((now/3)%700,50,20,100);requestAnimationFrame(f)}requestAnimationFrame(f)
</script>"""
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def main():
    report = {}
    procs = []
    prev_fg = user32.GetForegroundWindow()
    (HERE / "anim.html").write_text(PAGE, encoding="utf-8")
    procs.append(subprocess.Popen([sys.executable, "-c", TK]))
    procs.append(subprocess.Popen(["notepad.exe"]))
    procs.append(subprocess.Popen([EDGE, f"--user-data-dir={HERE / 'edge-profile'}",
                                   "--no-first-run", "--no-default-browser-check",
                                   "--disable-sync", "--window-size=800,480",
                                   "--window-position=300,200",
                                   f"--app={(HERE / 'anim.html').as_uri()}"]))
    wins = {}
    extra_pids = set()
    try:
        wins["tk"] = find("NL spike tk")
        wins["notepad"] = find_pid(procs[1].pid)
        wins["edge"] = find("NL spike edge")
        extra_pids.add(pid_of(wins["edge"]))
        move(wins["notepad"], 120, 380)
        time.sleep(2.0)
        vs = user32.GetSystemMetrics(78)          # SM_CXVIRTUALSCREEN
        for name, hwnd in wins.items():
            r = {}
            home = rect(hwnd)
            r["shown"] = changes(hwnd)
            # covered: a topmost window of our own over it
            cover = subprocess.Popen([sys.executable, "-c",
                "import tkinter as tk; r=tk.Tk(); r.overrideredirect(1); "
                f"r.geometry('{home[2]}x{home[3]}+{home[0]}+{home[1]}'); "
                "r.attributes('-topmost',1); r.configure(bg='black'); "
                "r.after(3500, r.destroy); r.mainloop()"])
            time.sleep(1.5)
            r["covered"] = changes(hwnd)
            cover.wait(10)
            ghost(hwnd, True)
            time.sleep(1.0)
            r["ghost"] = changes(hwnd)
            _, own, *_ = capture(hwnd)
            r["ghost"]["screen_shows_window"] = similar(on_screen(hwnd), own)
            ghost(hwnd, False)
            time.sleep(0.5)
            _, own, *_ = capture(hwnd)
            r["restored_screen_shows_window"] = similar(on_screen(hwnd), own)
            move(hwnd, vs + 200, home[1])
            time.sleep(1.0)
            r["offscreen"] = changes(hwnd)
            move(hwnd, home[0], home[1])
            user32.ShowWindow(hwnd, SW_MINIMIZE)
            time.sleep(1.0)
            r["minimized"] = changes(hwnd)
            user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            time.sleep(0.8)
            report[name] = r
            print(name, json.dumps(r), flush=True)

        # typing into a ghost: Notepad, made ghost but hit-testable, focused
        np_ = wins["notepad"]
        ghost(np_, True, clickthrough=False)
        time.sleep(0.5)
        user32.mouse_event(0x0001, 0, 0, 0, 0)    # an input event of our own: a move of 0
        user32.SetForegroundWindow(np_)
        time.sleep(0.4)
        focused = user32.GetForegroundWindow() == np_
        typed = None
        if focused:
            for ch in "nl-ghost-typed":
                class KI(ctypes.Structure):
                    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

                class INP(ctypes.Structure):
                    _fields_ = [("type", wt.DWORD), ("ki", KI), ("pad", ctypes.c_ubyte * 8)]
                if user32.GetForegroundWindow() != np_:
                    break                                 # never type anywhere else
                for flags in (4, 4 | 2):                  # KEYEVENTF_UNICODE, then KEYUP
                    i = INP(1, KI(0, ord(ch), flags, 0, 0))
                    user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INP))
            time.sleep(0.4)
            edit = user32.FindWindowExW(np_, None, "Edit", None)
            b = ctypes.create_unicode_buffer(256)
            user32.SendMessageW(edit, 0x000D, 256, b)    # WM_GETTEXT
            typed = b.value
        ghost(np_, False)
        report["typing_into_ghost"] = {"got_focus": focused, "text": typed}
        print("typing_into_ghost", report["typing_into_ghost"], flush=True)

        # encoding one Edge frame, as the stream would
        _, raw, w, h, _ = capture(wins["edge"])
        (st_j, jpg), ms_j = timed(lambda: encode(raw, w, h, JPEG, 75))
        (st_p, png), ms_p = timed(lambda: encode(raw, w, h, PNG))
        z, ms_z = timed(lambda: zlib.compress(raw, 1))
        report["encode"] = {"size": f"{w}x{h}", "raw_kb": len(raw) // 1024,
                            "jpeg75": [st_j, len(jpg) // 1024, ms_j],
                            "png": [st_p, len(png) // 1024, ms_p],
                            "zlib1": [len(z) // 1024, ms_z]}
        print("encode", report["encode"], flush=True)
        (HERE / "edge-frame.jpg").write_bytes(jpg)
    finally:
        for hwnd in wins.values():
            try:
                ghost(hwnd, False)
            except Exception:
                pass
        for p in procs:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        for pid in extra_pids:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        if prev_fg:
            user32.SetForegroundWindow(prev_fg)
        (HERE / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
