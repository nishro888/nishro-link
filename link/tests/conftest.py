"""Fixtures shared across test modules.

ONE Tcl interpreter for the whole run. Each UI test module used to start its own,
and on Windows starting Tk interpreters one after another fails every so often
with "couldn't read file init.tcl: No error". It showed up first inside
test_ui_tk, about one run in four; fixed there per module, it moved to
test_ui_pair. A flaky suite teaches people to rerun failures instead of reading
them, so there is exactly one interpreter now and each module gets a Toplevel.

Requested only by the UI modules, which skip themselves where there is no
display - so a headless run never creates it.
"""
import pytest


@pytest.fixture(scope="session")
def tk_session():
    import tkinter as tk
    r = tk.Tk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture(autouse=True)
def no_broadcasts(monkeypatch):
    """Nothing in the suite may search the real network. The laptop and the AIO
    are on it, and a test that finds them depends on what is switched on - and
    tells them things. Direct questions to loopback still work."""
    from link import discovery
    monkeypatch.setattr(discovery, "NETWORK", False)
