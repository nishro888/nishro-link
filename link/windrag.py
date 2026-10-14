"""Noticing a window dragged across the edge towards another computer.

The other half of stage 4 (docs/moving-windows.md section 4.6). A frame is
dragged back home by the window manager's own title bar, and window_frame.py
watches where it lands; this is the same gesture the other way round - take
one of THIS computer's windows by its title bar, drag it off the side of the
screen another computer is on, let go, and it goes there.

Nothing here touches Windows. The system-specific half (windrag_win.py)
feeds it: a drag began, where the window is now, a drag ended. So the whole
decision can be driven by a test with no window on any screen, and the
thresholds are the very ones the frame uses (winmove.past_edge,
winmove.THROW_BACK) so the gesture feels the same in both directions.
"""
from __future__ import annotations

from . import winmove

# A drag has to have gone somewhere before it is acted on at all: a window
# nudged while already hanging over an edge is not a gesture.
START_INSIDE = 0.2


class Drags:
    """What to do about windows being dragged on this computer.

    `doorways()` answers the borders of this computer's own screens that
    lead to a computer a window could be thrown at, each as
    {"side", "edge", "device"} - the node's, over the control API, because
    only it has the arrangement and knows who is connected and willing. The
    border is a LINE in local pixels (desk.Door.edge), not the far side of
    everything, so a computer with two monitors measures against the border
    that actually leads somewhere.

    `ask(hwnd, device)` sends the window, and answers "" or why not.
    `end_drag(hwnd)` takes the window out of the window manager's move loop,
    so it is not left hanging half off the screen; `log` says what happened.

    One drag at a time, which is all a mouse can do.
    """

    def __init__(self, doorways, ask, end_drag, log=None):
        self.doorways = doorways
        self.ask = ask
        self.end_drag = end_drag
        self.log = log or (lambda *_: None)
        self.hwnd = None              # the window being dragged, if any
        self.sent = False             # already on its way: do nothing more
        self._began_inside = False

    # ------------------------------------------------------------ the drag
    def began(self, hwnd) -> bool:
        """A window started being dragged. True if it is worth watching."""
        self.hwnd = None
        self.sent = False
        self._began_inside = False
        try:
            if not hwnd or not self.doorways():
                return False
        except Exception as e:
            self.log(f"could not tell which borders lead anywhere: {e!r}")
            return False
        self.hwnd = hwnd
        return True

    def moved(self, place) -> str:
        """The window is now at `place` - (x, y, w, h) on this screen.

        Returns the computer it was sent to, or "" if it stays. Called as
        often as the system cares to say; it is cheap and does nothing until
        the window is far enough over.
        """
        if self.hwnd is None or self.sent or not place:
            return ""
        try:
            doors = list(self.doorways() or ())
        except Exception:
            return ""
        if not doors:
            return ""
        # Where it started matters: a window already hanging over a border
        # when it was picked up would otherwise go the instant it was
        # touched, which is not a gesture anyone made.
        if not self._began_inside:
            how_far = max((winmove.past_line(d.get("side"), place,
                                             d.get("edge")) for d in doors),
                          default=1.0)
            if how_far <= START_INSIDE:
                self._began_inside = True
            return ""
        for door in doors:
            if winmove.far_enough(door.get("side"), place, door.get("edge")):
                return self._across(str(door.get("side")),
                                    str(door.get("device")))
        return ""

    def ended(self) -> None:
        """The drag is over, however it ended."""
        self.hwnd = None
        self.sent = False
        self._began_inside = False

    # ----------------------------------------------------------- sending it
    def _across(self, side: str, device: str) -> str:
        hwnd = self.hwnd
        self.sent = True              # whatever happens, not twice
        # Out of the move loop FIRST. Left in it, the window stays wherever
        # the hand is while the picture of it appears on the other computer,
        # and letting go would drop it half off this screen.
        try:
            self.end_drag(hwnd)
        except Exception as e:
            self.log(f"could not end the drag: {e!r}")
        try:
            why = self.ask(hwnd, device)
        except Exception as e:
            self.log(f"that window could not be sent to {device}: {e!r}")
            return ""
        if why:
            self.log(f"dragged towards {device} ({side}), but it cannot go: "
                     f"{why}")
            return ""
        self.log(f"dragged across the {side} edge, so it is going to {device}")
        return device
