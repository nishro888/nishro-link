"""docs/manual/tray-menu.png (the real Windows tray menu, example data only,
Devices open, numbered callouts) and docs/manual/tray-states.png (the icon's
three states, drawn from link/icon.py)."""
import base64
import ctypes
import io
import os
import sys
import threading
import time
from ctypes import wintypes

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
ctypes.windll.user32.SetProcessDPIAware()

from PIL import Image, ImageDraw, ImageFont, ImageGrab  # noqa: E402

from link import tray, tray_win  # noqa: E402
from link.icon import TRAY  # noqa: E402

OUT = os.path.join(REPO, "docs", "manual")
AMBER = (255, 176, 32)
INK = (24, 24, 24)
BG = (32, 32, 36)
BADGE = ImageFont.truetype(r"C:\Windows\Fonts\seguisb.ttf", 15)
LABEL = ImageFont.truetype(r"C:\Windows\Fonts\segoeui.ttf", 15)

u32 = tray_win.u32
tray_win._types()
u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u32.IsWindowVisible.argtypes = [wintypes.HWND]
u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
u32.GetMenuItemRect.argtypes = [wintypes.HWND, wintypes.HMENU, wintypes.UINT,
                                ctypes.POINTER(wintypes.RECT)]
u32.GetSubMenu.argtypes = [wintypes.HMENU, ctypes.c_int]
u32.GetSubMenu.restype = wintypes.HMENU
ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
u32.EnumWindows.argtypes = [ENUM, wintypes.LPARAM]
WM_KEYDOWN, VK_ESCAPE, VK_RIGHT, VK_DOWN = 0x0100, 0x1B, 0x27, 0x28

now = time.time()
status = {"enabled": True, "role": "hub", "connected": True, "group": "laptop",
          "setup": None, "notify": True,
          "devices": [{"name": "laptop", "online": True, "me": True, "hub": True},
                      {"name": "desktop-1", "online": True, "me": False, "hub": False},
                      {"name": "desktop-2", "online": False, "me": False, "hub": False}]}
items = tray.menu(tray.state(status))
DEVICES = next(i for i, it in enumerate(items) if it["kind"] == "sub")


def menus():
    found = []

    def each(h, _):
        buf = ctypes.create_unicode_buffer(64)
        u32.GetClassNameW(h, buf, 64)
        if buf.value == "#32768" and u32.IsWindowVisible(h):
            found.append(h)
        return True
    u32.EnumWindows(ENUM(each), 0)
    return found


