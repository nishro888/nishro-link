"""A localhost web UI for configuring, starting and stopping the link.

Served by the link process itself, on 127.0.0.1 only. Chosen over a native tray
because it is the same code on Windows and on Wayland, needs no GUI toolkit and
no extra dependency, and survives being packed into a one-file exe.

WHY IT IS TOKEN-PROTECTED. This API can change the shared PIN, turn input
capture on, and hand this machine's keyboard to another computer. Binding to
loopback keeps the network out, but it does NOT keep out a web page you happen to
have open: any site can POST to http://127.0.0.1:8771 from your browser. So every
/api/ call needs a token minted at startup and baked into the page we serve.
A random token is not in a page nobody else can read.

"Stop" means stop LINKING, not stop the process. Killing the process would take
the UI down with it, and then there would be nothing left to start it from.
"""
from __future__ import annotations

import collections
import json
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import access, autostart, config, pairing, runtime

MAX_BODY = 64 * 1024     # every real command is a few hundred bytes


class ControlAPI:
    def __init__(self, node, cfg: dict, log, cfg_path=None, port: int = 8771):
        self.node = node                 # the Node, for status and enable/disable
        self.cfg = cfg
        self.log = log
        self.cfg_path = cfg_path
        self.port = int(port)
        self.token = secrets.token_urlsafe(16)
        self._srv = None
        # The hub's arrangement can change from either window now - the peer's
        # arrives over the wire - so saving it hangs off the node, not off the
        # one HTTP handler that used to be the only way in.
        node.on_placement = self._persist_placement
        node.on_paired = self._persist_peer
        node.on_devices = self._device_event
        node.on_event = self._on_event
        node.on_invited = self._on_invited
        node.on_removed = self._on_removed
        node.removed_ids = set(cfg.get("removed") or [])
        node.core.devices = self._roster_view()
        # Things that happened, for the window to show as notices: who joined,
        # who was refused and why. Numbered, so each is shown once.
        self.events = collections.deque(maxlen=40)
        self._event_seq = 0
        self._inviting = False
        # Set by check_firewall(): [] clear, a list of rules if blocked, None if
        # it could not be checked (never shown as a problem).
        self.firewall_blocked = None
        # Linux device access, from access.check(); None or ok means nothing
        # to do. Set by the program at startup.
        self.setup = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    def start(self) -> bool:
        api = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            # Keep-alive is worse than useless here. The UI polls about once a
            # second over loopback, where a fresh connection costs nothing - but
            # a kept-alive one parks a thread in readline() with no timeout,
            # waiting for a request that never comes. Across a long session that
            # is an unbounded pile of stuck threads.
            timeout = 10

            def log_message(self, *a):
                pass                     # one line per poll would drown the real log

            def handle_one_request(self):
                super().handle_one_request()
                self.close_connection = True

            def _send(self, code, body: bytes, ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                # This page must never be framed or cached by anything.
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def _authorised(self, q) -> bool:
                given = (q.get("t", [""])[0]
                         or self.headers.get("X-Nishro-Token", ""))
                return secrets.compare_digest(given, api.token)

            def do_GET(self):
                u = urlsplit(self.path)
                q = parse_qs(u.query)
                if u.path == "/":
                    page = PAGE.replace("__TOKEN__", api.token)
                    return self._send(200, page.encode("utf-8"),
                                      "text/html; charset=utf-8")
                if not self._authorised(q):
                    return self._send(403, b'{"error":"bad token"}')
                if u.path == "/api/status":
                    return self._send(200, json.dumps(api.status()).encode())
                self._send(404, b'{"error":"no such thing"}')

            def do_POST(self):
                u = urlsplit(self.path)
                q = parse_qs(u.query)
                # Read the body BEFORE deciding anything. Answering and closing
                # with it still unread makes Windows reset the connection rather
                # than close it, and the reset can destroy the answer in flight:
                # a refused request then reached the client as WinError 10053
                # instead of a 403. Found as a test failing ~1 run in 25.
                try:
                    n = max(0, int(self.headers.get("Content-Length") or 0))
                except ValueError:
                    return self._send(400, b'{"error":"bad content-length"}')
                if n > MAX_BODY:
                    return self._send(413, b'{"error":"request too large"}')
                raw = self.rfile.read(n) if n else b""
                if not self._authorised(q):
                    return self._send(403, b'{"error":"bad token"}')
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    return self._send(400, b'{"error":"bad json"}')
                try:
                    out = api.command(u.path, body)
                except Exception as e:
                    return self._send(500, json.dumps({"error": repr(e)}).encode())
                if out is None:
                    return self._send(404, b'{"error":"no such command"}')
                self._send(200, json.dumps(out).encode())

        class Server(ThreadingHTTPServer):
            # http.server turns SO_REUSEADDR on for everyone. On Windows that
            # lets a second copy bind 8771 on top of the first, and the window
            # then polled a DIFFERENT node than the one whose URL it showed.
            # Turning it off outright fixed Windows and broke Linux, where the
            # same flag is what allows a restart over TIME_WAIT. runtime.
            # exclusive() sets whichever one this platform needs.
            allow_reuse_address = False

            def server_bind(self):
                runtime.exclusive(self.socket)
                super().server_bind()

        wanted, last = self.port, None
        for port in range(wanted, wanted + 10):
            try:
                self._srv = Server(("127.0.0.1", port), Handler)
            except OSError as e:
                last = e
                continue
            self.port = port
            if port != wanted:
                self.log(f"control UI: port {wanted} is taken, using {port} "
                         f"(another Nishro Link is probably running)")
            break
        else:
            self.log(f"control UI not available on port {wanted}: {last}")
            return False
        self._srv.daemon_threads = True
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        return True

    def stop(self) -> None:
        if not self._srv:
            return
        try:
            self._srv.shutdown()        # stops serve_forever
        except Exception:
            pass
        try:
            # ...but shutdown() does NOT close the listening socket, so without
            # this the port stays held until the process exits. That leaks a port
            # per restart and makes tests that bind fresh ports flaky.
            self._srv.server_close()
        except Exception:
            pass
        self._srv = None

    # ------------------------------------------------------------ the data
    def role(self) -> str:
        """alone: a group of one. hub: others connect to it. member: it
        connects to a hub."""
        n = self.node
        if not n.core.is_hub:
            return "member"
        return "hub" if n.members() else "alone"

    def group(self):
        n = self.node
        return n.core.node if n.core.is_hub else (n.trusted_peer or n.peer_name)

    def status(self) -> dict:
        n, core = self.node, self.node.core
        lay = core.layout
        return {
            "role": self.role(),
            "group": self.group(),
            "members": n.members(),
            "dial": dict(n.dial),
            "adding": dict(n.adding),
            "blocked": n.blocked,
            "events": list(self.events),
            "node": core.node,
            "device_id": n.device_id,
            "peer": n.peer_name,
            "peer_id": n.peer_id,
            "hub": bool(core.is_hub),
            "side": self.cfg.get("side"),
            "peer_addr": self.cfg.get("peer_addr"),
            "port": self.cfg.get("port"),
            "pin_set": bool(self.cfg.get("pin")),
            "paired": self.node.paired(),
            "addresses": my_addresses(),
            "trusted_peer": n.trusted_peer,
            "encrypted": False,      # authenticated, not encrypted - see protocol.py
            "firewall_blocked": self.firewall_blocked or [],
            "setup": self.setup if self.setup and not self.setup.get("ok") else None,
            "autostart": self._autostart_state(),
            "policy": dict(core.policy),
            "enabled": bool(n.enabled),
            "connected": n.ch is not None,
            "holder": core.baton.holder,
            "holds": core.holds(),
            "epoch": core.epoch,
            "cursor": {"screen": core.cursor.screen,
                       "x": core.cursor.x, "y": core.cursor.y,
                       "remote": core.cursor_is_remote()},
            "suppress": {"mouse": core.suppress_mouse(),
                         "keyboard": core.suppress_keyboard()},
            "rtt_ms": round(n.rtt_ms, 2) if n.rtt_ms else None,
            "dropped": n.link_stats()[0],
            "queue": n.link_stats()[1],
            "placement": core.placement,
            "devices": self.devices(),
            "peers": n.peers(),
            "problems": lay.problems(),
            "screens": [{"name": s, "w": lay.get(s).w, "h": lay.get(s).h,
                         "x": lay.get(s).x, "y": lay.get(s).y,
                         "parts": [list(p) for p in lay.get(s).parts],
                         "owner": lay.get(s).owner, "mine": lay.is_local(s)}
                        for s in lay.names()],
            "log": list(getattr(self.log, "tail", []))[-60:],
        }

    def command(self, path: str, body: dict):
        if path == "/api/enable":
            self.node.set_enabled(True)
            return {"ok": True, "enabled": True}
        if path == "/api/disable":
            self.node.set_enabled(False)
            return {"ok": True, "enabled": False}
        if path == "/api/release":
            # The failsafe, as a button. Someone whose mouse has just stopped
            # working should not have to remember a key combination.
            self.node._act(self.node.core.local_failsafe)
            return {"ok": True}
        if path == "/api/pair":
            return self._pair(body)
        if path == "/api/join":
            return self._join(body)
        if path == "/api/invite":
            return self._invite(body)
        if path == "/api/leave":
            return self._leave()
        if path == "/api/remove":
            return self._remove(str(body.get("name") or ""))
        if path == "/api/forget":
            return self._forget()
        if path == "/api/device/forget":
            return self._remove(str(body.get("name") or ""))
        if path == "/api/setup":
            return self._setup()
        if path == "/api/autostart":
            return self._autostart(bool(body.get("on")))
        if path == "/api/firewall":
            return self._firewall()
        if path == "/api/discover":
            return self._discover()
        if path == "/api/password":
            return self._password(bool(body.get("new")))
        if path == "/api/placement":
            return self._place(body.get("boxes") or [])
        if path == "/api/config":
            return self._configure(body)
        if path == "/api/quit":
            self.log("quit requested from the control UI")
            self.node.stop()
            return {"ok": True}
        return None

    def _pair(self, body: dict) -> dict:
        """Pair with another device, and connect straight away.

        Two modes, which is the whole of the decision a person has to make:

          wait  - this device shows its own details and waits to be dialled
          dial  - this device is given the other one's details and calls it

        Applied live. A pairing flow that ended in "now restart the program"
        would not be a pairing flow.
        """
        mode = str(body.get("mode") or "").lower()
        if mode not in ("wait", "dial"):
            return {"error": "mode must be 'wait' or 'dial'"}
        if mode == "dial" and self.node.members():
            # Joining another group would strand this one's devices.
            return {"error": (
                f"this device already has devices in its group "
                f"({', '.join(self.node.members())}). Add the other device from "
                f"here instead, or remove these first.")}
        if mode == "wait" and not body.get("pin"):
            # The waiting side does not choose: it uses the password it is
            # already showing, or makes one. See pairing.py for why.
            pin = self.cfg.get("pin") or pairing.new_password()
            if pairing.problem(pin):
                pin = pairing.new_password()      # an old short one: replace it
        else:
            pin = pairing.normalise(body.get("pin"))
            bad = pairing.problem(pin)
            if bad:
                return {"error": bad}
        port = self.cfg.get("port") or 8770
        if body.get("port"):
            try:
                port = int(body["port"])
            except (TypeError, ValueError):
                return {"error": f"port must be a number, not {body['port']!r}"}
            if not 1 <= port <= 65535:
                return {"error": f"{port} is not a usable port number"}

        # A NAME, normally. An address is accepted too, for a network that
        # drops the search - but it is the fallback, not the question.
        name = str(body.get("peer") or "").strip()
        addr = str(body.get("peer_addr") or "").strip()
        if mode == "dial" and not (name or addr):
            return {"error": "enter the other device's name"}
        if mode == "dial" and name and name.casefold() == self.node.core.node.casefold():
            return {"error": f"'{name}' is this device - enter the other one's name"}

        hub = (mode == "wait")
        if hub:
            self.cfg.update(hub=True, pin=pin, port=port, peer_addr=None,
                            peer_id=None)
        else:
            # A new pairing starts clean: the ID and address of whatever was
            # paired before belong to a different device.
            self.cfg.update(hub=False, pin=pin, port=port, peer=name or None,
                            peer_id=None, peer_addr=addr or None)
        where = config.save(self.cfg, self.cfg_path)

        if hub:
            self.node.reconfigure(hub=True, peer_addr=None, peer_name=None,
                                  peer_id=None, port=port, pin=pin)
        else:
            self.node.reconfigure(hub=False, peer_addr=addr or None,
                                  peer_name=name or None, peer_id=None,
                                  port=port, pin=pin, joining=True)
            with self.node._lock:
                self.node.core.alone()     # the new group's desk arrives with it
        self.node.set_enabled(True)
        self.log(f"paired: this device will "
                 + ("wait for a connection" if hub
                    else f"connect to {name or addr}") + f" on port {port}")
        out = {"ok": True, "saved_to": str(where), "mode": mode,
               "waiting": hub, "port": port, "name": self.node.core.node}
        if hub:
            out["pin"] = pin          # shown, to be read out to the other device
        return out

    def _password(self, new: bool) -> dict:
        """The password this device shows, making one if there is none. `new`
        replaces it: the hub hands the new one to every device connected now,
        and one that is switched off will be asked for it when it comes back."""
        if new and not self.node.core.is_hub:
            return {"error": f"only {self.group() or 'the hub'} can change the "
                             f"group's password"}
        pin = self.cfg.get("pin")
        if new or not pin or pairing.problem(pin):
            pin = pairing.new_password()
            self.cfg["pin"] = pin
            config.save(self.cfg, self.cfg_path)
            told = self.node.rekey(pin)
            if new:
                self.log(f"new password made - given to {told} connected "
                         f"device{'s' if told != 1 else ''}")
        return {"pin": pairing.display(pin), "name": self.node.core.node}

    # ---------------------------------------------------- adding and leaving
    def _invite(self, body: dict) -> dict:
        """Add a device that is on its own, with the password it shows. Runs in
        the background; the window follows status()["invite"]."""
        name = str(body.get("name") or "").strip()
        pin = pairing.normalise(body.get("pin"))
        if not name:
            return {"error": "which device?"}
        if name.casefold() == self.node.core.node.casefold():
            return {"error": f"'{name}' is this device"}
        bad = pairing.problem(pin)
        if bad:
            return {"error": bad}
        if self._inviting:
            return {"error": "already adding a device - one at a time"}
        self._inviting = True
        self.node.set_enabled(True)          # it will be connecting to us

        def run():
            try:
                self.node.invite(name, pin, addr=body.get("addr") or None,
                                 port=body.get("port") or None)
            except Exception as e:
                self.node.adding = {"mode": "invite", "phase": "failed",
                                    "reason": "error", "detail": repr(e),
                                    "target": name}
            finally:
                self._inviting = False
        threading.Thread(target=run, daemon=True).start()
        return {"ok": True, "started": True}

    def _join(self, body: dict) -> dict:
        """Join another device's group, with the password it shows. The password
        is checked with its hub FIRST: a wrong one changes nothing here. Runs in
        the background; the window follows status()["adding"]."""
        name = str(body.get("name") or "").strip()
        pin = pairing.normalise(body.get("pin"))
        addr = str(body.get("addr") or "").strip() or None
        if not name and not addr:
            return {"error": "which device?"}
        if name and name.casefold() == self.node.core.node.casefold():
            return {"error": f"'{name}' is this device"}
        bad = pairing.problem(pin)
        if bad:
            return {"error": bad}
        if self.node.members():
            return {"error": (
                f"this device already has devices in its group "
                f"({', '.join(self.node.members())}). Add the other device from "
                f"here instead, or remove these first.")}
        if self._inviting:
            return {"error": "already adding a device - one at a time"}
        self._inviting = True

        def run():
            n = self.node
            try:
                r = n.probe(name or addr, pin, addr=addr,
                            port=body.get("port") or None)
                if not r["ok"]:
                    return
                was = self.group() if not n.core.is_hub else None
                self.cfg.update(hub=False, pin=pin, peer=r["hub"],
                                peer_id=r["hub_id"], peer_addr=r["addr"],
                                port=r["port"], devices=[], placement=None,
                                removed=[])
                config.save(self.cfg, self.cfg_path)
                if was and was != r["hub"]:
                    n.leave()                 # tell the old group
                n.reconfigure(hub=False, peer_addr=r["addr"], peer_name=r["hub"],
                              peer_id=r["hub_id"], port=r["port"], pin=pin,
                              joining=True)
                with n._lock:
                    n.core.alone()
                n.set_enabled(True)
                self.log(f"joining {r['hub']}'s group")
                n.adding = dict(n.adding, phase="joining")
                end = time.monotonic() + 15
                while time.monotonic() < end:
                    if n.dial.get("phase") == "connected":
                        n.adding = dict(n.adding, phase="connected")
                        return
                    if n.dial.get("phase") == "failed":
                        break
                    time.sleep(0.1)
                n.adding = dict(n.adding, phase="failed",
                                reason=n.dial.get("reason") or "timeout",
                                detail=n.dial.get("detail"))
            except Exception as e:
                n.adding = {"mode": "join", "phase": "failed", "reason": "error",
                            "detail": repr(e), "target": name}
            finally:
                self._inviting = False
        threading.Thread(target=run, daemon=True).start()
        return {"ok": True, "started": True}

    def _leave(self) -> dict:
        if self.node.core.is_hub:
            return {"error": "this device is the hub - remove the other devices "
                             "instead"}
        group = self.group()
        self.node.leave()
        self._go_alone(f"left {group}'s group")
        return {"ok": True}

    def _remove(self, name: str) -> dict:
        if not name:
            return {"error": "which device?"}
        dev = next((d for d in self.cfg.get("devices") or []
                    if d.get("name") == name), {})
        err = self.node.remove(name)
        if err:
            return {"error": err}
        if dev.get("id"):
            removed = set(self.cfg.get("removed") or [])
            removed.add(dev["id"])
            self.cfg["removed"] = sorted(removed)
            self.node.removed_ids.add(dev["id"])
        self.cfg["devices"] = [d for d in self.cfg.get("devices") or []
                               if d.get("name") != name]
        config.save(self.cfg, self.cfg_path)
        self.node.core.devices = self._roster_view()
        self.log(f"{name} removed from the group")
        return {"ok": True}

    def _go_alone(self, why: str) -> None:
        """A group of one again, with a password of its own: the old group's
        password stays with the old group."""
        pin = pairing.new_password()
        self.cfg.update(hub=True, pin=pin, peer=None, peer_id=None,
                        peer_addr=None, devices=[], placement=None, removed=[])
        config.save(self.cfg, self.cfg_path)
        self.node.go_alone(pin)
        self.node.core.devices = self._roster_view()
        self.node.set_enabled(True)
        self.log(f"{why} - this device is on its own again, with a new password")

    def _on_removed(self, by: str) -> None:
        self._go_alone(f"{by} removed this device from its group")

    def _on_invited(self, hub, hub_id, addr, port, secret, by) -> bool:
        """Another device added this one to its group (Node._invited)."""
        self.cfg.update(hub=False, pin=pairing.normalise(secret), port=port,
                        peer=hub, peer_id=hub_id, peer_addr=addr, devices=[],
                        placement=None, removed=[])
        config.save(self.cfg, self.cfg_path)

        def go():
            time.sleep(0.3)                  # let the answer go out first
            self.node.reconfigure(hub=False, peer_addr=addr, peer_name=hub,
                                  peer_id=hub_id, port=port, pin=secret,
                                  joining=True)
            with self.node._lock:
                self.node.core.alone()
            self.node.set_enabled(True)
        threading.Thread(target=go, daemon=True).start()
        return True

    # ------------------------------------------------------------ notices
    def _on_event(self, kind: str, info: dict) -> None:
        name = info.get("name") or "a device"
        text, tone = None, "dim"
        if kind == "joined":
            text = (f"{name} joined the group" if info.get("first")
                    else f"{name} connected")
            tone = "ok"
        elif kind == "connected":
            text = (f"Connected to {name}'s group" if info.get("first")
                    else f"Connected to {name}")
            tone = "ok"
        elif kind == "rejected":
            text = f"{name} tried to connect with the wrong password"
            tone = "warn"
        elif kind == "dial_failed":
            text = (f"{name} did not accept the password"
                    if info.get("reason") == "wrong_password" else
                    f"{name} refused: there is already a device called "
                    f"{self.node.core.node} in its group")
            tone = "bad"
        elif kind == "removed":
            text = f"{name} removed this device from its group"
            tone = "warn"
        elif kind == "left_group":
            text = f"{name} left the group"
        elif kind == "invited":
            text = f"{name} added this device to {info.get('group')}'s group"
            tone = "ok"
        elif kind == "disconnected":
            text = f"Lost the connection to {name} - reconnecting"
            tone = "warn"
        elif kind == "went_offline":
            text = f"{name} disconnected"
        elif kind == "rekeyed":
            self.cfg["pin"] = pairing.normalise(info.get("pin"))
            config.save(self.cfg, self.cfg_path)
            text = f"{name} changed the group's password - this device has it"
            tone = "ok"
        if text:
            self._event_seq += 1
            self.events.append({"id": self._event_seq, "at": time.time(),
                                "kind": kind, "name": name, "text": text,
                                "tone": tone})

    def _discover(self) -> dict:
        """Devices on this network. Takes about a second."""
        found = self.node.find("*")
        return {"devices": [{"name": f.name, "id": f.id, "addr": f.addr,
                             "waiting": f.waiting, "group": f.group,
                             "alone": f.alone,
                             "paired": f.id == self.node.peer_id}
                            for f in found]}

    # ------------------------------------------------------------ devices
    def _device_event(self, event, name, info) -> None:
        """A device joined or left (on the hub), or the hub described the group
        (on a peer). Kept in the config, so the list survives a restart and
        shows machines that are switched off."""
        devs = {d["name"]: dict(d) for d in self.cfg.get("devices") or []
                if d.get("name")}
        now = int(time.time())
        if event == "forgotten":
            devs.pop(name, None)
        elif event == "joined":
            if info.get("id") in (self.cfg.get("removed") or []):
                self.cfg["removed"] = [i for i in self.cfg["removed"]
                                       if i != info.get("id")]
            d = devs.get(name) or {"name": name, "first_seen": now}
            screens = [{k: s[k] for k in ("w", "h", "parts") if k in s}
                       for s in info.get("screens") or []]
            d.update(id=info.get("id") or d.get("id"),
                     addr=info.get("addr") or d.get("addr"),
                     screens=screens or d.get("screens") or [], last_seen=now)
            devs[name] = d
        elif event == "left" and name in devs:
            devs[name]["last_seen"] = now
            self._on_event("went_offline", {"name": name})
        elif event == "roster":
            for d in info.get("devices") or []:
                if d.get("name") and d["name"] != self.node.core.node:
                    kept = devs.get(d["name"], {})
                    devs[d["name"]] = dict(kept, **{k: v for k, v in d.items()
                                                  if k != "online"})
        self.cfg["devices"] = list(devs.values())
        config.save(self.cfg, self.cfg_path)
        self.node.core.devices = self._roster_view()

    def _roster_view(self) -> list:
        """The group as the hub describes it to everyone: itself, then every
        device that has joined. Addresses stay with the hub."""
        core = self.node.core
        if not core.is_hub:
            return []
        out = [{"name": core.node, "id": self.node.device_id, "hub": True}]
        for d in self.cfg.get("devices") or []:
            out.append({k: d.get(k) for k in ("name", "id", "first_seen",
                                              "last_seen") if d.get(k) is not None})
        return out

    def devices(self) -> list:
        """Every machine in the group, for the window: from the arrangement
        (displays, what is here) and the device list (IDs, when last seen)."""
        n, core = self.node, self.node.core
        known = {d.get("name"): d for d in self.cfg.get("devices") or []}
        live = {p["name"]: p for p in n.peers()}
        hub_name = core.node if core.is_hub else (n.trusted_peer or n.peer_name)
        online = set(live) | {core.node}
        if not core.is_hub:
            online |= set(core.online)
        out = []
        for m in core.layout.machines():
            d = known.get(m.name, {})
            out.append({
                "name": m.name,
                "me": m.owner == core.node,
                "hub": m.name == hub_name,
                "online": m.owner in online,
                "displays": [[r.w, r.h] for r in m.displays()],
                "rects": [[r.x, r.y, r.w, r.h] for r in m.displays()],
                "id": d.get("id") or (n.device_id if m.owner == core.node else
                                      n.peer_id if m.name == n.peer_name else None),
                "addr": (live.get(m.name) or {}).get("addr") or d.get("addr"),
                "rtt_ms": (live.get(m.name) or {}).get("rtt_ms"),
                "last_seen": d.get("last_seen"),
            })
        return out

    def _setup(self) -> dict:
        """Set up keyboard and mouse access on Linux, through the desktop's own
        password prompt. After it, a new login is still needed."""
        r = access.fix()
        if r.get("ok"):
            self.setup = access.check()
            self.log("keyboard and mouse access set up - log out and back in "
                     "once to finish")
        return dict(r, setup=self.setup)

    # ------------------------------------------------- start when I log in
    def _launch(self) -> list:
        """The command a login runs: this copy, with this run's config file."""
        return autostart.command(["--config", str(self.cfg_path)]
                                 if self.cfg_path else [])

    def _autostart_state(self) -> dict:
        try:
            return autostart.state(self._launch())
        except Exception:                  # never let the status call fail on it
            return {"available": False, "on": False, "current": False}

    def _autostart(self, on: bool) -> dict:
        try:
            autostart.switch(on, self._launch())
        except Exception as e:
            self.log(f"could not {'set' if on else 'stop'} starting at login: {e}")
            return {"ok": False, "error": str(e),
                    "autostart": self._autostart_state()}
        self.log("starts at login, in the background" if on
                 else "no longer starts at login")
        return {"ok": True, "autostart": self._autostart_state()}

    def check_firewall(self) -> None:
        """Look for firewall rules blocking this program. Slow (PowerShell), so
        run from a background thread at startup."""
        self.firewall_blocked = runtime.firewall_blocks()
        if self.firewall_blocked:
            self.log("Windows Firewall is BLOCKING this program: "
                     + "; ".join(self.firewall_blocked)
                     + ". Other devices cannot find or reach it - most likely a "
                       "Windows Security prompt was dismissed. Press 'Allow "
                       "through the firewall' in the window to undo it.")

    def _firewall(self) -> dict:
        """Undo a block, through Windows' own elevation prompt."""
        ok = runtime.allow_through_firewall()
        self.firewall_blocked = runtime.firewall_blocks()
        if ok and not self.firewall_blocked:
            self.log("firewall: this program is allowed on private networks now")
        return {"ok": bool(ok) and not self.firewall_blocked,
                "declined": not ok, "blocked": self.firewall_blocked or []}

    def _persist_peer(self, name, dev_id, addr) -> None:
        """A dial worked: remember who it reached, by name, ID and address."""
        self.cfg.update(peer=name, peer_id=dev_id, peer_addr=addr)
        config.save(self.cfg, self.cfg_path)

    def _forget(self) -> dict:
        """Unpair: leave the group, or - on a hub - let every device go. Back to
        a group of one, which is where a fresh install starts."""
        if self.node.core.is_hub:
            for name in self.node.members():
                self._remove(name)
            self._go_alone("the group was taken apart")
            return {"ok": True}
        return self._leave()

    def _place(self, boxes) -> dict:
        """Take an arrangement from the editor and put it in force - on both
        machines, now. See NodeCore.arrange."""
        err = self.node.arrange(boxes)
        if err:
            return {"error": err}
        self.log("arrangement changed: "
                 + ", ".join(f"{b['name']}@{b['x']},{b['y']}" for b in boxes))
        connected = self.node.ch is not None
        if self.node.core.is_hub:
            reach = "both" if connected else "here"
        else:
            reach = "sent"
        return {"ok": True, "applied": reach}

    def _persist_placement(self, boxes) -> None:
        self.cfg["placement"] = boxes
        config.save(self.cfg, self.cfg_path)

    def _configure(self, body: dict) -> dict:
        """Write settings through. Anything needing a restart says so rather than
        pretending to have taken effect."""
        live, restart = {}, []
        # Refuse a bad password BEFORE touching anything: checked in turn below,
        # it came after `side`, so a refused request had already moved the peer
        # to the other side of the desk in memory.
        if "pin" in body and pairing.problem(body["pin"]):
            return {"error": pairing.problem(body["pin"])}
        if "side" in body:
            side = str(body["side"])
            if side not in ("left", "right", "top", "bottom"):
                return {"error": f"side must be left/right/top/bottom, not {side!r}"}
            self.cfg["side"] = side
            self.node.core.side = side
            restart.append("side")
        for key in ("may_drive", "may_be_driven"):
            if key in body:
                val = bool(body[key])
                self.cfg["policy"][key] = val
                self.node.core.policy[key] = val      # takes effect at once
                live[key] = val
        if "claim" in body:
            claim = str(body["claim"])
            if claim not in ("motion", "click", "hotkey"):
                return {"error": f"claim must be motion/click/hotkey, not {claim!r}"}
            self.cfg["policy"]["claim"] = claim
            self.node.core.policy["claim"] = claim
            self.node.core.claims.policy = claim
            live["claim"] = claim
        if "pin" in body:
            if not self.node.core.is_hub:
                return {"error": f"only {self.group() or 'the hub'} can change "
                                 f"the group's password"}
            self.cfg["pin"] = pairing.normalise(body["pin"])
            told = self.node.rekey(self.cfg["pin"])      # and the devices here now
            live["pin"] = f"given to {told} connected device{'s' if told != 1 else ''}"
        if "peer_addr" in body:
            self.cfg["peer_addr"] = str(body["peer_addr"]) or None
            restart.append("the other device's address")
        if "node" in body:
            name = str(body["node"]).strip()
            if not name:
                return {"error": "a device name cannot be empty"}
            if name != self.cfg.get("node"):
                self.cfg["node"] = name
                # Not applied live: the name is baked into the layout, the
                # arbiter and every grant already issued. Renaming underneath all
                # that would be a far bigger change than it looks.
                restart.append("device name (restart to apply)")
        if "hub" in body:
            want = bool(body["hub"])
            if want != bool(self.cfg.get("hub")):
                self.cfg["hub"] = want
                restart.append("waits/connects (restart to apply)")
        if "port" in body:
            try:
                port = int(body["port"])
            except (TypeError, ValueError):
                return {"error": f"port must be a number, not {body['port']!r}"}
            if not 1 <= port <= 65535:
                return {"error": f"port {port} is not a usable port number"}
            if port != self.cfg.get("port"):
                self.cfg["port"] = port
                restart.append("port (restart to apply)")

        where = config.save(self.cfg, self.cfg_path)
        self.log(f"settings changed from the control UI: "
                 f"{', '.join(list(live) + restart) or 'nothing'}")
        return {"ok": True, "saved_to": str(where),
                "applied_now": live, "needs_reconnect": restart}


def my_addresses() -> list:
    found = []
    try:
        for fam, _, _, _, sa in socket.getaddrinfo(socket.gethostname(), None):
            if fam == socket.AF_INET and not sa[0].startswith("127.") and sa[0] not in found:
                found.append(sa[0])
    except OSError:
        pass
    return found


PAGE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nishro Link</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#14171a;--dim:#6b7280;--line:#e5e7eb;
      --ok:#0f9d58;--warn:#e8a400;--bad:#d93025;--accent:#2563eb;--mono:ui-monospace,Consolas,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
  --bg:#0f1115;--card:#171a20;--ink:#e6e8ea;--dim:#9aa3ad;--line:#262b33}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:20px;margin:0 0 2px;display:flex;align-items:center;gap:10px}
.sub{color:var(--dim);font-size:13px;margin:0 0 20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:0 0 14px}
.card h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);margin:0 0 12px}
.dot{width:10px;height:10px;border-radius:50%;flex:0 0 auto;background:var(--dim)}
.dot.on{background:var(--ok)}.dot.off{background:var(--bad)}.dot.idle{background:var(--warn)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.m{background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:10px 12px}
.m b{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--dim);font-weight:600}
.m span{font-size:16px;font-variant-numeric:tabular-nums}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
button{font:inherit;padding:9px 16px;border-radius:9px;border:1px solid var(--line);
       background:var(--card);color:var(--ink);cursor:pointer}
