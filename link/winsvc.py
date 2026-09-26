"""Windows plumbing for the service: its entry point, the agent it starts in
the console session, and which desktop is showing.

All through ctypes, the same calls Input Leap and Mouse Without Borders make:

  StartServiceCtrlDispatcherW   run as a real service, stopped cleanly by the
                                Service Control Manager
  WTSGetActiveConsoleSessionId  the session in front of the screen
  DuplicateTokenEx +            our own SYSTEM token, moved into that session
  SetTokenInformation           (needs SE_TCB_NAME, which LocalSystem holds)
  CreateProcessAsUserW          the agent, on "winsta0\\<desktop>" - Default,
                                or Winlogon for the lock and login screens
  OpenInputDesktop              the desktop that is showing now
"""
from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes

SERVICE_NAME = "NishroLink"

if sys.platform == "win32":
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)

# ------------------------------------------------------------------ service
SERVICE_WIN32_OWN_PROCESS = 0x10
SERVICE_STOPPED, SERVICE_START_PENDING, SERVICE_STOP_PENDING, SERVICE_RUNNING = 1, 2, 3, 4
SERVICE_ACCEPT_STOP, SERVICE_ACCEPT_SHUTDOWN = 0x1, 0x4
SERVICE_CONTROL_STOP, SERVICE_CONTROL_INTERROGATE, SERVICE_CONTROL_SHUTDOWN = 1, 4, 5
ERROR_FAILED_SERVICE_CONTROLLER_CONNECT = 1063
ERROR_CALL_NOT_IMPLEMENTED = 120


class SERVICE_STATUS(ctypes.Structure):
    _fields_ = [("dwServiceType", wintypes.DWORD),
                ("dwCurrentState", wintypes.DWORD),
                ("dwControlsAccepted", wintypes.DWORD),
                ("dwWin32ExitCode", wintypes.DWORD),
                ("dwServiceSpecificExitCode", wintypes.DWORD),
                ("dwCheckPoint", wintypes.DWORD),
                ("dwWaitHint", wintypes.DWORD)]


if sys.platform == "win32":
    _MAIN = ctypes.WINFUNCTYPE(None, wintypes.DWORD,
                               ctypes.POINTER(wintypes.LPWSTR))
    _HANDLER = ctypes.WINFUNCTYPE(wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.LPVOID)

    class SERVICE_TABLE_ENTRYW(ctypes.Structure):
        _fields_ = [("lpServiceName", wintypes.LPWSTR), ("lpServiceProc", _MAIN)]


def run_as_service(body, name: str = SERVICE_NAME) -> bool:
    """Hand this process to the Service Control Manager, which calls `body(stop)`
    on its own thread; `stop` is set when Windows asks the service to stop.
    False if this process was not started as a service (started by hand)."""
    stop = threading.Event()
    keep = {}                                  # callbacks must outlive the call

    def report(state, wait_ms=0):
        st = SERVICE_STATUS(SERVICE_WIN32_OWN_PROCESS, state,
                            0 if state == SERVICE_START_PENDING else
                            SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN,
                            0, 0, keep.setdefault("check", 0) + 1, wait_ms)
        keep["check"] += 1
        advapi32.SetServiceStatus(keep["handle"], ctypes.byref(st))

    def handler(control, _type, _data, _ctx):
        if control in (SERVICE_CONTROL_STOP, SERVICE_CONTROL_SHUTDOWN):
            report(SERVICE_STOP_PENDING, 5000)
            stop.set()
            return 0
        if control == SERVICE_CONTROL_INTERROGATE:
            return 0
        return ERROR_CALL_NOT_IMPLEMENTED

    def service_main(_argc, _argv):
        keep["handler"] = _HANDLER(handler)
        advapi32.RegisterServiceCtrlHandlerExW.restype = wintypes.HANDLE
        keep["handle"] = advapi32.RegisterServiceCtrlHandlerExW(
            name, keep["handler"], None)
        report(SERVICE_START_PENDING, 10000)
        report(SERVICE_RUNNING)
        try:
            body(stop)
        finally:
            report(SERVICE_STOPPED)

    keep["main"] = _MAIN(service_main)
    table = (SERVICE_TABLE_ENTRYW * 2)(SERVICE_TABLE_ENTRYW(name, keep["main"]),
                                        SERVICE_TABLE_ENTRYW(None, _MAIN()))
    if advapi32.StartServiceCtrlDispatcherW(table):
        return True
    if ctypes.get_last_error() == ERROR_FAILED_SERVICE_CONTROLLER_CONNECT:
        return False
    raise ctypes.WinError(ctypes.get_last_error())


