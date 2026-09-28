"""Adding a device: pick it, type the password it shows, watch it connect.

There is no "which side are you" step any more. Every device shows its name and
password (Devices page) and is ready to be added; this dialog lists what is on
the network and says, per device, what adding it means:

  on its own             "Add"             it joins THIS group   (invite)
  in another group       "Join its group"  this device joins THAT group (join)
  already in this group  nothing to do

Either way the person types ONE password - the one shown on the device they
picked - and the dialog follows the attempt step by step until it says
"connected" or says why not, in words, with what to do about it. It used to
close itself after a second and a half with "looking for it and connecting",
whatever then happened: reported as "I don't see peer connected or not".

A wrong password changes nothing on either side: invite proves it before
anything moves, and join checks it with the hub first (Node.probe).

The other direction is always on screen too, at the bottom: this device's own
name and password, for adding it from the other machine instead.
"""
from __future__ import annotations

import queue
import threading
import time
import tkinter as tk

from . import pairing, ui_theme
from .ui_kit import Button, Kit, Toggle, field, info, placeholder

POLL_MS = 150

STEPS = {
    "invite": ("Find {t}", "Check the password", "{t} joins this group", "Connected"),
    "join": ("Find {t}", "Check the password", "Join {t}'s group", "Connected"),
}
# Which step each phase is on.
STEP_OF = {"searching": 0, "connecting": 0, "verifying": 1, "checked": 2,
           "joining": 2, "connected": 3}


def failure(mode: str, reason: str, target: str, detail=None, port=8770):
    """What went wrong, as (headline, what to do) - short, never a bare code."""
    t = target or "the device"
    return {
        "wrong_password": ("Wrong password", f"Check the password shown on {t}."),
        "not_found": (f"{t} not found",
                      f"Is Nishro Link open on it? A firewall may block UDP {port}."),
        "unreachable": (f"Can't reach {t}",
                        f"Its firewall may be blocking TCP {port}."),
        "busy": (f"{t} has its own group", ""),
        "in_group": (f"{t} is in {detail}'s group", ""),
        "paused": (f"Sharing is off on {t}", "Turn it on there, then retry."),
        "version": ("Version mismatch", "Install the same version on both."),
        "timeout": (f"{t} didn't connect", "Check that sharing is on there."),
        "name_taken": ("Name already in use", "Rename this device, then retry."),
        "impostor": (f"Couldn't verify {t}", "It didn't prove the password."),
        "not_connected": ("Not connected to the hub", "Add it from the hub instead."),
        "refused": (f"{t} refused", str(detail or "")),
    }.get(reason, ("That didn't work", f"{reason}{': ' + str(detail) if detail else ''}"))


def row_action(f: dict, me: dict):
    """(what it is, tone, button text, mode, target) for one device found.
    mode None: nothing to do - or not allowed, and `what` says why."""
    name, group = f.get("name"), f.get("group")
    mine = me.get("group")
    if (group and mine and group == mine) or name in me.get("members", ()):
        return "In this group", "dim", None, None, None
    has_group = me.get("role") == "hub"     # devices of its own, so cannot join
    if f.get("waiting") and f.get("alone"):
        if me.get("role") == "member" and not me.get("connected"):
            return "On its own", "ok", None, None, None
        return "On its own", "ok", "Add", "invite", name
    if f.get("waiting"):
        if has_group:
            return "Has its own group", "accent2", None, None, None
        return "Has its own group", "accent2", "Join its group", "join", name
    if group:
        if has_group:
            return f"In {group}'s group", "dim", None, None, None
        return f"In {group}'s group", "dim", "Join that group", "join", group
    return "Sharing is off", "faint", None, None, None


