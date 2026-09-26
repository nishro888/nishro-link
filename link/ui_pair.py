"""Adding a device: pick a side, then a name and a password. No addresses.

Pairing two machines is one decision made twice - somebody has to go first - so
both people open this same dialog and pick opposite sides:

  Let another device connect   this machine shows its NAME and a password it
                               made up, and waits
  Connect to another device    this machine lists the devices it can see, you
                               pick one (or type its name) and the password

Any number of devices can join the one that waits: each of them chooses
"Connect to another device" and picks it.

An address never appears. Addresses come from DHCP and change; the name is found
on the network each time it is needed (discovery.py). There is an Advanced
section with an address field, for the network that drops the search.

The password is generated, not chosen, for the reason given in pairing.py.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk

from . import ui_theme
from .ui_kit import Button, Kit, Toggle, field


class AddDevice:
    """A modal: pick a side, then show or type a name and a password."""

    def __init__(self, parent, api, palette, on_done=None, search=None):
        self.api = api
        self.C = dict(ui_theme.palette(), **(palette or {}))
        self.kit = Kit(self.C, ui_theme.fonts(parent))
        self.on_done = on_done
        self.status = None
        # How to look for devices. Injectable so tests need not wait out a real
        # search, and so the network is never touched from a test.
        self._search = search or (lambda: api.command("/api/discover", {}))
        self._results: queue.Queue = queue.Queue()
        self._alive = True
        self._found = []

        s = api.status()
        self.port = s.get("port") or 8770
        self.node = s.get("node") or "this device"

        self.top = tk.Toplevel(parent, bg=self.C["panel"])
        self.top.title("Add a device")
        self.top.transient(parent)
        self.top.resizable(True, False)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self._choice()
        self.top.update_idletasks()
        self._centre(parent)
        try:
            self.top.grab_set()          # modal: the choice comes first
        except tk.TclError:
            pass

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
        self.top.unbind("<Return>")
        for child in self.top.winfo_children():
            child.destroy()

    def _text(self, parent, text, role="body", tone="ink", wrap=460):
        return tk.Label(parent, text=text, font=self.kit.F[role],
                        bg=parent.cget("bg"), fg=self.C[tone], justify="left",
                        anchor="w", wraplength=wrap)

    def _page(self, title, blurb):
        self._clear()
        box = tk.Frame(self.top, bg=self.C["panel"], padx=22, pady=20)
        box.pack(fill="both", expand=True)
        self._text(box, title, "h2").pack(anchor="w")
        self._text(box, blurb, "small", "dim").pack(anchor="w", pady=(4, 14))
        return box

    # ------------------------------------------------------- step 1: which side
    def _choice(self) -> None:
        C = self.C
        box = self._page("Connect devices",
                         "Open this on both machines. One lets the other connect; "
                         "the other connects to it. It does not matter which "
                         "does which - and any number of devices can connect to "
                         "the same one.")
        for title, blurb, go, glyph in (
            ("Let another device connect to this one",
             "This machine shows its name and a password, and waits.",
             self._show, "⇲"),
            ("Connect to another device",
             "Pick the other machine from a list, or type its name, then enter "
             "the password it shows.",
             self._enter, "⇱"),
        ):
            card = tk.Frame(box, bg=C["card"], highlightthickness=1,
                            highlightbackground=C["line"], cursor="hand2")
            card.pack(fill="x", pady=5)
            inner = tk.Frame(card, bg=C["card"], padx=14, pady=12)
            inner.pack(fill="x")
            tk.Label(inner, text=glyph, font=self.kit.F["hero"], bg=C["card"],
                     fg=C["accent"]).pack(side="left", padx=(0, 14))
            words = tk.Frame(inner, bg=C["card"])
            words.pack(side="left", fill="x", expand=True)
            self._text(words, title, "h3").pack(anchor="w")
            self._text(words, blurb, "small", "dim", wrap=360).pack(anchor="w",
                                                                    pady=(2, 0))
            Button(inner, self.kit, "Choose", go, small=True).pack(side="right")
            for w in (card, inner, words):
                w.bind("<Button-1>", lambda _e, g=go: g())
        row = tk.Frame(box, bg=C["panel"])
        row.pack(fill="x", pady=(14, 0))
        Button(row, self.kit, "Cancel", self.close, kind="ghost").pack(side="right")

    # ------------------------------------------- step 2a: let the other connect
    def _show(self) -> None:
        C = self.C
        box = self._page("Let another device connect",
                         "On the other machine choose “Connect to another "
                         "device”, pick this one and type the password.")
        r = self.api.command("/api/password", {}) or {}
        card = tk.Frame(box, bg=C["card"], highlightthickness=1,
                        highlightbackground=C["accent"])
        card.pack(fill="x")
        grid = tk.Frame(card, bg=C["card"], padx=16, pady=14)
        grid.pack(fill="x")
        self.f_name = self._field(grid, "Device name", 0, r.get("name") or self.node,
                                  readonly=True, big=True)
        self.f_pin = self._field(grid, "Password", 1, r.get("pin", ""),
                                 readonly=True, big=True, mono=True)
        grid.columnconfigure(1, weight=1)

        tools = tk.Frame(box, bg=C["panel"])
        tools.pack(fill="x", pady=(8, 0))
        Button(tools, self.kit, "Copy password",
               lambda: self._copy(self.f_pin.get()), small=True).pack(side="left")
        Button(tools, self.kit, "New password", self._new_password,
               small=True).pack(side="left", padx=6)

        self.f_port = self._advanced(box, [("Port", str(self.port))])[0]
        self._footer(box, "Start waiting", self._do_wait)

    def _new_password(self) -> None:
        r = self.api.command("/api/password", {"new": True}) or {}
        self._set(self.f_pin, r.get("pin", ""))
        self._say("A new password. A device that used the old one has to be "
                  "paired again.", self.C["warn"])

    # ---------------------------------------- step 2b: connect to another one
    def _enter(self) -> None:
        C = self.C
        box = self._page("Connect to another device",
                         "Take the password from the other machine, where it is "
                         "showing it and waiting.")
        self._text(box, "DEVICES ON THIS NETWORK", "caps", "dim").pack(anchor="w")
        lst = tk.Frame(box, bg=C["panel"])
        lst.pack(fill="x", pady=(4, 0))
        self.devices = tk.Listbox(lst, height=4, activestyle="none",
                                  exportselection=False, bg=C["card"], fg=C["ink"],
                                  selectbackground=C["accent"],
                                  selectforeground=C["accent_ink"],
                                  highlightthickness=1, highlightbackground=C["line"],
                                  highlightcolor=C["accent"], relief="flat",
                                  font=self.kit.F["body"], borderwidth=0)
        self.devices.pack(side="left", fill="x", expand=True)
        self.devices.bind("<<ListboxSelect>>", self._picked)
        self.search_btn = Button(lst, self.kit, "Search again", self._start_search,
                                 small=True)
        self.search_btn.pack(side="left", padx=(8, 0), anchor="n")
        self.found_note = self._text(box, "", "small", "dim")
        self.found_note.pack(anchor="w", pady=(4, 12))

        grid = tk.Frame(box, bg=C["panel"])
        grid.pack(fill="x")
        self.f_name = self._field(grid, "Device name", 0, "")
        self.f_pin = self._field(grid, "Password", 1, "", mono=True)
        grid.columnconfigure(1, weight=1)
        self.f_name.focus_set()

        self.f_port, self.f_addr = self._advanced(
            box, [("Port", str(self.port)),
                  ("Address", "", "only if the device never shows up in the list")])
        self._footer(box, "Connect", self._do_dial)
        self.top.bind("<Return>", lambda _e: self._do_dial())
        self._start_search()

    def _start_search(self) -> None:
        self.devices.delete(0, "end")
        self.found_note.configure(text="Searching…")
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
        except tk.TclError:
            return                                   # the page was left meanwhile
        self._found = list(r.get("devices") or [])
        self.devices.delete(0, "end")
        for d in self._found:
            state = ("waiting for a connection" if d.get("waiting")
                     else "not accepting connections")
            self.devices.insert("end", f"  {d.get('name')}   —   {state}")
        if r.get("error"):
            note = f"The search failed: {r['error']}"
        elif not self._found:
            note = ("Nothing found. On the other machine, choose “Let another "
                    "device connect” - or type its name below anyway. If it "
                    "still cannot be found, a firewall is dropping the search: "
                    f"allow UDP {self.port} for Nishro Link.")
        else:
            note = "Pick one, or type a name below."
        self.found_note.configure(text=note)
        waiting = [i for i, d in enumerate(self._found) if d.get("waiting")]
        if len(waiting) == 1 and not self.f_name.get():
            # Exactly one device is waiting: that is almost certainly the one.
            self.devices.selection_set(waiting[0])
            self._picked()

    def _picked(self, _e=None) -> None:
        sel = self.devices.curselection()
        if not sel:
            return
        d = self._found[sel[0]]
        self._set(self.f_name, d.get("name", ""))
        if not d.get("waiting"):
            self._say(f"{d.get('name')} is not waiting for a connection yet - on "
                      f"it, choose “Let another device connect”.", self.C["warn"])
        else:
            self._say("")
        self.f_pin.focus_set()

    # ------------------------------------------------------------ pieces
    def _field(self, parent, text, row, value, readonly=False, big=False,
               mono=False):
        C, F = self.C, self.kit.F
        tk.Label(parent, text=text, font=F["small"], bg=parent.cget("bg"),
                 fg=C["dim"]).grid(row=row, column=0, sticky="w", padx=(0, 12),
                                   pady=5)
        e = field(parent, self.kit)
        e.configure(font=F["mono_big"] if (mono and big) else
                    F["mono"] if mono else F["h2"] if big else F["body"])
        e.grid(row=row, column=1, sticky="ew", pady=5, ipady=4)
        e.insert(0, value)
        if readonly:
            e.configure(state="readonly",
                        readonlybackground=C["card"] if big else C["card_hi"],
                        fg=C["accent"] if big else C["ink"])
        return e

    def _set(self, entry, value) -> None:
        was = str(entry.cget("state"))
        entry.configure(state="normal")
        entry.delete(0, "end")
        entry.insert(0, value)
        entry.configure(state=was)

    def _advanced(self, box, fields):
        """A collapsed section, so the ordinary path shows no ports or addresses."""
        C = self.C
        holder = tk.Frame(box, bg=C["panel"])
        holder.pack(fill="x", pady=(12, 0))
        inner = tk.Frame(holder, bg=C["panel"])
        entries = []
        for i, spec in enumerate(fields):
            entries.append(self._field(inner, spec[0], i, spec[1]))
            if len(spec) > 2:
                tk.Label(inner, text=spec[2], font=self.kit.F["tiny"], bg=C["panel"],
                         fg=C["faint"]).grid(row=i, column=2, sticky="w", padx=(8, 0))
        inner.columnconfigure(1, weight=1)
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
        return entries

    def _footer(self, box, action, command) -> None:
        C = self.C
        self.msg = self._text(box, "", "small", "dim")
        self.msg.pack(anchor="w", fill="x", pady=(14, 0))
        row = tk.Frame(box, bg=C["panel"])
        row.pack(fill="x", pady=(10, 0))
        Button(row, self.kit, action, command, kind="primary").pack(side="right")
        Button(row, self.kit, "Back", self._choice, kind="ghost").pack(side="right",
                                                                     padx=8)

    def _copy(self, text) -> None:
        try:
            self.top.clipboard_clear()
            self.top.clipboard_append(text)
            self._say("Copied.", self.C["ok"])
        except tk.TclError:
            self._say("Could not reach the clipboard.", self.C["bad"])

    # ---------------------------------------------------------------- actions
    def _do_wait(self) -> None:
        self._pair({"mode": "wait", "port": self.f_port.get()})

    def _do_dial(self) -> None:
        self._pair({"mode": "dial", "peer": self.f_name.get().strip(),
                    "pin": self.f_pin.get(), "port": self.f_port.get(),
                    "peer_addr": self.f_addr.get().strip()})

    def _pair(self, body) -> None:
        r = self.api.command("/api/pair", body) or {}
        if r.get("error"):
            return self._say(r["error"], self.C["bad"])
        self.status = r
        if self.on_done:
            self.on_done(r)
        if r.get("waiting"):
            # Staying open on purpose: the name and password on screen are what
            # is being typed on the other machine right now.
            self._say("Waiting for devices to connect. Leave this open until they "
                      "have - they need what is on screen. Any number can join.",
                      self.C["ok"])
        else:
            self._say("Looking for it and connecting…", self.C["ok"])
            self.top.after(1200, self.close)

    def _say(self, text, colour=None) -> None:
        self.msg.configure(text=text, fg=colour or self.C["dim"])

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
