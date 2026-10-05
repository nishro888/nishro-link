"""Every screenshot in docs/manual.md: the window in the dark theme, example
data only, with numbered callouts that match the manual's steps."""
import ctypes
import os
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "link", "tests"))
ctypes.windll.user32.SetProcessDPIAware()

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from link import config, control_api, ui_device, ui_pair, ui_theme, ui_tk  # noqa: E402
from link.desk import Desk  # noqa: E402
from link.node import Node, NodeCore, _Link  # noqa: E402
from link.runtime import RunLog  # noqa: E402
from test_node_live import FakeCapture, FakeInjector  # noqa: E402

OUT = os.path.join(REPO, "docs", "manual")
os.makedirs(OUT, exist_ok=True)
for f in os.listdir(OUT):
    # Not the setup wizard's pages: shot_setup.py makes those.
    if f.endswith(".png") and not (f.startswith("setup-") and f[6:7].isdigit()):
        os.remove(os.path.join(OUT, f))

AMBER = (255, 176, 32)
INK = (24, 24, 24)
BADGE = ImageFont.truetype(r"C:\Windows\Fonts\seguisb.ttf", 15)

control_api.my_addresses = lambda: ["192.168.1.10"]
ui_tk._log_path = lambda *a, **k: r"C:\ProgramData\NishroLink\link.log"
ui_theme.PREF_PATH = os.path.join(tempfile.mkdtemp(), "ui.json")
ui_theme.save_pref("dark")
tmp = tempfile.mkdtemp()


# ------------------------------------------------------------------ helpers
def settle(w, n=8):
    for _ in range(n):
        w.update()
        time.sleep(0.05)


from ctypes import wintypes  # noqa: E402

U32, G32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
U32.GetParent.argtypes = [wintypes.HWND]
U32.GetParent.restype = wintypes.HWND
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
G32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
G32.DeleteDC.argtypes = [wintypes.HDC]


def _render(win):
    """The window as it draws itself (PrintWindow, full content): never
    anything else on the screen - no other window, tooltip or pointer."""
    hwnd = U32.GetParent(win.winfo_id()) or win.winfo_id()
    r = wintypes.RECT()
    U32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    sdc = U32.GetDC(None)
    dc = G32.CreateCompatibleDC(sdc)
    bmp = G32.CreateCompatibleBitmap(sdc, w, h)
    old = G32.SelectObject(dc, bmp)
    U32.PrintWindow(hwnd, dc, 2)                    # PW_RENDERFULLCONTENT

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]
    bih = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    G32.SelectObject(dc, old)
    G32.GetDIBits(dc, bmp, 0, h, buf, ctypes.byref(bih), 0)
    G32.DeleteObject(bmp)
    G32.DeleteDC(dc)
    U32.ReleaseDC(None, sdc)
    return Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1), (r.left, r.top)


def grab(win, over=()):
    """The inside of a window (no title bar), with any popups `over` it drawn
    where they are; and where the inside is on the screen."""
    settle(win)
    img, (wx, wy) = _render(win)
    x, y = win.winfo_rootx(), win.winfo_rooty()
    for pop in over:
        pimg, (px, py) = _render(pop)
        img.paste(pimg, (px - wx, py - wy))
    cx, cy = x - wx, y - wy
    img = img.crop((cx, cy, cx + win.winfo_width(), cy + win.winfo_height()))
    return img, (x, y)


def box(w, origin, pad=4):
    x, y = w.winfo_rootx() - origin[0], w.winfo_rooty() - origin[1]
    return (x - pad, y - pad, x + w.winfo_width() + pad, y + w.winfo_height() + pad)


def ubox(origin, *ws, pad=4):
    """One box round several widgets."""
    ws = [w for w in ws if w is not None]
    if not ws:
        return None
    bs = [box(w, origin, pad) for w in ws]
    return (min(b[0] for b in bs), min(b[1] for b in bs),
            max(b[2] for b in bs), max(b[3] for b in bs))


def find(root, text, starts=False):
    """The widget showing `text` (depth first), or None."""
    stack = [root]
    while stack:
        w = stack.pop(0)
        try:
            t = str(w.cget("text"))
            if t == text or (starts and t.startswith(text)):
                return w
        except Exception:
            pass
        stack.extend(w.winfo_children())
    return None