def rect(h):
    r = wintypes.RECT()
    u32.GetWindowRect(h, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def item_rect(hmenu, i):
    r = wintypes.RECT()
    u32.GetMenuItemRect(None, hmenu, i, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


owner = tray_win.WinTray(log=print)          # its hidden window only; no icon
ids = {}
h, default = tray_win.build_menu(items, ids)
u32.SetMenuDefaultItem(h, default, False)
sub = u32.GetSubMenu(h, DEVICES)
shot = {}


u32.GetMenuState.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
MF_BYPOSITION, MF_HILITE, MN_SELECTITEM = 0x400, 0x80, 0x1E5


def lit(hmenu, n):
    return [i for i in range(n) if u32.GetMenuState(hmenu, i, MF_BYPOSITION) & MF_HILITE]


def driver():
    time.sleep(0.8)
    m = menus()
    if not m:
        print("no menu showed")
        return
    u32.PostMessageW(m[0], MN_SELECTITEM, DEVICES, 0)
    time.sleep(0.4)
    print("lit before opening:", lit(h, len(items)))
    u32.PostMessageW(m[0], WM_KEYDOWN, VK_RIGHT, 0)
    time.sleep(0.8)
    others = [w for w in menus() if w != m[0]]
    for w in others:                         # opened by key, its first row is lit
        u32.PostMessageW(w, MN_SELECTITEM, 0xFFFFFFFF, 0)
    time.sleep(0.5)
    print("menus:", len(menus()), "lit main:", lit(h, len(items)),
          "lit sub:", lit(sub, len(items[DEVICES]["items"])))
    shown = menus()
    shot["menus"] = [(rect(w), ImageGrab.grab(bbox=rect(w), all_screens=True))
                     for w in shown]
    shot["main"] = [item_rect(h, i) for i in range(len(items))]
    shot["sub"] = [item_rect(sub, i) for i in range(len(items[DEVICES]["items"]))]
    for _ in range(3):
        for w in menus():
            u32.PostMessageW(w, WM_KEYDOWN, VK_ESCAPE, 0)
        time.sleep(0.2)


threading.Thread(target=driver, daemon=True).start()
u32.SetForegroundWindow(owner.hwnd)
u32.TrackPopupMenu(h, tray_win.TPM_RIGHTBUTTON | tray_win.TPM_RETURNCMD, 900, 260, 0,
                   owner.hwnd, None)
u32.DestroyMenu(h)
time.sleep(0.2)

# ---------------------------------------------------------------- compose
grabs = shot["menus"]
x0 = min(r[0] for r, _ in grabs)
y0 = min(r[1] for r, _ in grabs)
x1 = max(r[2] for r, _ in grabs)
y1 = max(r[3] for r, _ in grabs)
PADL, PAD = 44, 20
W, H = x1 - x0 + PADL + PAD, y1 - y0 + 2 * PAD
img = Image.new("RGB", (W, H), BG)
# the main menu first, the submenu over it
for r, g in sorted(grabs, key=lambda t: t[0][0]):
    img.paste(g, (r[0] - x0 + PADL, r[1] - y0 + PAD))


def local(r):
    return (r[0] - x0 + PADL, r[1] - y0 + PAD, r[2] - x0 + PADL, r[3] - y0 + PAD)


def union(*rs):
    return (min(r[0] for r in rs), min(r[1] for r in rs), max(r[2] for r in rs),
            max(r[3] for r in rs))


main = [local(r) for r in shot["main"]]
subr = [local(r) for r in shot["sub"]]
at = {it.get("action") or it["kind"]: i for i, it in enumerate(items)}
marks = [(1, main[0]),
         (2, main[at["sharing"]]),
         (3, union(main[at["find"]], main[at["release"]])),
         (4, union(main[DEVICES], *subr)),
         (5, main[at["open"]]),
         (6, main[at["hide"]]),
         (7, main[at["quit"]])]
d = ImageDraw.Draw(img)
for n, r in marks:
    a, b, c, e = r
    d.rounded_rectangle((a - 2, b + 1, c + 2, e - 1), 5, outline=AMBER, width=2)
for n, r in marks:
    cy = (r[1] + r[3]) // 2
    cx = PADL - 20
    if n == 4:                               # its box spans both menus: badge at the row
        cy = (main[DEVICES][1] + main[DEVICES][3]) // 2
    d.line((cx + 12, cy, r[0] - 2, cy), fill=AMBER, width=2)
    d.ellipse((cx - 12, cy - 12, cx + 12, cy + 12), fill=AMBER, outline=(20, 20, 20),
              width=2)
    t = str(n)
    d.text((cx - d.textlength(t, font=BADGE) / 2, cy - 10), t, font=BADGE, fill=INK)
img.save(os.path.join(OUT, "tray-menu.png"), optimize=True)
print("saved tray-menu", img.size)

# ------------------------------------------------------------ the three states
labels = [("ok", "Connected, or ready"), ("paused", "Sharing is off"),
          ("alert", "Needs attention")]
cell = 230
st = Image.new("RGB", (cell * 3 + 20, 96), BG)
d = ImageDraw.Draw(st)
for k, (state, text) in enumerate(labels):
    x = 20 + k * cell
    d.rounded_rectangle((x, 16, x + 64, 80), 8, fill=(22, 22, 26))
    ic = Image.open(io.BytesIO(base64.b64decode(TRAY[state][32]))).convert("RGBA")
    st.paste(ic, (x + 16, 32), ic)
    d.text((x + 78, 38), text, font=LABEL, fill=(230, 230, 230))
st.save(os.path.join(OUT, "tray-states.png"), optimize=True)
print("saved tray-states", st.size)
