"""Nishro Link - one mouse and keyboard across two machines.

Either machine's mouse drives both. "hub" only means the one that listens on
the socket and settles who is driving; it does NOT mean the one with the mouse.

Set up each machine once:

    nishro-link --setup

It asks three things. On the machine that listens: that it listens, which side
the other one is on, and a shared PIN. On the other: that it does not listen,
the listener's IP, and the same PIN.

Nothing else is asked, because nothing else has to be. Machine names and screen
sizes travel in the handshake, and the listener's layout is the shared truth the
other side adopts - so neither has to be told the other's name, its resolution,
or which side it sits on.

Then, on both:

    nishro-link

Press both Ctrl keys together on either machine to drop everything and get your
local input back.
"""
from __future__ import annotations

import argparse
import gc
import json
import socket
import sys
import threading

from . import config, control_api, desk, desktop
from .inject import make_injector
from .node import Node, NodeCore
from .runtime import RunLog, SingleInstance, log_path


def build_desk(cfg, here, peer_label="peer"):
    """The arrangement to start with, from the config and this machine's
    monitors (`here`, a desktop.Desktop).

    A saved arrangement is kept, with this machine re-measured - monitors change
    between runs, and Desk.resize() moves the neighbours so a machine that grew
    does not slide underneath them. With nothing saved, the other machine goes
    on `side`.

    Its own function, not inline in main(), since inline it could not be
    tested: a local variable there once shadowed the desk module, and the
    program failed on startup with nothing in the suite to notice.
    """
    screen = parse_size(cfg.get("screen"), here.size)
    # Monitor outlines only describe the desktop that was measured. A size
    # forced in the config is a different screen, so it gets none.
    parts = here.parts if screen == here.size else ()
    placement = cfg.get("placement")
    if placement:
        lay = desk.place(cfg["node"], placement)
        if cfg["node"] in lay.names():
            lay.resize(cfg["node"], screen[0], screen[1], parts)
        else:
            desk.beside(lay, cfg["node"], screen[0], screen[1], cfg["node"],
                        "left", parts)
        return lay
    peer_screen = parse_size(cfg.get("peer_screen"), screen)
    return desk.simple(cfg["node"], screen, peer_label, peer_screen,
                       cfg.get("side") or "left", parts=parts)


def peer_name(cfg):
    """The name to look for, or None if none is known.

    Earlier builds saved the placeholder "peer" as the name when pairing by
    address. Read as a name, it sent the dialler searching for a device called
    "peer" - forever, because it refused the real one at the remembered address
    as the wrong machine. Found on the Ubuntu box on upgrade. It means no name.
    """
    name = (cfg.get("peer") or "").strip()
    if not name or name == "peer" or name == cfg.get("node"):
        return None
    return name


def load_window(log):
    """The Tk window module, or None and a log line saying why not.

    tkinter is a separate package on Debian and Ubuntu (python3-tk), and this
    import used to be unguarded: on the Ubuntu box a missing package ended the
    whole program with a traceback, taking the link down over a missing WINDOW.
    The link runs fine headless, so no window is never a reason to stop - only a
    reason to say how to get one.
    """
    try:
        from . import ui_tk
    except ImportError as e:
        fix = ("sudo apt install python3-tk" if sys.platform.startswith("linux")
               else "reinstall Python with its Tcl/Tk option ticked")
        log(f"no window: {e.name or 'tkinter'} is not installed ({fix}). "
            f"Running headless - use the control UI link above.")
        return None
    if not ui_tk.available():
        log("no display for the window - running headless; "
            "use the control UI link above")
        return None
    return ui_tk


def detect_screen():
    """This machine's screen size: its whole desktop, every monitor included."""
    return desktop.detect().size


def parse_size(text, fallback):
    if not text or "x" not in str(text).lower():
        return fallback
    w, h = str(text).lower().split("x")[:2]
    return int(w), int(h)


