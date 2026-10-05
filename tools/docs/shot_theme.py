"""The README's screenshots: the window in one theme (argv[1]: dark|light),
with example data only. With "publish" as argv[2] they go to docs/screenshots;
without, to a temporary folder, to look at first."""
import os
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODE = sys.argv[1]
PUBLISH = len(sys.argv) > 2 and sys.argv[2] == "publish"
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "link", "tests"))

import ctypes  # noqa: E402
from ctypes import wintypes  # noqa: E402

from link import config, control_api, ui_pair, ui_theme, ui_tk  # noqa: E402
from link.desk import Desk  # noqa: E402
from link.node import Node, NodeCore, _Link  # noqa: E402
from link.runtime import RunLog  # noqa: E402
from test_node_live import FakeCapture, FakeInjector  # noqa: E402

ctypes.windll.user32.SetProcessDPIAware()
U = ctypes.WinDLL("user32")
U.GetParent.argtypes = [wintypes.HWND]
U.GetParent.restype = wintypes.HWND
U.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

control_api.my_addresses = lambda: ["192.168.1.10"]
ui_tk._log_path = lambda *a, **k: r"C:\Users\you\AppData\Local\NishroLink\link.log"
ui_theme.PREF_PATH = os.path.join(tempfile.mkdtemp(), "ui.json")
ui_theme.save_pref(MODE)

OUT = os.path.join(REPO, "docs", "screenshots") if PUBLISH else \
    tempfile.mkdtemp(prefix="nishro-link-shots-" + MODE + "-")
os.makedirs(OUT, exist_ok=True)
tmp = tempfile.mkdtemp()


def settle(w, n=8):
    for _ in range(n):
        w.update()
        time.sleep(0.05)


def frame(win, name):
    """The window as it draws itself - never a grab of the screen, which would
    take in whatever else is showing."""
    settle(win)
    import shot_manual_grab
    img, _ = shot_manual_grab.grab(win)
    img.save(os.path.join(OUT, f"{name}.png"), optimize=True)


desk = Desk("laptop")
desk.add("laptop", 3286, 1080, parts=((0, 0, 1920, 1080), (1920, 148, 1366, 768)))
desk.add("desktop-1", 1920, 1080, owner="desktop-1", x=3286, y=0)
desk.add("desktop-2", 1920, 1080, owner="desktop-2", x=0, y=-1080)
# Wrap-around: right off desktop-1 comes back in on the laptop.
desk.add_copy("laptop", x=3286 + 1920, y=0)
cfg = config.merge(config.DEFAULTS, {"node": "laptop", "hub": True,
                                     "pin": "tiger-lemon-coral-radio", "port": 8770,
                                     "placement": desk.boxes()})
cfg["devices"] = [
    {"name": "desktop-1", "id": "a1", "addr": "192.168.1.20", "last_seen": time.time()},
    {"name": "desktop-2", "id": "a2", "addr": "192.168.1.40",
     "last_seen": time.time() - 3 * 3600}]
core = NodeCore("laptop", desk, cfg["policy"], is_hub=True, side="right")
core.peer_online("desktop-1")
node = Node(core, FakeCapture(), FakeInjector(), port=8770)
node.device_id = "3f9a1c07d2e84b56"     # an example, not this computer's
node.capture.start(node)
lk = _Link("desktop-1", object(), ("192.168.1.20", 50000))
lk.rtt_ms = 1.8
node.links["desktop-1"] = lk
log = RunLog(os.path.join(tmp, "link.log"), echo=False)
for line in ["node 'laptop' 3286x1080  |  waiting for a device, on the right",
             "hub 'laptop' listening on 0.0.0.0:8770",
             "peer connected: desktop-1 (password verified both ways)",
             "peer connected: desktop-2 (password verified both ways) - 2 connected",
             "baton=us epoch=3 cursor=desktop-1 suppress(mouse=1,kbd=1)",
             "desktop-2 disconnected - 1 still connected",
             "rejected tablet at ('192.168.1.30', 51234): wrong password",
             "displays changed: 2 displays, desktop 3286x1080"]:
    log(line)
api = control_api.ControlAPI(node, cfg, log, cfg_path=os.path.join(tmp, "c.json"),
                             port=0)
app = ui_tk.App(api)
app._nearby_search = lambda: {"devices": [
    {"name": "desktop-3", "id": "k1", "waiting": True, "alone": True,
     "group": "desktop-3"}]}
app.root.geometry("1040x700+20+0")
app.root.attributes("-topmost", True)
settle(app.root)
for name in ("overview", "devices", "arrange", "activity", "settings", "help"):
    app.show_page(name)
    app._poll_now()
    if name == "arrange":
        settle(app.root)
        app.arranger.select("desktop-1")     # its handles, and what applies
        app._poll_now()
    frame(app.root, name)
    print("saved", name)

found = {"devices": [
    {"name": "desktop-3", "id": "k1", "waiting": True, "alone": True,
     "group": "desktop-3"},
    {"name": "desktop-1", "id": "a1", "waiting": False, "group": "laptop"}]}
d = ui_pair.AddDevice(app.root, api, app.C, search=lambda: found)
d.top.attributes("-topmost", True)
settle(app.root, 25)
frame(d.top, "add")
d.close()
print("saved dialogs to", OUT)
app._alive = False
app.root.destroy()
node.stop()