def mark(img, marks):
    """Numbered amber callouts: an outline round each thing, and its number."""
    img = img.convert("RGB")
    d = ImageDraw.Draw(img)
    W, H = img.size
    for n, r in marks:
        if r is None:
            print("   (no widget for callout", n, ")")
            continue
        x0, y0, x1, y1 = [int(v) for v in r]
        x0, y0 = max(1, x0), max(1, y0)
        x1, y1 = min(W - 2, x1), min(H - 2, y1)
        d.rounded_rectangle((x0, y0, x1, y1), 6, outline=AMBER, width=2)
        cx, cy = max(13, min(W - 13, x0)), max(13, min(H - 13, y0))
        d.ellipse((cx - 12, cy - 12, cx + 12, cy + 12), fill=AMBER,
                  outline=(20, 20, 20), width=2)
        t = str(n)
        tw = d.textlength(t, font=BADGE)
        d.text((cx - tw / 2, cy - 10), t, font=BADGE, fill=INK)
    return img


def save(img, name, marks=()):
    if marks:
        img = mark(img, marks)
    img.save(os.path.join(OUT, name + ".png"), optimize=True)
    print("saved", name)


class Ev:
    def __init__(self, x, y):
        self.x_root, self.y_root = int(x), int(y)


def centre(w):
    return w.winfo_rootx() + w.winfo_width() // 2, w.winfo_rooty() + w.winfo_height() // 2


# -------------------------------------------------------------- example data
desk = Desk("laptop")
desk.add("laptop", 3286, 1080, parts=((0, 0, 1920, 1080), (1920, 148, 1366, 768)))
desk.add("desktop-1", 1920, 1080, owner="desktop-1", x=3286, y=0)
desk.add("desktop-2", 1920, 1080, owner="desktop-2", x=0, y=-1080)
desk.add_copy("laptop", x=3286 + 1920, y=0)
cfg = config.merge(config.DEFAULTS, {"node": "laptop", "hub": True,
                                     "pin": "tiger-lemon-coral-radio", "port": 8770,
                                     "placement": desk.boxes()})
now = time.time()
cfg["devices"] = [
    {"name": "desktop-1", "id": "a1", "addr": "192.168.1.20", "last_seen": now,
     "first_seen": now - 3 * 86400, "version": "1.0.0"},
    {"name": "desktop-2", "id": "a2", "addr": "192.168.1.40",
     "last_seen": now - 3 * 3600, "first_seen": now - 86400, "version": "1.0.0"}]
core = NodeCore("laptop", desk, cfg["policy"], is_hub=True, side="right")
core.peer_online("desktop-1")
node = Node(core, FakeCapture(), FakeInjector(), port=8770)
node.device_id = "3f9a1c07d2e84b56"
node.capture.start(node)


class Ch:
    def send(self, m):
        pass

    def close(self, flush=0.0):
        pass


lk = _Link("desktop-1", Ch(), ("192.168.1.20", 50000))
lk.rtt_ms = 1.8
node.links["desktop-1"] = lk
log = RunLog(os.path.join(tmp, "link.log"), echo=False)
for line in ["node 'laptop' 3286x1080  |  waiting for a device, on the right",
             "hub 'laptop' listening on 0.0.0.0:8770",
             "peer connected: desktop-1 (password verified both ways)",
             "peer connected: desktop-2 (password verified both ways) - 2 connected",
             "baton=desktop-1 epoch=3 cursor=desktop-1 suppress(mouse=1,kbd=1)",
             "find the pointer: it is on desktop-1 - asked it to show where",
             "baton=us epoch=4 cursor=laptop suppress(mouse=0,kbd=0)",
             "desktop-2 disconnected - 1 still connected",
             "rejected tablet at ('192.168.1.30', 51234): wrong password",
             "displays changed: 2 displays, desktop 3286x1080"]:
    log(line)
api = control_api.ControlAPI(node, cfg, log, cfg_path=os.path.join(tmp, "c.json"),
                             port=0)
app = ui_tk.App(api)
found = {"devices": [
    {"name": "desktop-3", "id": "k1", "waiting": True, "alone": True,
     "group": "desktop-3", "addr": "192.168.1.50"},
    {"name": "desktop-1", "id": "a1", "waiting": False, "group": "laptop"}]}