class AddDevice:
    """A modal: the devices found, then one password, then the outcome."""

    def __init__(self, parent, api, palette, on_done=None, search=None,
                 on_arrange=None, start=None):
        self.api = api
        self.C = dict(ui_theme.palette(), **(palette or {}))
        self.kit = Kit(self.C, ui_theme.fonts(parent))
        self.on_done = on_done
        self.on_arrange = on_arrange
        # How to look for devices. Injectable so tests need not wait out a real
        # search, and so the network is never touched from a test.
        self._search = search or (lambda: api.command("/api/discover", {}))
        self._results: queue.Queue = queue.Queue()
        self._alive = True
        self._found = []
        self.mode = self.target = None
        self.outcome = None               # "connected" or a failure reason
        self._poll_id = None
        self._t0 = 0.0

        s = api.status()
        self.port = s.get("port") or 8770
        self.node = s.get("node") or "this device"

        self.top = tk.Toplevel(parent, bg=self.C["panel"])
        self.top.withdraw()                   # built hidden: see ui_theme.reveal
        self.top.title("Add a device")
        self.top.transient(parent)
        self.top.resizable(True, False)
        self.top.minsize(480, 0)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<Escape>", lambda _e: self.close())
        if start:
            self._password(*start)
        else:
            self._list()
        self.top.update_idletasks()
        self._centre(parent)

        def modal():
            try:
                self.top.grab_set()
            except tk.TclError:
                pass
        ui_theme.reveal(self.top, then=modal)

    # ------------------------------------------------------------- pieces
    def _centre(self, parent) -> None:
        w, h = self.top.winfo_reqwidth(), self.top.winfo_reqheight()
        try:
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            x, y = px + (pw - w) // 2, py + (ph - h) // 3
        except tk.TclError:
            x = y = 100
        self.top.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _clear(self) -> None:
        if self._poll_id is not None:
            try:
                self.top.after_cancel(self._poll_id)
            except tk.TclError:
                pass
            self._poll_id = None
        self.top.unbind("<Return>")
        for child in self.top.winfo_children():
            child.destroy()

    def _text(self, parent, text, role="body", tone="ink", wrap=470):
        return tk.Label(parent, text=text, font=self.kit.F[role],
                        bg=parent.cget("bg"), fg=self.C[tone], justify="left",
                        anchor="w", wraplength=wrap)

    def _page(self, title, blurb):
        self._clear()
        box = tk.Frame(self.top, bg=self.C["panel"], padx=24, pady=20)
        box.pack(fill="both", expand=True)
        self._text(box, title, "h2").pack(anchor="w")
        if blurb:
            self._text(box, blurb, "small", "dim").pack(anchor="w", pady=(4, 14))
        return box

    def _me(self) -> dict:
        s = self.api.status()
        return {"role": s.get("role"), "group": s.get("group"),
                "members": s.get("members") or [], "connected": s.get("connected"),
                "node": s.get("node")}

    # ------------------------------------------------------- 1: the list
    def _list(self) -> None:
        C = self.C
        box = self._page("Add a device", "Devices on your network")
        self.rows = tk.Frame(box, bg=C["card"], highlightthickness=1,
                             highlightbackground=C["line"])
        self.rows.pack(fill="x")
        bar = tk.Frame(box, bg=C["panel"])
        bar.pack(fill="x", pady=(6, 0))
        self.found_note = self._text(bar, "", "small", "dim", wrap=360)
        self.found_note.pack(side="left", fill="x", expand=True)
        self.search_btn = Button(bar, self.kit, "Search again", self._start_search,
                                 kind="ghost", small=True)
        self.search_btn.pack(side="right")

        # Typed by hand, for a device the search does not reach.
        hand = tk.Frame(box, bg=C["panel"])
        hand.pack(fill="x", pady=(14, 0))
        self._text(hand, "Not listed?", "small", "dim").pack(side="left")
        self.f_name = field(hand, self.kit, width=18)
        self.f_name.pack(side="left", padx=8, ipady=3)
        placeholder(self.f_name, self.kit, "Device name")
        self.btn_by_name = Button(hand, self.kit, "Next", self._by_name, small=True)
        self.btn_by_name.pack(side="left")
        self.f_addr = self._advanced(box)
        self.f_name.bind("<Return>", lambda _e: self._by_name())

        self._reverse(box)
        row = tk.Frame(box, bg=C["panel"])
        row.pack(fill="x", pady=(14, 0))
        Button(row, self.kit, "Close", self.close, kind="ghost").pack(side="right")
        self._start_search()

    def _advanced(self, box):
        C = self.C
        holder = tk.Frame(box, bg=C["panel"])
        holder.pack(fill="x", pady=(8, 0))
        inner = tk.Frame(holder, bg=C["panel"])
        self._text(inner, "Address", "small", "dim").pack(side="left")
        entry = field(inner, self.kit, width=18)
        entry.pack(side="left", padx=8, ipady=3)
        placeholder(entry, self.kit, "192.168.1.20")
        info(inner, self.kit, "Only for a network that drops searches, where the "
                              "device is never found by name.").pack(side="left")
        shown = tk.BooleanVar(value=False)

        def flip():
            if shown.get():
                inner.pack(fill="x", pady=(6, 0))
            else:
                inner.pack_forget()
        row = tk.Frame(holder, bg=C["panel"])
        row.pack(anchor="w")
        Toggle(row, self.kit, shown, command=flip).pack(side="left")
        tk.Label(row, text="Advanced", font=self.kit.F["small"], bg=C["panel"],
                 fg=C["dim"]).pack(side="left", padx=8)
        self.advanced = shown
        return entry

    def _reverse(self, box) -> None:
        """The other way round, always in view: add THIS device from there."""
        C = self.C
        pw = (self.api.command("/api/password", {}) or {}).get("pin", "")
        wrap = tk.Frame(box, bg=C["card"], highlightthickness=1,
                        highlightbackground=C["line"])
        wrap.pack(fill="x", pady=(18, 0))
        inner = tk.Frame(wrap, bg=C["card"], padx=14, pady=10)
        inner.pack(fill="x")
        self._text(inner, "Pair from the other device", "h3", "ink"
                   ).grid(row=0, column=0, columnspan=4, sticky="w")
        self._text(inner, "Name", "small", "dim").grid(row=1, column=0, sticky="w",
                                                      pady=(6, 0))
        self._text(inner, self.node, "h3", "ink").grid(row=1, column=1, sticky="w",
                                                      padx=(8, 20), pady=(6, 0))
        self._text(inner, "Password", "small", "dim").grid(row=1, column=2,
                                                          sticky="w", pady=(6, 0))
        self.my_pw = self._text(inner, pw, "h3", "accent")
        self.my_pw.grid(row=1, column=3, sticky="w", padx=(8, 8), pady=(6, 0))
        Button(inner, self.kit, "Copy", lambda: self._copy(pw), small=True
               ).grid(row=1, column=4, sticky="e", pady=(6, 0))
        inner.columnconfigure(3, weight=1)

    # ------------------------------------------------------------ search
    def _start_search(self) -> None:
        for w in self.rows.winfo_children():
            w.destroy()
        self._text(self.rows, "  Searching…", "body", "dim").pack(anchor="w",
                                                                   pady=10)
        self.found_note.configure(text="")
        self.search_btn.set_enabled(False)
        threading.Thread(target=self._search_thread, daemon=True).start()
        self.top.after(100, self._search_poll)

    def _search_thread(self) -> None:
        # Off the Tk thread: a search takes about a second, and a window that
        # freezes for a second every time it is opened feels broken.
        try:
            r = self._search() or {}
        except Exception as e:                       # shown, never raised
            r = {"error": repr(e)}
        self._results.put(r)

    def _search_poll(self) -> None:
        if not self._alive:
            return
        try:
            r = self._results.get_nowait()
        except queue.Empty:
            self.top.after(100, self._search_poll)
            return
        self._show_found(r)

    def _show_found(self, r) -> None:
        try:
            self.search_btn.set_enabled(True)
            for w in self.rows.winfo_children():
                w.destroy()
        except tk.TclError:
            return                                   # the page was left meanwhile
        self._found = list(r.get("devices") or [])
        me = self._me()
        if r.get("error"):
            self._text(self.rows, f"  The search failed: {r['error']}", "body",
                       "bad").pack(anchor="w", pady=10)
        elif not self._found:
            row = tk.Frame(self.rows, bg=self.C["card"])
            row.pack(fill="x", padx=12, pady=10)
            self._text(row, "No devices found", "body", "dim").pack(side="left")
            info(row, self.kit, f"Open Nishro Link on the other computer. If it "
                                f"still doesn't appear, a firewall is dropping the "
                                f"search (UDP {self.port}) - type its name below."
                 ).pack(side="left", padx=(6, 0))
        for i, f in enumerate(self._found):
            self._row(f, me, first=(i == 0))
        n = len(self._found)
        self.found_note.configure(text=(f"{n} found" if n else ""))
        if me.get("role") == "hub" and any(not row_action(f, me)[2]
                                           and f.get("group") != me.get("group")
                                           for f in self._found):
            self.found_note.configure(text=f"{n} found  ·  a hub can't join "
                                           f"another group")

    def _row(self, f, me, first=False) -> None:
        C = self.C
        what, tone, action, mode, target = row_action(f, me)
        if not first:
            tk.Frame(self.rows, bg=C["line"], height=1).pack(fill="x")
        row = tk.Frame(self.rows, bg=C["card"], padx=12, pady=9)
        row.pack(fill="x")
        dot = tk.Canvas(row, width=10, height=10, highlightthickness=0,
                        bg=C["card"])
        dot.create_oval(1, 1, 9, 9, outline="",
                        fill=C["ok"] if f.get("waiting") else C["faint"])
        dot.pack(side="left", padx=(0, 10))
        tk.Label(row, text=f.get("name"), font=self.kit.F["h3"], bg=C["card"],
                 fg=C["ink"]).pack(side="left")
        tk.Label(row, text="  " + what, font=self.kit.F["small"], bg=C["card"],
                 fg=C.get(tone, C["dim"])).pack(side="left")
        if action:
            Button(row, self.kit, action,
                   lambda m=mode, t=target: self._password(m, t),
                   kind="primary" if mode == "invite" else "secondary",
                   small=True).pack(side="right")

    def _by_name(self) -> None:
        name = self.f_name.get().strip()
        addr = self.f_addr.get().strip() if self.advanced.get() else ""
        if not name and not addr:
            self.found_note.configure(text="Type the other device's name first.",
                                      fg=self.C["warn"])
            return
        if name and name.casefold() == self.node.casefold():
            self.found_note.configure(text=f"'{name}' is this device.",
                                      fg=self.C["warn"])
            return
        # Not knowing whether it is on its own or in a group, try adding it; if
        # it turns out to have a group, the same password joins that instead.
        mode = "join" if self._me().get("role") == "member" else "invite"
        self._password(mode, name or addr, addr=addr or None)

    # ---------------------------------------------------- 2: the password
    def _password(self, mode, target, addr=None) -> None:
        C = self.C
        self.mode, self.target, self.addr = mode, target, addr
        self.outcome = None
        title = f"Add {target}" if mode == "invite" else f"Join {target}'s group"
        box = self._page(title, None)
        sub = tk.Frame(box, bg=C["panel"])
        sub.pack(anchor="w", pady=(4, 12))
        self._text(sub, f"Enter the password shown on {target}", "body", "dim"
                   ).pack(side="left")
        info(sub, self.kit, f"It is on {target}'s Devices page, under This "
                            f"device. Case, spaces and dashes are ignored."
             ).pack(side="left", padx=(6, 0))
        self.f_pin = field(box, self.kit)
        self.f_pin.configure(font=self.kit.F["h2"], justify="center")
        self.f_pin.pack(fill="x", ipady=6)
        placeholder(self.f_pin, self.kit, "word-word-word-word")
        self.f_pin.focus_set()

        self.steps = tk.Frame(box, bg=C["panel"])
        self.steps.pack(fill="x", pady=(16, 0))
        self._draw_steps(-1)
        self.msg = self._text(box, "", "h3", "dim")
        self.msg.pack(anchor="w", fill="x", pady=(12, 0))
        self.msg_detail = self._text(box, "", "small", "dim")
        self.msg_detail.pack(anchor="w", fill="x")
        self.extra = tk.Frame(box, bg=C["panel"])
        self.extra.pack(fill="x")

        row = tk.Frame(box, bg=C["panel"])
        row.pack(fill="x", pady=(16, 0))
        self.btn_go = Button(row, self.kit,
                             "Add" if mode == "invite" else "Join", self._go,
                             kind="primary")
        self.btn_go.pack(side="right")
        self.btn_back = Button(row, self.kit, "Back", self._list, kind="ghost")
        self.btn_back.pack(side="right", padx=8)
        self.top.bind("<Return>", lambda _e: self._go())

    def _draw_steps(self, at: int, failed: bool = False, done: bool = False) -> None:
        C, F = self.C, self.kit.F
        for w in self.steps.winfo_children():
            w.destroy()
        self.step_labels = []
        for i, text in enumerate(STEPS[self.mode]):
            if done or i < at:
                mark, colour = "✓", C["ok"]
            elif i == at:
                mark, colour = ("✖", C["bad"]) if failed else ("●", C["accent"])
            else:
                mark, colour = "○", C["faint"]
            row = tk.Frame(self.steps, bg=C["panel"])
            row.pack(fill="x", pady=1)
            tk.Label(row, text=mark, font=F["h3"], bg=C["panel"], fg=colour,
                     width=2).pack(side="left")
            lb = tk.Label(row, text=text.format(t=self.target), font=F["body"],
                          bg=C["panel"],
                          fg=C["ink"] if (i <= at or done) else C["faint"])
            lb.pack(side="left")
            self.step_labels.append(lb)

    def _go(self) -> None:
        if self.outcome == "connected":
            return self.close()
        pin = self.f_pin.get()
        bad = pairing.problem(pin)
        if bad:
            self._tell("Password too short",
                       f"At least {pairing.MIN_LENGTH} characters.", "warn")
            return
        body = {"name": self.target, "pin": pin}
        if self.addr:
            body["addr"] = self.addr
        r = self.api.command("/api/invite" if self.mode == "invite" else "/api/join",
                             body) or {}
        if r.get("error"):
            self._tell("Can't do that", r["error"][:1].upper() + r["error"][1:])
            return
        self._t0 = time.time()
        self.outcome = None
        for w in self.extra.winfo_children():
            w.destroy()
        self.btn_go.set_enabled(False)
        self.f_pin.configure(state="disabled")
        self._tell("", "")
        self._draw_steps(0)
        self._poll_id = self.top.after(POLL_MS, self._follow)

    def _follow(self) -> None:
        """Follow the attempt in status()["adding"] until it ends."""
        self._poll_id = None
        if not self._alive:
            return
        try:
            a = self.api.status().get("adding") or {}
        except Exception as e:
            a = {"phase": "failed", "reason": "error", "detail": repr(e)}
        fresh = (a.get("since") or 0) >= self._t0 - 1.0 and \
            a.get("mode") in (None, self.mode)
        phase = a.get("phase") if fresh else "searching"
        if phase == "connected":
            return self._done(a)
        if phase == "failed":
            return self._failed(a)
        self._draw_steps(STEP_OF.get(phase, 0))
        if phase == "verifying" and a.get("detail"):
            self.target = a["detail"] if self.mode == "join" else self.target
        self._poll_id = self.top.after(POLL_MS, self._follow)

    def _failed(self, a) -> None:
        reason = a.get("reason") or "error"
        self.outcome = reason
        at = max(0, min(3, STEP_OF.get(self._last_phase(reason), 0)))
        self._draw_steps(at, failed=True)
        self._tell(*failure(self.mode, reason, a.get("target") or self.target,
                            a.get("detail"), self.port))
        self.f_pin.configure(state="normal")
        self.btn_go.set_enabled(True)
        self.btn_go.set_text("Try again")
        if reason == "wrong_password":
            self.f_pin.select_range(0, "end")
            self.f_pin.focus_set()
        # The same password joins its group instead, when it turned out to have
        # one - offered, not done behind the person's back.
        me = self._me()
        if reason in ("busy", "in_group") and me.get("role") != "hub":
            # busy: it is the hub of its own group. in_group: detail names it.
            group = a.get("detail") if reason == "in_group" else self.target
            Button(self.extra, self.kit, f"Join {group}'s group instead",
                   lambda g=group: self._switch_to_join(g),
                   kind="primary", small=True).pack(anchor="w", pady=(8, 0))
        elif reason in ("busy", "in_group"):
            self._text(self.extra, "Both have groups of their own - remove the "
                                   "devices from one first.", "small", "dim"
                       ).pack(anchor="w", pady=(6, 0))

    def _tell(self, headline, detail="", tone="bad") -> None:
        self.msg.configure(text=headline, fg=self.C[tone] if headline else self.C["dim"])
        self.msg_detail.configure(text=detail)

    @staticmethod
    def _last_phase(reason) -> str:
        return {"wrong_password": "verifying", "impostor": "verifying",
                "name_taken": "verifying", "busy": "verifying",
                "timeout": "joining", "refused": "verifying"}.get(reason,
                                                                   "searching")

    def _switch_to_join(self, group) -> None:
        pin = self.f_pin.get()
        self._password("join", group)
        self.f_pin.insert(0, pin)
        self._go()

    def _done(self, a) -> None:
        C = self.C
        self.outcome = "connected"
        who = self.target if self.mode == "invite" else (a.get("detail")
                                                          or self.target)
        box = self._page("", None)
        tk.Label(box, text="✓", font=self.kit.F["hero"], bg=C["panel"],
                 fg=C["ok"]).pack(anchor="w")
        head = (f"{who} is connected" if self.mode == "invite"
                else f"Connected to {who}'s group")
        self._text(box, head, "h2").pack(anchor="w", pady=(2, 6))
        self._text(box, "Next, arrange your screens to match your desk.", "body",
                   "dim").pack(anchor="w")
        row = tk.Frame(box, bg=C["panel"])
        row.pack(fill="x", pady=(18, 0))
        Button(row, self.kit, "Arrange  →", self._arrange,
               kind="primary").pack(side="right")
        Button(row, self.kit, "Done", self.close, kind="ghost").pack(side="right",
                                                                   padx=8)
        self.top.bind("<Return>", lambda _e: self._arrange())
        if self.on_done:
            self.on_done({"ok": True, "mode": self.mode, "name": who})

    def _arrange(self) -> None:
        cb = self.on_arrange
        self.close()
        if cb:
            cb()

    # --------------------------------------------------------------- misc
    def _copy(self, text) -> None:
        try:
            self.top.clipboard_clear()
            self.top.clipboard_append(text)
            self.found_note.configure(text="Password copied.", fg=self.C["ok"])
        except tk.TclError:
            pass

    def close(self) -> None:
        self._alive = False
        try:
            self.top.grab_release()
        except tk.TclError:
            pass
        try:
            self.top.destroy()
        except tk.TclError:
            pass
