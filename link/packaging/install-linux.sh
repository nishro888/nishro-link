#!/usr/bin/env bash
# Nishro Link - Linux installer.
#
#   ./install-linux.sh              install, then ask the setup questions
#   ./install-linux.sh --no-setup   install only
#
# Installs for the current user. The only thing needing root is device
# permission, and it is asked for explicitly with an explanation.
set -euo pipefail

PREFIX="${HOME}/.local/share/nishro-link"
BIN="${HOME}/.local/bin"
UNIT="${HOME}/.config/systemd/user"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # the link/ directory

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  ok   %s\n' "$*"; }
warn() { printf '  ..   %s\n' "$*"; }
die()  { printf '\n\033[31mstopped: %s\033[0m\n' "$*" >&2; exit 1; }

say "Nishro Link - installing for ${USER}"

# ---------------------------------------------------------------- python
command -v python3 >/dev/null || die "python3 is not installed"
python3 - <<'EOF' || die "Python 3.8 or newer is required"
import sys
raise SystemExit(0 if sys.version_info >= (3, 8) else 1)
EOF
ok "python3 $(python3 -c 'import sys; print("%d.%d"%sys.version_info[:2])')"

# tkinter is a separate package on Debian/Ubuntu and is NOT pulled in by pip.
# Without it the program still runs, but with no window at all - and the only
# clue is one line in a log the user has no window to read.
if ! python3 -c "import tkinter" 2>/dev/null; then
  warn "python3-tk is missing - the app will run without its window"
  if [ -t 0 ] && command -v apt-get >/dev/null; then
    say "Install it? The program works headless without it, but there will be no"
    say "window - only the web page it prints at startup."
    printf '  install python3-tk now? [Y/n]: '
    read -r reply
    case "${reply:-y}" in
      [Nn]*) warn "skipped - run with --no-window, or use the printed URL" ;;
      *) sudo apt-get install -y python3-tk && ok "python3-tk" ;;
    esac
  else
    warn "install it with: sudo apt install python3-tk"
  fi
else
  ok "tkinter (the window will open)"
fi

if ! python3 -c "import evdev" 2>/dev/null; then
  warn "installing python-evdev"
  python3 -m pip install --user --quiet evdev \
    || die "could not install evdev. Try: sudo apt install python3-evdev"
fi
ok "evdev"

# The link's encryption (secure.py): X25519 and ChaCha20-Poly1305.
if ! python3 -c "import cryptography.hazmat.primitives.ciphers.aead" 2>/dev/null; then
  warn "installing python cryptography"
  python3 -m pip install --user --quiet "cryptography>=3.4" \
    || die "could not install cryptography. Try: sudo apt install python3-cryptography"
fi
ok "cryptography"

# ------------------------------------------------------------ permissions
# Two different things, and they fail in different ways if you skip one:
#   reading /dev/input/*  -> capture. Needs the 'input' group.
#   writing /dev/uinput   -> injection. Needs a udev rule, or it is root-only.
# Overridable only so the test harness can point them somewhere writable.
# Nothing else should ever set these.
RULE="${NL_UDEV_RULE:-/etc/udev/rules.d/99-nishro-link.rules}"
UINPUT="${NL_UINPUT_DEV:-/dev/uinput}"
NEED_RELOGIN=0
ROOT_SKIPPED=0
WANT_GROUP=0; WANT_RULE=0; WANT_MODULE=0

id -nG "$USER" | grep -qw input        || WANT_GROUP=1
[ -f "$RULE" ]                         || WANT_RULE=1
# uinput is often compiled INTO the kernel rather than built as a module,
# in which case it never appears in lsmod even though it works perfectly.
# Asking whether the device node exists is the actual question.
[ -e "$UINPUT" ]                       || WANT_MODULE=1

root_work() {
  [ "$WANT_GROUP" = 1 ] && { sudo usermod -aG input "$USER"; NEED_RELOGIN=1; }
  if [ "$WANT_RULE" = 1 ]; then
    echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' \
      | sudo tee "$RULE" >/dev/null
    sudo udevadm control --reload-rules
    sudo udevadm trigger --subsystem-match=misc --sysname-match=uinput || true
  fi
  if [ "$WANT_MODULE" = 1 ]; then
    sudo modprobe uinput || warn "could not load the uinput module now"
    echo uinput | sudo tee /etc/modules-load.d/uinput.conf >/dev/null
  fi
}

if [ $((WANT_GROUP + WANT_RULE + WANT_MODULE)) -eq 0 ]; then
  ok "device permissions already in place"
elif sudo -n true 2>/dev/null; then
  warn "applying device permissions (sudo already authorised)"
  root_work
  ok "device permissions"