button:hover{border-color:var(--accent)}
button.p{background:var(--accent);border-color:var(--accent);color:#fff}
button.d{background:var(--bad);border-color:var(--bad);color:#fff}
label{display:block;font-size:12px;color:var(--dim);margin:0 0 4px}
input,select{font:inherit;width:100%;padding:8px 10px;border-radius:8px;
             border:1px solid var(--line);background:var(--bg);color:var(--ink)}
.f{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:0 0 14px}
.chk{display:flex;gap:9px;align-items:flex-start;margin:0 0 10px}
.chk input{width:auto;margin-top:3px}
.chk small{display:block;color:var(--dim)}
pre{background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:10px;
    font:12px/1.55 var(--mono);max-height:260px;overflow:auto;margin:0;white-space:pre-wrap}
.note{font-size:12.5px;color:var(--dim);margin:10px 0 0}
.warn{color:var(--warn)}.bad{color:var(--bad)}
#msg{min-height:18px;font-size:13px;margin:8px 0 0}
</style></head><body><div class="wrap">

<h1><span class="dot" id="d"></span> Nishro Link</h1>
<p class="sub" id="who">connecting…</p>

<div class="card"><h2>Now</h2>
  <div class="grid" id="metrics"></div>
  <p class="note" id="explain"></p>
</div>

<div class="card"><h2>Control</h2>
  <div class="row">
    <button class="p" id="toggle">…</button>
    <button id="release">Give me my mouse back</button>
    <button class="d" id="quit">Quit</button>
  </div>
  <p class="note">“Give me my mouse back” is the same as pressing both Ctrl keys:
     it drops control and un-suppresses this machine immediately.</p>
</div>

<div class="card"><h2>Settings</h2>
  <div class="f">
    <div><label>Other machine is on my</label><select id="side">
      <option value="left">left</option><option value="right">right</option>
      <option value="top">top</option><option value="bottom">bottom</option></select></div>
    <div><label>What takes control</label><select id="claim">
      <option value="motion">deliberate movement</option>
      <option value="click">a click only</option>
      <option value="hotkey">hotkey only</option></select></div>
    <div><label>Listening machine's IP</label><input id="peer_addr" placeholder="192.168.1.10"></div>
    <div><label>Shared PIN</label><input id="pin" placeholder="unchanged" autocomplete="off"></div>
  </div>
  <div class="chk"><input type="checkbox" id="may_drive">
    <div><b>This machine may take control</b><small>Off makes it a screen you can
    drive but that never drives you.</small></div></div>
  <div class="chk"><input type="checkbox" id="may_be_driven">
    <div><b>This machine may be controlled</b><small>Off means nothing is ever
    injected here.</small></div></div>
  <div class="row"><button class="p" id="save">Save</button></div>
  <div id="msg"></div>
</div>

<div class="card"><h2>Log</h2><pre id="log">…</pre></div>

<script>
const T = "__TOKEN__";
const $ = i => document.getElementById(i);
let touched = new Set(), last = null;
["side","claim","peer_addr","pin","may_drive","may_be_driven"].forEach(k =>
  $(k).addEventListener("input", () => touched.add(k)));

async function api(path, body) {
  const r = await fetch(path + "?t=" + encodeURIComponent(T), {
    method: body ? "POST" : "GET",
    headers: {"Content-Type": "application/json"},
    body: body ? JSON.stringify(body) : undefined});
  return r.json();
}
const esc = s => String(s).replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));

