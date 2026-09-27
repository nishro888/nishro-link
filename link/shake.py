"""Shaking the mouse to find the pointer - as PowerToys' Find My Mouse does.

A pointer lost among several screens is found by shaking the mouse: quick
back-and-forth movement that goes a long way and gets nowhere. This decides
when movement is that, and nothing else - no screens, no drawing, no clock of
its own - so every threshold here is provable in a test.

THE RULE, over the last WINDOW_MS of movement:
  - it went at least MIN_PATH pixels, all told;
  - it doubled back sharply at least MIN_REVERSALS times - the direction
    swinging round by more than 120 degrees from one moment to the next. A
    shake flips straight back; a circle turns gradually (even very fast, a
    few tens of degrees per moment), and a drag hardly turns at all. Any
    direction: along, up and down, or diagonally;
  - it went at least RATIO times further than the size of the patch it
    stayed in - a zig-zag across the screen doubles back too, but gets
    somewhere.
Then nothing more until COOLDOWN_MS has passed, so one shake is one spotlight.

Movement is gathered into BUCKET_MS slices first, and judged once per slice:
a mouse can report a thousand times a second, and the judging - a few dozen
slices - must cost nothing on the path every mouse event takes.
"""
from __future__ import annotations

import math
from collections import deque

BUCKET_MS = 25
WINDOW_MS = 800
MIN_PATH = 800           # pixels: a moderate shake, about 1000 px a second
MIN_REVERSALS = 4
RATIO = 3.0
MIN_LEG = 6              # pixels a slice must move to have a direction
COOLDOWN_MS = 1500


class Shake:
    def __init__(self):
        self._slices = deque()        # (end ms, dx, dy) of closed slices
        self._start = None            # when the open slice began
        self._dx = self._dy = 0
        self._quiet_until = -1

    def feed(self, dx: int, dy: int, now: int) -> bool:
        """One mouse movement at `now` ms. True when that completes a shake."""
        shook = False
        if self._start is None:
            self._start = now
        elif now - self._start >= BUCKET_MS:
            shook = self._close(now)
            self._start, self._dx, self._dy = now, 0, 0
        self._dx += int(dx)
        self._dy += int(dy)
        return shook

    def _close(self, now: int) -> bool:
        s = self._slices
        # Dated when it ended, not when the next movement came: after a rest
        # that is long ago, and it must fall out of the window.
        s.append((min(now, self._start + BUCKET_MS), self._dx, self._dy))
        while s and now - s[0][0] > WINDOW_MS:
            s.popleft()
        if now < self._quiet_until or len(s) < 4:
            return False
        path = 0.0
        x = y = x0 = x1 = y0 = y1 = 0
        reversals = 0
        last = None                     # the last slice that had a direction
        for _, dx, dy in s:
            step = math.hypot(dx, dy)
            path += step
            x += dx
            y += dy
            x0, x1 = min(x0, x), max(x1, x)
            y0, y1 = min(y0, y), max(y1, y)
            if step < MIN_LEG:
                continue
            if last is not None:
                lx, ly, lstep = last
                # cos(angle) < -1/2: swung round by more than 120 degrees
                if (dx * lx + dy * ly) < -0.5 * step * lstep:
                    reversals += 1
            last = (dx, dy, step)
        span = math.hypot(x1 - x0, y1 - y0) + 1
        if path >= MIN_PATH and reversals >= MIN_REVERSALS and path >= RATIO * span:
            self._quiet_until = now + COOLDOWN_MS
            s.clear()
            return True
        return False
