"""When Nishro Link should type Bangla for another computer's keyboard.

Only where Avro Keyboard is running, and the setting has not been switched
off. Nobody who does not use Avro should lose F12 to it or meet Bangla, and
someone who does already lives with F12 being Avro's: from the other
keyboard it now does the same thing.

Whether Avro is running is looked up on a thread of its own every few
seconds. The answer is READ on the input path, never worked out there: the
key path runs with the node's lock held, and a process list - however quick
- has no business between a key arriving and it being played.
"""
from __future__ import annotations

import sys
import threading

AVRO = "avro keyboard.exe"
EVERY_S = 3.0


def running_programs() -> set:
    """The names of the programs running on this computer, lower-cased.

    Windows only - Toolhelp32, which the service (SYSTEM, session 0) can use
    to see every session's processes - and an empty set anywhere else.
    """
    if sys.platform != "win32":
        return set()
    import ctypes
    import ctypes.wintypes as wt

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                    ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                    ("th32ParentProcessID", wt.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    k32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.CloseHandle.argtypes = [wt.HANDLE]
    TH32CS_SNAPPROCESS = 0x00000002
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == wt.HANDLE(-1).value:
        return set()
    names = set()
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            names.add(entry.szExeFile.lower())
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return names


class Setting:
    """`bangla_phonetic` read from a config file, at most every few seconds.

    For the part of Nishro Link that shows a moved window: it is its own
    process, so it cannot see the service's settings in memory, and must
    follow them anyway - switching Bangla off in Settings has to reach a
    window already moved. The first readable file wins; a file that cannot
    be read counts as the default, which is safe because the gate still
    needs Avro running.
    """

    def __init__(self, paths, key="bangla_phonetic", default=True,
                 every=EVERY_S, clock=None):
        import time
        self.paths = [p for p in paths if p]
        self.key = key
        self.default = default
        self.every = every
        self.clock = clock or time.monotonic
        self._value = default
        self._at = None

    def __call__(self) -> bool:
        now = self.clock()
        if self._at is None or now - self._at >= self.every:
            self._at = now
            self._value = self._read()
        return self._value

    def _read(self) -> bool:
        import json
        for p in self.paths:
            try:
                with open(p, encoding="utf-8") as f:
                    return bool(json.load(f).get(self.key, self.default))
            except (OSError, ValueError):
                continue
        return self.default


class Gate:
    """`gate()` - True while Bangla typing should be offered here.

    `wanted()` is the setting. `programs()` lists what is running; it is
    called on this gate's own thread, every `every` seconds, never on the
    caller's.
    """

    def __init__(self, wanted, programs=running_programs, every=EVERY_S,
                 log=None):
        self.wanted = wanted
        self.programs = programs
        self.every = every
        self.log = log or (lambda *_: None)
        self.avro = False
        self._thread = None
        self._stop = threading.Event()
        self._said = None

    def __call__(self) -> bool:
        if self._thread is None:
            self.start()
        try:
            return bool(self.wanted()) and self.avro
        except Exception:
            return False

    def start(self) -> None:
        if self._thread is not None:
            return
        self.look()                         # the first answer, before any key
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="bangla-gate")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def look(self) -> None:
        try:
            now = AVRO in self.programs()
        except Exception as e:
            self.log(f"could not tell whether Avro Keyboard is running: {e!r}")
            now = False
        if now != self._said:
            self._said = now
            self.log("Avro Keyboard is running: F12 on another computer's "
                     "keyboard switches Bangla here" if now else
                     "Avro Keyboard is not running: keys from other computers "
                     "are played as they are")
        self.avro = now

    def _run(self) -> None:
        while not self._stop.wait(self.every):
            self.look()