function metric(name, value, cls) {
  return `<div class="m"><b>${name}</b><span class="${cls||''}">${esc(value)}</span></div>`;
}

function render(s) {
  last = s;
  $("who").textContent =
    `${s.node} · ${s.hub ? "listening on port " + s.port : "connecting to " + (s.peer_addr||"?")}`
    + ` · other machine on the ${s.side}`;
  $("d").className = "dot " + (!s.enabled ? "off" : s.connected ? "on" : "idle");

  const drv = s.holds ? "this machine" : (s.holder || "nobody");
  $("metrics").innerHTML =
      metric("Sharing", s.enabled ? "on" : "OFF", s.enabled ? "" : "bad")
    + metric("Peer", s.connected ? "connected" : "not connected",
             s.connected ? "" : "warn")
    + metric("Driving", drv)
    + metric("Cursor is on", s.cursor.screen + (s.cursor.remote ? " (theirs)" : " (here)"))
    + metric("My mouse", s.suppress.mouse ? "forwarded" : "local")
    + metric("My keyboard", s.suppress.keyboard ? "forwarded" : "local")
    + metric("Round trip", s.rtt_ms == null ? "—" : s.rtt_ms + " ms")
    + metric("Dropped", s.dropped, s.dropped ? "warn" : "");

  let why = "";
  if (!s.enabled) why = "Sharing is off. Nothing is captured and nothing is forwarded.";
  else if (!s.connected) why = s.hub
      ? "Waiting for the other machine to connect. If it says “timed out”, a firewall is blocking this port."
      : "Trying to reach " + (s.peer_addr||"?") + ". Retries back off automatically.";
  else if (s.suppress.mouse && s.suppress.keyboard)
      why = "The other machine is driving and the cursor is over there, so this mouse and keyboard are being forwarded to it. Move this mouse to take control back.";
  else if (s.suppress.mouse)
      why = "The other machine is driving, but the cursor is on this screen — so typing here stays here, and this mouse only serves to take control back.";
  else why = "This machine is driving. Push the pointer off the "
      + s.side + " edge to cross over.";
  $("explain").textContent = why;

  $("toggle").textContent = s.enabled ? "Turn sharing off" : "Turn sharing on";
  $("toggle").className = s.enabled ? "" : "p";

  if (!touched.has("side")) $("side").value = s.side || "left";
  if (!touched.has("claim")) $("claim").value = s.policy.claim || "motion";
  if (!touched.has("peer_addr")) $("peer_addr").value = s.peer_addr || "";
  if (!touched.has("may_drive")) $("may_drive").checked = s.policy.may_drive !== false;
  if (!touched.has("may_be_driven")) $("may_be_driven").checked = s.policy.may_be_driven !== false;
  if (!touched.has("pin")) $("pin").placeholder = s.pin_set ? "set — type to change" : "not set";

  const log = $("log");
  const atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 24;
  log.textContent = (s.log || []).join("\n");
  if (atEnd) log.scrollTop = log.scrollHeight;
}