app._nearby_search = lambda: found
app._confirm = lambda *a: True
app.root.geometry("1080x700+20+0")
app.root.attributes("-topmost", True)
settle(app.root, 15)


def page(name):
    app.show_page(name)
    app._poll_now()
    settle(app.root, 10)


# ====================================================================== Home
page("overview")
img, o = grab(app.root)
save(img, "home", [
    (1, box(app.sidebar, o, 0)),
    (2, box(app.title.master, o)),
    (3, box(app.chip, o)),
    (4, box(app.sharing_switch.master.master, o)),
    (5, box(app.hero_card, o, 0)),
    (6, box(app.btn_find.master, o)),
    (7, box(app.stat_card, o, 0)),
    (8, box(app._metrics, o, 0)),
    (9, box(app._prev_card, o, 0))])

# ============================================== find the pointer, as it looks
page("overview")
img, o = grab(app.root)
base = img.convert("RGBA")
W, H = base.size
px = app.btn_find.winfo_rootx() - o[0] + app.btn_find.winfo_width() // 2
py = app.btn_find.winfo_rooty() - o[1] + app.btn_find.winfo_height() // 2
radius = 90
dark = Image.alpha_composite(base, Image.new("RGBA", base.size, (0, 0, 0, 150)))
keep = Image.new("L", base.size, 0)
ImageDraw.Draw(keep).ellipse((px - radius, py - radius, px + radius, py + radius),
                             fill=255)
base = Image.composite(base, dark, keep)
arrow = [(0, 0), (0, 26), (7, 20), (12, 31), (17, 29), (12, 18), (21, 18)]
dr = ImageDraw.Draw(base)
dr.polygon([(px + x, py + y) for x, y in arrow], fill=(255, 255, 255), outline=(0, 0, 0))
save(base, "spotlight")

# ============================================================ narrow window
app.root.geometry("720x640+20+0")
page("overview")
settle(app.root, 15)
img, o = grab(app.root)
save(img, "narrow")
app.root.geometry("1080x700+20+0")
settle(app.root, 10)

# Sharing off - the top of the window is enough.
api.command("/api/disable", {})
app._poll_now()
settle(app.root, 10)
img, o = grab(app.root)
img = img.crop((200, 0, img.width, 150))
save(img, "sharing-off")
api.command("/api/enable", {})
app._poll_now()
settle(app.root, 6)

# The firewall banner (Windows).
api.firewall_blocked = ["Nishro Link|TCP|Private"]
app._poll_now()
settle(app.root, 10)
img, o = grab(app.root)
save(img.crop((200, 0, img.width, 210)), "firewall-banner",
     [(1, (app.fw_btn.winfo_rootx() - o[0] - 200 - 4, app.fw_btn.winfo_rooty() - o[1] - 4,
           app.fw_btn.winfo_rootx() - o[0] - 200 + app.fw_btn.winfo_width() + 4,
           app.fw_btn.winfo_rooty() - o[1] + app.fw_btn.winfo_height() + 4))])
api.firewall_blocked = []
app._poll_now()
settle(app.root, 6)

# The public network banner (Windows) - an example network name.
api.network_public = ["Home-WiFi"]
app._poll_now()
settle(app.root, 10)
img, o = grab(app.root)
b = app.net_btn
save(img.crop((200, 0, img.width, 210)), "network-banner",
     [(1, (b.winfo_rootx() - o[0] - 200 - 4, b.winfo_rooty() - o[1] - 4,
           b.winfo_rootx() - o[0] - 200 + b.winfo_width() + 4,
           b.winfo_rooty() - o[1] + b.winfo_height() + 4))])
api.network_public = []
app._poll_now()
settle(app.root, 6)

# The Linux permissions banner.
real_status = api.status
api.status = lambda: dict(real_status(), setup={
    "problems": ["Your account is not in the 'input' group yet."],
    "fixable": True, "relogin": False})
app._poll_now()
settle(app.root, 10)
img, o = grab(app.root)
b = app.setup_btn
save(img.crop((200, 0, img.width, 210)), "setup-banner",
     [(1, (b.winfo_rootx() - o[0] - 200 - 4, b.winfo_rooty() - o[1] - 4,
           b.winfo_rootx() - o[0] - 200 + b.winfo_width() + 4,
           b.winfo_rooty() - o[1] + b.winfo_height() + 4))])
