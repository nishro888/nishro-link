#!/usr/bin/env bash
# Tests install-linux.sh against a stubbed system, in a throwaway HOME.
# Touches nothing real: no sudo, no /etc, no ~/.local.
#
#   ./link/packaging/test-install-linux.sh
#
# The case that matters is an installer with no terminal to type a password
# into. Calling sudo there blocks forever with no output, which is the worst
# failure an installer can have - indistinguishable from a crash. That happened
# for real, so it gets a test.
set -uo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAB="$(mktemp -d)"
trap 'rm -rf "$LAB"' EXIT
PASS=0; FAIL=0
check() { if [ "$2" = "$3" ]; then echo "  ok   $1"; PASS=$((PASS+1));
          else echo "  FAIL $1 (got '$2', wanted '$3')"; FAIL=$((FAIL+1)); fi; }

mkdir -p "$LAB/bin" "$LAB/home" "$LAB/etc"
cp -r "$SRC" "$LAB/link"

cat > "$LAB/bin/python3" <<'EOF'
#!/usr/bin/env bash
# NL_NO_TK lets a test pretend python3-tk is absent - the case that leaves a
# user with a program that runs and shows nothing.
case "$*" in
  *"import evdev"*)   exit 0 ;;
  *"import tkinter"*) [ -n "${NL_NO_TK:-}" ] && exit 1 || exit 0 ;;
  *)                  exec python "$@" ;;
esac
EOF
cat > "$LAB/bin/sudo" <<'EOF'
#!/usr/bin/env bash
[ "$1" = "-n" ] && exit 1          # never pre-authorised in the lab
read -r _ < /dev/tty               # the call that used to hang
EOF
printf '#!/usr/bin/env bash\nexit 0\n' > "$LAB/bin/systemctl"
chmod +x "$LAB/bin"/*

run() {  # run(label, id-groups, lsmod-output) -> stdout in $OUT, status in $ST
  printf '#!/usr/bin/env bash\necho "tester %s"\n' "$2" > "$LAB/bin/id"
  printf '#!/usr/bin/env bash\necho "%s"\n' "$3" > "$LAB/bin/lsmod"
  chmod +x "$LAB/bin/id" "$LAB/bin/lsmod"
  rm -rf "$LAB/home"; mkdir -p "$LAB/home"
  OUT="$(cd "$LAB" && PATH="$LAB/bin:$PATH" HOME="$LAB/home" USER=tester \
        NL_UDEV_RULE="$LAB/etc/rule" NL_UINPUT_DEV="$LAB/etc/uinput" \
        NL_NO_TK="${NL_NO_TK:-}" \
        timeout 60 ./link/packaging/install-linux.sh --no-setup < /dev/null 2>&1)"
  ST=$?
}

echo "Nothing configured yet, and no terminal to ask for a password:"
run "unconfigured" "wheel" ""
check "does not hang or abort"        "$ST" "0"
check "says why it skipped root"      "$(grep -qc 'cannot ask for a password' <<<"$OUT" && echo y)" "y"
check "prints the manual commands"    "$(grep -qc 'usermod -aG input' <<<"$OUT" && echo y)" "y"
check "warns it will not capture yet" "$(grep -qc 'fail to read the keyboard' <<<"$OUT" && echo y)" "y"
check "still installs the launcher"   "$(test -x "$LAB/home/.local/bin/nishro-link" && echo y)" "y"
check "still installs the code"       "$(test -f "$LAB/home/.local/share/nishro-link/link/node.py" && echo y)" "y"
check "strips the tests"              "$(test -d "$LAB/home/.local/share/nishro-link/link/tests" || echo y)" "y"
check "strips packaging"              "$(test -d "$LAB/home/.local/share/nishro-link/link/packaging" || echo y)" "y"
check "installs the service unit"     "$(test -f "$LAB/home/.config/systemd/user/nishro-link.service" && echo y)" "y"

echo
echo "Already set up - group joined, rule present, uinput node exists:"
touch "$LAB/etc/rule" "$LAB/etc/uinput"
run "configured" "input" ""
check "does not hang"                  "$ST" "0"
check "asks for no root at all"        "$(grep -qc 'already in place' <<<"$OUT" && echo y)" "y"
check "does not re-add the group"      "$(grep -qc 'usermod' <<<"$OUT" || echo y)" "y"
# uinput compiled into the kernel shows in no lsmod output, so a lsmod-based
# check demanded sudo on a machine that was already working. Found on real
# hardware, by the Ubuntu box.
check "does not ask to load uinput"    "$(grep -qc 'modprobe' <<<"$OUT" || echo y)" "y"

echo
echo "Without python3-tk, which is a separate package on Debian:"
NL_NO_TK=1 run "no-tk" "input" ""
check "does not hang"                  "$ST" "0"
check "says the window will not open"  "$(grep -qc 'without its window' <<<"$OUT" && echo y)" "y"
check "names the package to install"   "$(grep -qc 'python3-tk' <<<"$OUT" && echo y)" "y"
check "still installs"                 "$(test -x "$LAB/home/.local/bin/nishro-link" && echo y)" "y"

echo
echo "The launcher it generates:"
LAUNCH="$LAB/home/.local/bin/nishro-link"
check "sets PYTHONPATH"  "$(grep -qc 'PYTHONPATH' "$LAUNCH" && echo y)" "y"
check "execs the module" "$(grep -qc 'link.nishro_link' "$LAUNCH" && echo y)" "y"
check "passes arguments" "$(grep -qc '"\$@"' "$LAUNCH" && echo y)" "y"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