def make_capture(screen, origin=(0, 0)):
    if sys.platform == "win32":
        from .capture_win import WinCapture
        return WinCapture(origin=origin)
    from .capture_linux import LinuxCapture
    return LinuxCapture(screen=screen)


def build_parser():
    ap = argparse.ArgumentParser(
        prog="nishro-link", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    # Everything defaults to None so an unspecified flag means "keep whatever is
    # saved" rather than silently overwriting it with a default.
    ap.add_argument("--node", help="this machine's name (default: hostname)")
    ap.add_argument("--peer", help="the other machine's name")
    ap.add_argument("--peer-addr", help="the hub's IP (required unless --hub)")
    ap.add_argument("--hub", action="store_true", default=None,
                    help="listen and arbitrate. Administrative only - it does NOT "
                         "mean this machine has the mouse.")
    ap.add_argument("--no-hub", dest="hub", action="store_false",
                    help="stop being the hub")
    ap.add_argument("--side", choices=["left", "right", "top", "bottom"],
                    help="where the PEER sits relative to this machine")
    ap.add_argument("--screen", help="this machine's size, e.g. 1366x768 (auto)")
    ap.add_argument("--peer-screen", help="the other machine's size, e.g. 1920x1080")
    ap.add_argument("--port", type=int)
    ap.add_argument("--pin")
    ap.add_argument("--claim", choices=["motion", "click", "hotkey"],
                    help="what hands control to this machine (default: motion)")
    ap.add_argument("--no-drive", dest="may_drive", action="store_false", default=None,
                    help="may be controlled, but never takes control")
    ap.add_argument("--drive", dest="may_drive", action="store_true",
                    help="may take control (the default)")
    ap.add_argument("--no-driven", dest="may_be_driven", action="store_false",
                    default=None, help="may take control, but never accepts any")
    ap.add_argument("--driven", dest="may_be_driven", action="store_true",
                    help="may be controlled (the default)")
    ap.add_argument("--config", help="config file (default: per-user location)")
    ap.add_argument("--save", action="store_true",
                    help="store these settings, then run with them")
    ap.add_argument("--show", action="store_true",
                    help="print the settings that would be used, and stop")
    ap.add_argument("--setup", action="store_true",
                    help="ask the questions, save the answers, and stop")
    ap.add_argument("--ui-port", type=int, default=8771,
                    help="port for the local control UI (0 turns it off)")
    ap.add_argument("--no-window", action="store_true",
                    help="run without the application window (headless/service)")
    return ap


def setup(cfg: dict, path_hint) -> dict:
    """Ask for what cannot be guessed. Everything else is detected or defaulted.

    Deliberately short. The only facts a machine cannot work out for itself are
    what the other one is called, where it is, and which side it sits on.
    """
    def ask(prompt, current):
        shown = f" [{current}]" if current not in (None, "") else ""
        got = input(f"{prompt}{shown}: ").strip()
        return got or current

    w, h = detect_screen()
    print()
    print("Nishro Link setup - press Enter to keep what is in brackets.")
    print(f"This machine is {socket.gethostname()}, screen {w}x{h}.")
    print()
    print("One machine listens and the other dials it. They must not both listen.")
    hub = ask("Does THIS machine listen? (y/n)", "y" if cfg["hub"] else "n")
    cfg["hub"] = str(hub).lower().startswith("y")

    # Only ever ask for what the two machines cannot tell each other. Names and
    # screen sizes travel in the handshake, and the listener's layout is the
    # shared truth the other side adopts - so the only facts left are the
    # address, which side, and the shared secret.
    if cfg["hub"]:
        cfg["side"] = ask("Which side is the OTHER machine on? "
                          "(left/right/top/bottom)", cfg["side"])
    else:
        cfg["peer_addr"] = ask("The listening machine's IP", cfg["peer_addr"])
    cfg["pin"] = ask("Shared PIN (must match on both machines)", cfg["pin"] or "")

    where = config.save(cfg, path_hint)
    print()
    print(f"Saved to {where}")
    print("From now on, just run:  nishro-link")
    print()
    if cfg["hub"]:
        addrs = _my_addresses()
        print("On the OTHER machine, run setup and answer:")
        print("  listen?  n")
        print(f"  IP       {addrs[0] if addrs else 'this machine%s IP' % chr(39)}"
              + (f"   (also: {', '.join(addrs[1:])})" if len(addrs) > 1 else ""))
        print(f"  PIN      {cfg['pin'] or '(the same one)'}")
        print()
        print("It does not need a side, a name, or a screen size - it learns all")
        print("three from this machine when it connects.")
    else:
        print(f"Make sure {cfg['peer_addr']} is running as the listener, with the")
        print("same PIN. It decides which side this screen is on.")
    print()
    return cfg


def _my_addresses():
    """Our LAN addresses, best-effort, so setup can print the one to type."""
    found = []
    try:
        for fam, _, _, _, sa in socket.getaddrinfo(socket.gethostname(), None):
            ip = sa[0]
            if fam == socket.AF_INET and not ip.startswith("127.") and ip not in found:
                found.append(ip)
    except OSError:
        pass
    return found


def settings(argv=None):
    """Saved config with this run's flags on top."""
    args = build_parser().parse_args(argv)
    over = {
        "node": args.node, "peer": args.peer, "peer_addr": args.peer_addr,
        "hub": args.hub, "side": args.side, "screen": args.screen,
        "peer_screen": args.peer_screen, "port": args.port, "pin": args.pin,
        "policy": {"may_drive": args.may_drive,
                   "may_be_driven": args.may_be_driven,
                   "claim": args.claim},
    }
    return args, config.merge(config.load(args.config), over)


def main() -> int:
    args, cfg = settings()

    log = RunLog()

    if not cfg["node"]:
        cfg["node"] = socket.gethostname()
    if not cfg.get("device_id"):
        # Made once and kept: it is how a paired device is recognised after a
        # rename or a new address. Saved now, before anything can pair with it.
        import secrets
        cfg["device_id"] = secrets.token_hex(8)
        try:
            # The ID alone: cfg also holds this run's command-line flags, which
            # are saved only when someone asks for it with --save.
            stored = config.load(args.config)
            stored["device_id"] = cfg["device_id"]
            config.save(stored, args.config)
        except OSError:
            pass

    if args.setup:
        setup(cfg, args.config)
        return 0

    # Save before validating, so a setup can be built up over several runs, and
    # before showing, so `--save --show` does both rather than silently only one.
    if args.save:
        log(f"settings saved to {config.save(cfg, args.config)}")
    if args.show:
        shown = dict(cfg, pin=("set" if cfg["pin"] else "none"))
        print(json.dumps(shown, indent=2, sort_keys=True))
        print(f"\n# from {args.config or config.path()}")
        return 0

    # The peer's name and screen size arrive in the handshake, so these are
    # only placeholders until the two machines have actually spoken.
    # A placeholder for the other screen until the machines have spoken. Kept
    # out of cfg: that dict is saved from the window, and a saved "peer" is now
    # the NAME this device goes looking for.
    cfg["peer"] = peer_name(cfg)
    peer_label = cfg["peer"] or "peer"
    if not cfg["hub"] and not cfg["peer_addr"] and not cfg["peer"]:
        # Not an error any more: a fresh install has nothing paired and must
        # still come up, or there is no window to pair FROM. The link simply
        # waits until something is paired.
        log("no device paired yet - open the window and use 'Add a device'")

    here = desktop.detect()
    screen = parse_size(cfg["screen"], here.size)
    policy = cfg["policy"]
    lay = build_desk(cfg, here, peer_label)
    core = NodeCore(cfg["node"], lay, policy, is_hub=bool(cfg["hub"]),
                    side=cfg["side"])

    try:
        capture = make_capture(screen, origin=(here.x, here.y))
        injector = make_injector(screen=screen, origin=(here.x, here.y))
    except Exception as e:
        log(f"cannot start: {e}")
        return 1

    # One instance per port. Two of these running is not a harmless mistake:
    # they fight over the same devices and interleave into the same log, which
    # is how an incident ends up described by the wrong process's output.
    only = SingleInstance(cfg["port"] - 1)
    if not only.acquire():
        # Already running. Treat a second launch as "show me", not as an error:
        # that is what clicking the shortcut again means once the window has been
        # put in the background.
        if SingleInstance.signal(cfg["port"] - 1):
            log("Nishro Link is already running - asked it to show its window")
            return 0
        log(f"another Nishro Link is already running on port {cfg['port']}. "
            f"Stop it first, or use a different --port.")
        return 2

    n = Node(core, capture, injector, port=cfg["port"], pin=cfg["pin"],
             peer_addr=cfg["peer_addr"], on_log=log,
             device_id=cfg["device_id"],
             peer_name=None if cfg["hub"] else cfg["peer"],
             peer_id=None if cfg["hub"] else cfg.get("peer_id"))
    n.detect_desktop = desktop.detect          # notice monitors plugged in or out
    n.desktop_now = here

    if cfg["hub"]:
        where = f"waiting for a device, on the {cfg['side']}"
    elif cfg["peer_addr"]:
        where = f"connecting to {cfg['peer_addr']}"
    else:
        where = "nothing paired"
    log(f"node '{cfg['node']}' {screen[0]}x{screen[1]}  |  {where}")
    if cfg["hub"] or cfg["peer_addr"]:
        log("the other machine's name and screen size are learned "
            "when it connects")
    log(f"drive: {'yes' if policy['may_drive'] else 'no'}   "
        f"be driven: {'yes' if policy['may_be_driven'] else 'no'}   "
        f"claim: {policy['claim']}")
    if cfg["hub"] or cfg["peer_addr"]:
        log("push the pointer off the shared edge to cross over; "
            "move this machine's own mouse to take control back")
    log("FAILSAFE: press both Ctrl keys together to release everything")
    log(f"log: {log_path()}")

    ui = None
    if args.ui_port:
        ui = control_api.ControlAPI(n, cfg, log, cfg_path=args.config, port=args.ui_port)
        if ui.start():
            log(f"control UI: {ui.url}")
            log("  (that link carries a one-time token - it changes every run)")
        if sys.platform == "win32":
            threading.Thread(target=ui.check_firewall, daemon=True).start()

    # Input is captured on a thread where Windows blocks EVERY mouse on the
    # machine until the callback returns, so a garbage collection pause landing
    # there is a stutter the user can feel. Measured over 30s of sustained input:
    # worst case 2701us with the default collector, 583us with this. freeze()
    # moves everything allocated during startup into a permanent generation so
    # later passes have far less to walk; the raised gen1/gen2 thresholds make
    # the expensive full collections rare without making the cheap ones rarer.
    # Done here rather than in node.py: this is the application, and a library
    # has no business reconfiguring its host's collector.
    gc.collect()
    gc.freeze()
    gc.set_threshold(700, 50, 200)

    # The window, if there is a display for one. Tk must own the main thread, so
    # the link goes onto its own - the reverse of the headless case.
    window = None
    if not args.no_window and ui:
        ui_tk = load_window(log)
        if ui_tk:
            window = ui_tk.App(ui, on_quit=n.stop)
            only.watch(window.show)      # a second launch raises this window

    try:
        if window:
            threading.Thread(target=n.run, daemon=True).start()
            window.run()
        else:
            n.run()
    except KeyboardInterrupt:
        log("stopping")
    finally:
        n.stop()
        if ui:
            ui.stop()
        only.release()
        log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