api.status = lambda: dict(real_status(), setup={
    "problems": ["Log out and back in once."], "fixable": False, "relogin": True})
app._poll_now()
settle(app.root, 10)
img, o = grab(app.root)
save(img.crop((200, 0, img.width, 210)), "setup-relogin")
api.status = real_status
app._poll_now()
settle(app.root, 6)

# =================================================================== Devices
page("devices")
app._nearby_search = lambda: found
try:
    app._refresh_nearby()
except Exception:
    pass
settle(app.root, 25)
img, o = grab(app.root)
dp = app.pages["devices"]
pw = find(dp, "tiger-lemon-coral-radio")
save(img, "devices", [
    (1, box(pw.master if pw else None, o) if pw else None),
    (2, box(find(dp, "Copy"), o) if find(dp, "Copy") else None),
    (3, box(find(dp, "New password"), o) if find(dp, "New password") else None),
    (4, box(app.btn_rename_me.master, o)),
    (5, box(find(dp, "+  Add a device"), o) if find(dp, "+  Add a device") else None),
    (6, box(app.dev_list, o, 2)),
    (7, box(find(dp, "Nearby").master, o, 2) if find(dp, "Nearby") else None)])

# The right-click menu on a device.
card = find(app.dev_list, "desktop-1")
x, y = centre(card)
app._card_menu(Ev(x + 40, y + 10), "desktop-1")
settle(app.root, 10)
img, o = grab(app.root, over=[app._menu.top])
save(img, "device-menu", [(1, box(app._menu.top, o, 2))])
try:
    app._menu.close()
except Exception:
    pass
settle(app.root, 5)

# Details of another device, then renaming it.
det = ui_device.DeviceDetails(app.root, api, app.C, "desktop-1",
                              confirm=lambda *a: False)
det.top.attributes("-topmost", True)
settle(app.root, 15)
img, o = grab(det.top)
t = det.top
save(img, "details", [
    (1, box(det.name_label.master.master, o)),
    (2, box(find(t, "Rename"), o) if find(t, "Rename") else None),
    (3, ubox(o, find(t, "Displays").master, find(t, "Device ID").master)),
    (4, ubox(o, find(t, "Can control other devices").master,
             find(t, "Can be controlled").master)),
    (5, box(find(t, "Remove from the group"), o)
     if find(t, "Remove from the group") else None)])
det._start_rename()
settle(app.root, 8)
img, o = grab(det.top)
save(img, "details-rename", [(1, box(det.f_name, o))])
det.close()
settle(app.root, 5)

# ============================================================ Adding a device
d = ui_pair.AddDevice(app.root, api, app.C, search=lambda: found)
d.top.attributes("-topmost", True)
settle(app.root, 30)
img, o = grab(d.top)
t = d.top
add_btn = find(t, "Add")
row3 = find(t, "desktop-3")
save(img, "add-list", [
    (1, box(row3.master, o, 2) if row3 else None),
    (2, box(add_btn, o) if add_btn else None),
    (3, box(find(t, "Search again"), o) if find(t, "Search again") else None),
    (4, box(find(t, "Not listed?").master, o, 2) if find(t, "Not listed?") else None),
    (5, box(find(t, "Pair from the other device").master, o, 2)
     if find(t, "Pair from the other device") else None)])

d._password("invite", "desktop-3")
d.f_pin.delete(0, "end")
d.f_pin.insert(0, "bench-grape-molar-stump")
settle(app.root, 8)
img, o = grab(d.top)
save(img, "add-password", [(1, box(d.f_pin, o)), (2, box(d.steps, o)),
                           (3, box(d.btn_go, o))])
d.f_pin.configure(state="disabled")
d.btn_go.set_enabled(False)
d._draw_steps(1)
settle(app.root, 6)
img, o = grab(d.top)
save(img, "add-progress")
d._failed({"reason": "wrong_password"})
settle(app.root, 6)
img, o = grab(d.top)
save(img, "add-wrong", [(1, ubox(o, d.msg, d.msg_detail, pad=3))])
d._done({})
settle(app.root, 6)
img, o = grab(d.top)
save(img, "add-done", [(1, box(find(d.top, "Arrange  →"), o)
                           if find(d.top, "Arrange  →") else None)])
