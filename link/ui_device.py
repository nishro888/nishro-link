"""One device, in full: what it is, what it may do, and what can be done to it.

Opened from its card. Everything a person might want to know or change about
one machine is here, and only what they are allowed to change is live:

  rename          any device, from any device in the group
  control rights  any device while it is on - it is the one that applies them
  remove / leave  any device but the hub, from any device; a member leaves

A member does these by asking the hub, which keeps the group - so while the
hub cannot be reached, only this device's own name and rights can change.

Anything that cannot be changed says why, rather than just being grey.
The dialog follows the device by its ID, so it stays on the same machine when
that machine is renamed from here or from anywhere else.
"""
from __future__ import annotations

import tkinter as tk

from . import ui_theme
from .ui_kit import Button, Kit, Monitors, Pill, Toggle, ago, field

POLL_MS = 700


class DeviceDetails:
    def __init__(self, parent, api, palette, name, confirm=None, on_change=None):
        self.api = api
        self.C = dict(ui_theme.palette(), **(palette or {}))
        self.kit = Kit(self.C, ui_theme.fonts(parent))
        self.confirm = confirm or (lambda title, text: True)
        self.on_change = on_change
        self._alive = True
        self._poll_id = None
        self._editing = False
        self._sig = None
        d = self._find(name=name)
        self.name = name
        self.dev_id = (d or {}).get("id")

        self.top = tk.Toplevel(parent, bg=self.C["panel"])
        self.top.title(f"{name} - Nishro Link")
        self.top.transient(parent)
        self.top.resizable(True, False)
        self.top.minsize(460, 0)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<Escape>", lambda _e: self.close())
        self.box = tk.Frame(self.top, bg=self.C["panel"], padx=24, pady=20)
        self.box.pack(fill="both", expand=True)
        self._render()
        self.top.update_idletasks()
        try:
            x = parent.winfo_rootx() + (parent.winfo_width() - self.top.winfo_reqwidth()) // 2
            y = parent.winfo_rooty() + 80
            self.top.geometry(f"+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass
        ui_theme.dark_title_bar(self.top)
        self._poll_id = self.top.after(POLL_MS, self._poll)

    # --------------------------------------------------------------- data
    def _status(self) -> dict:
        return self.api.status()

    def _find(self, s=None, name=None):
        s = s or self._status()
        devs = s.get("devices") or []
        if getattr(self, "dev_id", None):
            for d in devs:
                if d.get("id") == self.dev_id:
                    return d
        name = name or getattr(self, "name", None)
        return next((d for d in devs if d["name"] == name), None)

    def _poll(self) -> None:
        self._poll_id = None
        if not self._alive:
            return
        if not self._editing:
            self._render()
        self._poll_id = self.top.after(POLL_MS, self._poll)

    # ------------------------------------------------------------- render
    def _render(self) -> None:
        s = self._status()
        d = self._find(s)
        sig = repr((d, s.get("role"), s.get("connected")))
        if sig == self._sig:
            return
        self._sig = sig
        for w in self.box.winfo_children():
            w.destroy()
        C, F, kit = self.C, self.kit.F, self.kit
        if d is None:
            tk.Label(self.box, text=f"{self.name} is no longer in this group.",
                     font=F["h3"], bg=C["panel"], fg=C["dim"]).pack(anchor="w")
            Button(self.box, kit, "Close", self.close, kind="ghost"
                   ).pack(anchor="e", pady=(16, 0))
            return
        self.name = d["name"]
        self.top.title(f"{d['name']} - Nishro Link")
        me, role = d["me"], s.get("role")
        manage = bool(s.get("can_manage", role == "hub"))

        head = tk.Frame(self.box, bg=C["panel"])
        head.pack(fill="x")
        pic = Monitors(head, kit)
        pic.configure(bg=C["panel"])
        pic.pack(side="left", padx=(0, 16))
        colour = C["mine"] if me else (C["theirs"] if d["online"] else C["offline"])
        fill = C["mine_fill"] if me else (C["theirs_fill"] if d["online"]
                                          else C["offline_fill"])
        pic.draw(_parts(d), colour, fill)
        words = tk.Frame(head, bg=C["panel"])
        words.pack(side="left", fill="x", expand=True)
        row = tk.Frame(words, bg=C["panel"])
        row.pack(fill="x")
        self.name_label = tk.Label(row, text=d["name"], font=F["h1"], bg=C["panel"],
                                   fg=C["ink"])
        self.name_label.pack(side="left")
        if me:
            Pill(row, kit, "THIS DEVICE", "accent").pack(side="left", padx=(10, 0))
        if d["hub"]:
            Pill(row, kit, "HUB", "accent2").pack(side="left", padx=(6, 0))
        rtt = d.get("rtt_ms")
        if me:
            state = "this device"
        elif d["online"]:
            state = "● online" + (f"  ·  {rtt:.1f} ms" if rtt else "")
        else:
            state = f"○ offline  ·  last seen {ago(d.get('last_seen'))}"
        tk.Label(words, text=state, font=F["small"], bg=C["panel"],
                 fg=C["ok"] if d["online"] and not me else C["dim"], anchor="w"
                 ).pack(anchor="w", pady=(2, 0))

        # ---- name
        self.rename_row = tk.Frame(self.box, bg=C["panel"])
        self.rename_row.pack(fill="x", pady=(16, 0))
        line = self._line(self.rename_row, "Name", d["name"])
        if me or manage:
            Button(line, kit, "Rename", self._start_rename, kind="ghost",
                   small=True).pack(side="right")
        elif not me:
            self._note(f"Not connected to {s.get('group') or 'the hub'} - "
                       f"changes unavailable")
        if d.get("rename_to"):
            self._note("Renamed  ·  applies when it's next online")

        # ---- facts
        facts = tk.Frame(self.box, bg=C["panel"])
        facts.pack(fill="x", pady=(4, 0))
        n = len(d["displays"])
        sizes = ", ".join(f"{w}×{h}" for w, h in d["displays"])
        self._line(facts, "Displays", f"{n}  ·  {sizes}")
        if d.get("addr") or me:
            addr = d.get("addr") or ", ".join(s.get("addresses") or []) or "—"
            self._line(facts, "Address", addr)
        self._line(facts, "Version", d.get("version") or "—")
        if not me and d.get("first_seen"):
            self._line(facts, "In the group", f"since {ago(d['first_seen'])}")
        if d.get("id"):
            self._line(facts, "Device ID", d["id"])

        # ---- control rights
        tk.Frame(self.box, bg=C["line"], height=1).pack(fill="x", pady=(16, 12))
        tk.Label(self.box, text="CONTROL", font=F["caps"], bg=C["panel"],
                 fg=C["dim"]).pack(anchor="w")
        editable, why = self._rights_editable(d, s)
        self.drive = tk.BooleanVar(value=bool(d.get("may_drive", True)))
        self.driven = tk.BooleanVar(value=bool(d.get("may_be_driven", True)))
        for var, text in ((self.drive, "Can control other devices"),
                          (self.driven, "Can be controlled")):
            r = tk.Frame(self.box, bg=C["panel"])
            r.pack(fill="x", pady=4)
            t = Toggle(r, kit, var, command=self._rights_changed)
            t.pack(side="left")
            if not editable:
                # Shown, not offered: no click, and drawn in the faint colours.
                t.unbind("<Button-1>")
                t.configure(cursor="arrow")
                t._kit = Kit(dict(C, accent=C["line_hi"], line_hi=C["line"],
                                  accent_ink=C["faint"], ink=C["faint"]), kit.F)
                t._draw()
            tk.Label(r, text=text, font=F["body"], bg=C["panel"],
                     fg=C["ink"] if editable else C["faint"]).pack(side="left",
                                                                   padx=10)
        if why:
            self._note(why)
        if not d.get("may_be_driven", True):
            self._note("The pointer stops at its edges", tone="warn")

        # ---- actions
        tk.Frame(self.box, bg=C["line"], height=1).pack(fill="x", pady=(16, 12))
        bar = tk.Frame(self.box, bg=C["panel"])
        bar.pack(fill="x")
        if not me and manage and not (role == "member" and d["hub"]):
            Button(bar, kit, "Remove from the group", self._remove, kind="danger",
                   small=True).pack(side="left")
        elif me and role == "member":
            Button(bar, kit, "Leave this group", self._leave, kind="danger",
                   small=True).pack(side="left")
        Button(bar, kit, "Close", self.close, kind="ghost").pack(side="right")
        self.msg = tk.Label(self.box, text="", font=F["small"], bg=C["panel"],
                            fg=C["dim"], anchor="w", justify="left", wraplength=420)
        self.msg.pack(anchor="w", fill="x", pady=(8, 0))

    def _line(self, parent, key, value):
        C, F = self.C, self.kit.F
        r = tk.Frame(parent, bg=C["panel"])
        r.pack(fill="x", pady=2)
        tk.Label(r, text=key, font=F["small"], bg=C["panel"], fg=C["dim"],
                 width=13, anchor="w").pack(side="left")
        lb = tk.Label(r, text=value, font=F["body"], bg=C["panel"], fg=C["ink"],
                      anchor="w")
        lb.pack(side="left", fill="x", expand=True)
        return r

    def _note(self, text, tone="faint"):
        tk.Label(self.box, text=text, font=self.kit.F["small"], bg=self.C["panel"],
                 fg=self.C[tone], anchor="w", justify="left", wraplength=420
                 ).pack(anchor="w", fill="x", pady=(2, 0))

    @staticmethod
    def _rights_editable(d, s):
        if d["me"]:
            return True, None
        if not s.get("can_manage", s.get("role") == "hub"):
            return False, (f"Unavailable while disconnected from "
                           f"{s.get('group') or 'the hub'}")
        if not d["online"]:
            return False, f"Available when {d['name']} is online"
        return True, None

    # ------------------------------------------------------------ actions
    def _start_rename(self) -> None:
        C, kit = self.C, self.kit
        self._editing = True
        for w in self.rename_row.winfo_children():
            w.destroy()
        tk.Label(self.rename_row, text="Name", font=kit.F["small"], bg=C["panel"],
                 fg=C["dim"], width=13, anchor="w").pack(side="left")
        self.f_name = field(self.rename_row, kit)
        self.f_name.insert(0, self.name)
        self.f_name.select_range(0, "end")
        self.f_name.pack(side="left", fill="x", expand=True, ipady=3)
        self.f_name.focus_set()
        Button(self.rename_row, kit, "Save", self._save_rename, kind="primary",
               small=True).pack(side="left", padx=(8, 0))
        Button(self.rename_row, kit, "Cancel", self._cancel_rename, kind="ghost",
               small=True).pack(side="left", padx=(4, 0))
        self.f_name.bind("<Return>", lambda _e: self._save_rename())
        self.f_name.bind("<Escape>", lambda _e: self._cancel_rename() or "break")

    def _cancel_rename(self) -> None:
        self._editing = False
        self._sig = None
        self._render()

    def _save_rename(self) -> None:
        new = self.f_name.get()
        d = self._find() or {}
        body = {"new": new} if d.get("me") else {"name": self.name, "new": new}
        r = self.api.command("/api/rename", body) or {}
        if r.get("error"):
            self.msg.configure(text=r["error"][:1].upper() + r["error"][1:],
                               fg=self.C["bad"])
            return
        self._editing = False
        self.name = " ".join(new.split())
        self._sig = None
        self._render()
        self.msg.configure(
            text=("Renamed  ·  applies when it's next online"
                  if r.get("pending") else "Renamed"), fg=self.C["ok"])
        if self.on_change:
            self.on_change()

    def _rights_changed(self) -> None:
        r = self.api.command("/api/rights", {
            "name": self.name, "may_drive": self.drive.get(),
            "may_be_driven": self.driven.get()}) or {}
        if r.get("error"):
            self.msg.configure(text=r["error"], fg=self.C["bad"])
            self._sig = None
            self.top.after(1500, self._render)
            return
        self.msg.configure(text="Saved", fg=self.C["ok"])
        if self.on_change:
            self.on_change()

    def _remove(self) -> None:
        d = self._find() or {}
        if not self.confirm(
                "Remove device",
                f"Remove {self.name} from the group?\n\n"
                + (f"{self.name} is disconnected now and forgets this group."
                   if d.get("online") else
                   f"{self.name} is switched off; it is told when it comes back.")
                + " To add it again, you will need the password it shows."):
            return
        r = self.api.command("/api/remove", {"name": self.name}) or {}
        if r.get("error"):
            self.msg.configure(text=r["error"], fg=self.C["bad"])
            return
        if self.on_change:
            self.on_change()
        self.close()

    def _leave(self) -> None:
        s = self._status()
        if not self.confirm(
                "Leave the group",
                f"Leave {s.get('group')}'s group?\n\nThis device stops sharing with "
                f"the others and gets a password of its own. To come back, add it "
                f"again."):
            return
        self.api.command("/api/leave", {})
        if self.on_change:
            self.on_change()
        self.close()

    def close(self) -> None:
        self._alive = False
        if self._poll_id is not None:
            try:
                self.top.after_cancel(self._poll_id)
            except tk.TclError:
                pass
        try:
            self.top.destroy()
        except tk.TclError:
            pass


def _parts(d) -> list:
    parts = [tuple(r) for r in d.get("rects") or []]
    if not parts:
        x = 0
        for w, h in d["displays"]:
            parts.append((x, 0, w, h))
            x += w
    return parts
