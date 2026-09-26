"""PyInstaller entry point. Kept separate so the package itself stays importable
as `python -m link.nishro_link` for anyone running from source.

The try/except is not decoration. This is built --windowed, so there is no
console: an exception before the window opens would put nothing on screen at all,
and the user would see a program that does not start, for no stated reason.

One failure, one dialog. This used to show its dialog and then re-raise, and the
PyInstaller bootloader showed a SECOND dialog for the same exception. It now
reports, writes the full traceback to the log the dialog points at, and exits 1.

NL_NO_DIALOG=1 (set by the build's smoke test) reports to stderr instead: a
build check must never put dialogs on the desktop of whoever is building. It
did, once, while proving that the check catches a broken build.
"""
import os
import sys
import traceback


def main() -> int:
    from link.nishro_link import main as run
    return run()


def _log_traceback(text: str) -> str:
    """Append to the program's own log. Returns where, for the dialog."""
    try:
        from link.runtime import log_path
        where = str(log_path())
    except Exception:
        where = os.path.join(os.environ.get("LOCALAPPDATA", "."), "NishroLink",
                             "link.log")
    try:
        os.makedirs(os.path.dirname(where), exist_ok=True)
        with open(where, "a", encoding="utf-8") as f:
            f.write("could not start:\n" + text + "\n")
    except OSError:
        pass
    return where


def _report(exc: BaseException) -> None:
    """Last resort: put the failure somewhere a person will actually see it."""
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    where = _log_traceback(text)
    if os.environ.get("NL_NO_DIALOG"):
        # A windowed build has no stderr at all - sys.stderr is None - and
        # writing to it raised, which the bootloader turned into a dialog: the
        # one thing this switch exists to prevent. The log already has it.
        if sys.stderr is not None:
            sys.stderr.write(text)
        return
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(
            "Nishro Link could not start",
            f"{type(exc).__name__}: {exc}"
            f"\n\nThe full error is in the log:\n{where}")
        root.destroy()
    except Exception:
        pass        # no display, no tkinter - the log still has it


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as e:
        _report(e)
        sys.exit(1)