# ------------------------------------------------------------------ desktops
UOI_NAME = 2
DESKTOP_READOBJECTS = 0x0001
DESKTOP_SWITCHDESKTOP = 0x0100


def _name(handle) -> str:
    buf = ctypes.create_unicode_buffer(256)
    need = wintypes.DWORD()
    if not user32.GetUserObjectInformationW(handle, UOI_NAME, buf,
                                            ctypes.sizeof(buf), ctypes.byref(need)):
        return ""
    return buf.value


def own_desktop() -> str:
    """The desktop this process was started on."""
    user32.GetThreadDesktop.restype = wintypes.HANDLE
    return _name(user32.GetThreadDesktop(kernel32.GetCurrentThreadId())) or "Default"


def showing_desktop() -> str:
    """The desktop receiving input now: Default, Winlogon (lock, login, UAC),
    Screen-saver. "" if it cannot be opened."""
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    h = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
    if not h:
        return ""
    try:
        return _name(h)
    finally:
        user32.CloseDesktop(h)


# ----------------------------------------------------------- the agent
TOKEN_ALL_ACCESS = 0xF01FF
MAXIMUM_ALLOWED = 0x02000000
SecurityImpersonation, TokenPrimary, TokenSessionId = 2, 1, 12
CREATE_NO_WINDOW, CREATE_UNICODE_ENVIRONMENT = 0x08000000, 0x00000400
STARTF_USESHOWWINDOW, SW_HIDE = 0x1, 0
WAIT_TIMEOUT = 0x102
NO_SESSION = 0xFFFFFFFF


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
                ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


class Proc:
    def __init__(self, handle, pid):
        self.handle, self.pid = handle, pid

    def alive(self) -> bool:
        return kernel32.WaitForSingleObject(self.handle, 0) == WAIT_TIMEOUT

    def kill(self) -> None:
        if self.handle:
            kernel32.TerminateProcess(self.handle, 1)
            kernel32.CloseHandle(self.handle)
            self.handle = None


def console_session() -> int:
    return kernel32.WTSGetActiveConsoleSessionId()


def launch_in_console(cmdline: str, desktop: str = "Default") -> Proc:
    """Start `cmdline` as SYSTEM in the console session, on winsta0\\desktop."""
    session = console_session()
    if session == NO_SESSION:
        raise OSError("no console session yet")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    tok, dup = wintypes.HANDLE(), wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_ALL_ACCESS,
                                     ctypes.byref(tok)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not advapi32.DuplicateTokenEx(tok, MAXIMUM_ALLOWED, None,
                                         SecurityImpersonation, TokenPrimary,
                                         ctypes.byref(dup)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = wintypes.DWORD(session)
        if not advapi32.SetTokenInformation(dup, TokenSessionId, ctypes.byref(sid),
                                            ctypes.sizeof(sid)):
            raise ctypes.WinError(ctypes.get_last_error())
        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(si)
        si.lpDesktop = f"winsta0\\{desktop}"
        si.dwFlags = STARTF_USESHOWWINDOW
        si.wShowWindow = SW_HIDE
        pi = PROCESS_INFORMATION()
        buf = ctypes.create_unicode_buffer(cmdline)
        if not advapi32.CreateProcessAsUserW(dup, None, buf, None, None, False,
                                             CREATE_NO_WINDOW, None, None,
                                             ctypes.byref(si), ctypes.byref(pi)):
            raise ctypes.WinError(ctypes.get_last_error())
        kernel32.CloseHandle(pi.hThread)
        return Proc(pi.hProcess, pi.dwProcessId)
    finally:
        for h in (tok, dup):
            if h:
                kernel32.CloseHandle(h)