async function poll() {
  try { render(await api("/api/status")); }
  catch (e) { $("who").textContent = "the link is not running"; $("d").className = "dot off"; }
}

function say(t, cls) { $("msg").innerHTML = `<span class="${cls||''}">${esc(t)}</span>`; }

$("toggle").onclick = async () => {
  await api(last && last.enabled ? "/api/disable" : "/api/enable", {});
  poll();
};
$("release").onclick = async () => { await api("/api/release", {}); say("Released."); poll(); };
$("quit").onclick = async () => {
  if (confirm("Quit Nishro Link? Both machines go back to their own mouse.")) {
    await api("/api/quit", {}); say("Stopping…");
  }
};
$("save").onclick = async () => {
  const body = {side: $("side").value, claim: $("claim").value,
                peer_addr: $("peer_addr").value,
                may_drive: $("may_drive").checked,
                may_be_driven: $("may_be_driven").checked};
  if ($("pin").value) body.pin = $("pin").value;
  const r = await api("/api/config", body);
  if (r.error) return say(r.error, "bad");
  touched.clear(); $("pin").value = "";
  say(r.needs_reconnect && r.needs_reconnect.length
      ? "Saved. Takes effect on the next connection: " + r.needs_reconnect.join(", ")
        + " — turn sharing off and on again to apply now."
      : "Saved and applied.", r.needs_reconnect && r.needs_reconnect.length ? "warn" : "");
  poll();
};

poll(); setInterval(poll, 1000);
</script></div></body></html>
"""