elif [ -t 0 ]; then
  say "These steps need root - you will be asked for your password"
  [ "$WANT_GROUP" = 1 ]  && echo "  - add ${USER} to the 'input' group (to read the keyboard)"
  [ "$WANT_RULE" = 1 ]   && echo "  - a udev rule so /dev/uinput works without root"
  [ "$WANT_MODULE" = 1 ] && echo "  - load the uinput module, and load it at boot"
  root_work
  ok "device permissions"
else
  # No terminal to type a password into. Asking anyway would hang forever with
  # no output, which is exactly what an unattended install must not do.
  ROOT_SKIPPED=1
  warn "cannot ask for a password (no terminal) - skipping the root steps"
fi

# ---------------------------------------------------------------- install
say "Installing"
mkdir -p "$PREFIX" "$BIN" "$UNIT"
rm -rf "${PREFIX}/link"
cp -r "$SRC" "${PREFIX}/link"
rm -rf "${PREFIX}/link/tests" "${PREFIX}/link/__pycache__" "${PREFIX}/link/packaging"
ok "code -> ${PREFIX}/link"

cat > "${BIN}/nishro-link" <<EOF
#!/usr/bin/env bash
exec python3 -m link.nishro_link "\$@"
EOF
chmod +x "${BIN}/nishro-link"
# PYTHONPATH rather than a package install: no venv to go stale, nothing to
# uninstall but a directory, and the launcher keeps working after an upgrade.
sed -i "2i export PYTHONPATH=\"${PREFIX}:\${PYTHONPATH:-}\"" "${BIN}/nishro-link"
ok "launcher -> ${BIN}/nishro-link"

cat > "${UNIT}/nishro-link.service" <<EOF
[Unit]
Description=Nishro Link - shared mouse and keyboard
After=graphical-session.target

[Service]
Type=simple
Environment=PYTHONPATH=${PREFIX}
ExecStart=/usr/bin/env python3 -m link.nishro_link
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload 2>/dev/null || true
ok "service unit (not enabled - see below)"

case ":${PATH}:" in
  *":${BIN}:"*) ok "${BIN} is on PATH" ;;
  *) warn "${BIN} is not on PATH - add it to your shell profile" ;;
esac

# ------------------------------------------------------------------ setup
if [ "${1:-}" != "--no-setup" ]; then
  say "Setup"
  PYTHONPATH="$PREFIX" python3 -m link.nishro_link --setup || true
fi

say "Done"
cat <<EOF
  Run it            nishro-link
  Start at login    systemctl --user enable --now nishro-link
  Stop              systemctl --user stop nishro-link
  Logs              journalctl --user -u nishro-link -f
  Uninstall         rm -rf ${PREFIX} ${BIN}/nishro-link ${UNIT}/nishro-link.service

  Failsafe: press BOTH Ctrl keys together to release all input, on either machine.
EOF

# UDP 8770 dropped hides this machine from the search by name; TCP 8770 dropped
# stops the link itself. ufw is off by default on Ubuntu desktops, so only say
# something when it is actually running - an instruction that does not apply
# makes people doubt the ones that do.
if command -v ufw >/dev/null 2>&1 && systemctl is-active --quiet ufw 2>/dev/null; then
  printf '\n\033[33m  A firewall (ufw) is running.\033[0m So the other machine can find and reach this one:\n'
  printf '    sudo ufw allow 8770\n'
fi

if [ "$ROOT_SKIPPED" = "1" ]; then
  printf '\n\033[33m  DEVICE PERMISSIONS WERE NOT SET.\033[0m\n'
  printf '  Run this once, in a terminal, then re-run this installer:\n\n'
  # Only what is actually missing. Telling someone to add themselves to a group
  # they are already in makes them doubt the rest of the list.
  [ "$WANT_GROUP" = 1 ] && printf "    sudo usermod -aG input %s\n" "$USER"
  if [ "$WANT_RULE" = 1 ]; then
    printf "    echo 'KERNEL==\"uinput\", GROUP=\"input\", MODE=\"0660\", OPTIONS+=\"static_node=uinput\"' | sudo tee %s\n" "$RULE"
    printf "    sudo udevadm control --reload-rules\n"
  fi
  if [ "$WANT_MODULE" = 1 ]; then
    printf "    sudo modprobe uinput\n"
    printf "    echo uinput | sudo tee /etc/modules-load.d/uinput.conf\n"
  fi
  printf '\n'
  [ "$WANT_GROUP" = 1 ] && printf '  Until then it will start but fail to read the keyboard.\n'
  [ "$WANT_GROUP" = 0 ] && printf '  Until then it will start but fail to move the pointer.\n'
  printf '\n'
fi

if [ "$NEED_RELOGIN" = "1" ]; then
  printf '\n\033[33m  LOG OUT AND BACK IN before first use - the new group membership\n'
  printf '  does not apply to this session, and capture will fail without it.\033[0m\n\n'
fi