d.close()
settle(app.root, 5)

# =============================================================== Arrangement
page("arrange")
a = app.arranger
a.select("desktop-1")
app._arr_text()
settle(app.root, 10)
img, o = grab(app.root)
cv = a.canvas


def canvas_box(key, pad=6):
    p = a.desk.instance(key)
    x0, y0 = a._to_screen(p.x, p.y)
    x1, y1 = a._to_screen(p.x + p.ww, p.y + p.wh)
    cx, cy = cv.winfo_rootx() - o[0], cv.winfo_rooty() - o[1]
    return (cx + x0 - pad, cy + y0 - pad, cx + x1 + pad, cy + y1 + pad)


tb = find(app.pages["arrange"], "In a row").master
d1 = canvas_box("desktop-1", 0)
line = (d1[0] - 7, (d1[1] + d1[3]) // 2, d1[0] + 7, d1[3] - 8)
legend = find(app.pages["arrange"], "pointer crosses here")
save(img, "arrange", [
    (1, ubox(o, find(tb, "In a row"), find(tb, "In a column"))),
    (2, ubox(o, app.arr_btns["copy"], app.arr_btns["aspect"], app.arr_btns["actual"])),
    (3, box(app.btn_undo, o)),
    (4, canvas_box("desktop-1")),
    (5, line),
    (6, canvas_box(("laptop", 1))),
    (7, box(app.arr_info, o, 2)),
    (8, box(legend.master, o, 2) if legend else None)])

# The right-click menu on the selected box.
r = canvas_box("desktop-1", 0)
app._arr_menu(Ev(o[0] + (r[0] + r[2]) // 2, o[1] + (r[1] + r[3]) // 2))
settle(app.root, 10)
img, o = grab(app.root, over=[app._menu.top])
save(img, "arrange-menu", [(1, box(app._menu.top, o, 2))])
try:
    app._menu.close()
except Exception:
    pass
settle(app.root, 5)

# Resizing: the handles.
a.select("desktop-2")
app._arr_text()
settle(app.root, 8)
img, o = grab(app.root)
save(img, "arrange-resize", [(1, canvas_box("desktop-2", 10))])

# A problem, said in words: a machine moved away from everything.
a.set_boxes = lambda boxes: None
a.desk.move("desktop-2", -2600, -1500)
a.redraw()
a.select("desktop-2")
app._arr_text()
settle(app.root, 10)
img, o = grab(app.root)
save(img, "arrange-problem", [(1, canvas_box("desktop-2")), (2, box(app.arr_problems, o, 2))])

# ================================================================= Activity
page("activity")
img, o = grab(app.root)
act = app.pages["activity"]
save(img, "activity", [(1, box(find(act, "Open the log folder"), o)
                            if find(act, "Open the log folder") else None)])

# ================================================================= Settings
page("settings")
img, o = grab(app.root)
st = app.pages["settings"]
save(img, "settings-top", [
    (1, box(find(st, "This device").master.master, o, 0)
     if find(st, "This device") else None),
    (2, box(find(st, "What takes control").master, o, 2)
     if find(st, "What takes control") else None)])


def scroll_down(pg):
    stack = [pg]
    while stack:
        w = stack.pop(0)
        if hasattr(w, "_canvas") and hasattr(w, "inner"):
            w._canvas.yview_moveto(1.0)
            return True
        stack.extend(w.winfo_children())
    return False


scroll_down(st)
settle(app.root, 10)
img, o = grab(app.root)
save(img, "settings-bottom", [
    (1, box(find(st, "Startup").master.master, o, 0) if find(st, "Startup") else None),
    (2, box(find(st, "Appearance").master.master, o, 0)
     if find(st, "Appearance") else None),
    (3, box(find(st, "Security").master.master, o, 0) if find(st, "Security") else None),
    (4, box(find(st, "Save"), o) if find(st, "Save") else None)])

# ===================================================================== Help
page("help")
img, o = grab(app.root)
hp = app.pages["help"]
save(img, "help", [(1, box(find(hp, "Copy details"), o) if find(hp, "Copy details") else None)])
scroll_down(hp)
settle(app.root, 10)
img, o = grab(app.root)
save(img, "help-lower")

app._alive = False
app.root.destroy()
node.stop()
print("done")
