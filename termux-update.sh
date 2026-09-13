#!/data/data/com.termux/files/usr/bin/sh
set -eu

REPO="https://github.com/LunaxStudios-Officiall/LunarX-HOME.git"
BRANCH="3.1.3"
DISTRO="lunarx"
RELEASE="/opt/lunarx/releases/$BRANCH"
TARGET="/opt/lunarx/home"
PERSISTENT="/opt/lunarx/runtime"
PORT="8787"
SUPERVISOR="$HOME/.local/bin/lunarx-home-supervisor"
PIDFILE="$HOME/.lunarx-home-supervisor.pid"
BOOTFILE="$HOME/.termux/boot/lunarx-server"
LOGFILE="$HOME/lunarx-home-proot.log"

say() { printf '%s\n' "[LunarX] $*"; }
fail() { printf '%s\n' "[LunarX] ERROR: $*" >&2; exit 1; }

[ -n "${PREFIX:-}" ] || fail "Run this command from Termux, not from inside Ubuntu PRoot."
command -v proot-distro >/dev/null 2>&1 || fail "proot-distro is not installed in Termux."
command -v curl >/dev/null 2>&1 || fail "curl is not installed in Termux."

command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock >/dev/null 2>&1 || true

say "Updating LunarX Home to $BRANCH..."

# Stop only the LunarX supervisor process tree from a previous phone deployment.
# This frees :8787 before the installer performs its own activation/rollback check.
kill_tree() {
  parent="$1"
  children="$(cat "/proc/$parent/task/$parent/children" 2>/dev/null || true)"
  for child in $children; do
    kill_tree "$child"
    kill -TERM "$child" 2>/dev/null || true
  done
}
if [ -f "$PIDFILE" ]; then
  old="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "$old" ] && kill -0 "$old" 2>/dev/null; then
    say "Stopping the previous LunarX supervisor..."
    kill_tree "$old"
    kill -TERM "$old" 2>/dev/null || true
    sleep 2
  fi
fi
rm -f "$PIDFILE"

proot-distro login "$DISTRO" -- bash -s <<'PROOT'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export LUNARX_PLATFORM=android-proot
export LUNARX_PERSISTENT_ROOT=/opt/lunarx/runtime
REPO="https://github.com/LunaxStudios-Officiall/LunarX-HOME.git"
BRANCH="3.1.3"
RELEASE="/opt/lunarx/releases/$BRANCH"
TARGET="/opt/lunarx/home"

mkdir -p /opt/lunarx/releases
if ! command -v git >/dev/null 2>&1; then
  apt-get update
  apt-get install -y --no-install-recommends git ca-certificates
fi
rm -rf "$RELEASE"
git clone --depth 1 --branch "$BRANCH" "$REPO" "$RELEASE"
cd "$RELEASE"
chmod 755 install.sh bootstrap.sh lunarxctl termux-update.sh 2>/dev/null || true

COMMAND=install
[ -d "$TARGET" ] && COMMAND=update
./install.sh "$COMMAND" --target "$TARGET"

# Fail here if the advertised PRoot Desktop stack is incomplete.
command -v startxfce4 >/dev/null 2>&1 || { echo "[LunarX] ERROR: startxfce4 is missing" >&2; exit 1; }
(command -v tigervncserver >/dev/null 2>&1 || command -v vncserver >/dev/null 2>&1) || { echo "[LunarX] ERROR: TigerVNC server is missing" >&2; exit 1; }
(command -v tigervncpasswd >/dev/null 2>&1 || command -v vncpasswd >/dev/null 2>&1) || { echo "[LunarX] ERROR: TigerVNC password tool is missing" >&2; exit 1; }
command -v websockify >/dev/null 2>&1 || { echo "[LunarX] ERROR: websockify is missing" >&2; exit 1; }
[ -f /usr/share/novnc/vnc.html ] || [ -f /usr/share/noVNC/vnc.html ] || { echo "[LunarX] ERROR: noVNC web client is missing" >&2; exit 1; }

# Stop the installer-spawned portable child; the Termux host supervisor below
# owns the durable PRoot lifetime after this command returns.
python3 "$TARGET/lunarxctl" stop >/dev/null 2>&1 || true
PROOT

mkdir -p "$HOME/.local/bin" "$HOME/.termux/boot"
cat > "$SUPERVISOR" <<'SUP'
#!/data/data/com.termux/files/usr/bin/sh
LOG="$HOME/lunarx-home-proot.log"
child=""
stop_child() {
  [ -n "$child" ] && kill -TERM "$child" 2>/dev/null || true
  [ -n "$child" ] && wait "$child" 2>/dev/null || true
  exit 0
}
trap stop_child TERM INT HUP
while true; do
  printf '%s - starting LunarX Home PRoot\n' "$(date)" >> "$LOG"
  proot-distro login lunarx -- bash -lc '
    export LUNARX_PLATFORM=android-proot
    export LUNARX_PERSISTENT_ROOT=/opt/lunarx/runtime
    export LUNARX_APP_ROOT=/opt/lunarx/home
    export LUNARX_CONFIG_ROOT=/opt/lunarx/runtime/config
    export LUNARX_DATA_ROOT=/opt/lunarx/runtime/data
    export LUNARX_STATE_ROOT=/opt/lunarx/runtime/state
    export LUNARX_RUNTIME_ROOT=/opt/lunarx/runtime/runtime
    export LUNARX_BACKUP_ROOT=/opt/lunarx/runtime/backups
    export LUNARX_BROKER_MODE=direct
    cd /opt/lunarx/home
    exec python3 app.py
  ' >> "$LOG" 2>&1 &
  child=$!
  wait "$child"
  rc=$?
  child=""
  printf '%s - LunarX exited with code %s; retrying in 5 seconds\n' "$(date)" "$rc" >> "$LOG"
  sleep 5
done
SUP
chmod 700 "$SUPERVISOR"

cat > "$BOOTFILE" <<'BOOT'
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock >/dev/null 2>&1 || true
sleep 15
command -v sshd >/dev/null 2>&1 && sshd >/dev/null 2>&1 || true
PIDFILE="$HOME/.lunarx-home-supervisor.pid"
SUPERVISOR="$HOME/.local/bin/lunarx-home-supervisor"
RUNNING=0
if [ -f "$PIDFILE" ]; then
  PID="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then RUNNING=1; fi
fi
if [ "$RUNNING" -ne 1 ]; then
  nohup "$SUPERVISOR" >/dev/null 2>&1 &
  echo $! > "$PIDFILE"
fi
BOOT
chmod 700 "$BOOTFILE"

rm -f "$PIDFILE"
nohup "$SUPERVISOR" >/dev/null 2>&1 &
echo $! > "$PIDFILE"

say "Waiting for the persistent server..."
health=""
i=0
while [ "$i" -lt 45 ]; do
  health="$(curl -fsS "http://127.0.0.1:$PORT/healthz" 2>/dev/null || true)"
  [ -n "$health" ] && break
  i=$((i + 1))
  sleep 1
done
[ -n "$health" ] || { tail -80 "$LOGFILE" 2>/dev/null || true; fail "LunarX did not become healthy."; }
printf '%s\n' "$health" | grep -q '"version":"3.1.3"' || fail "Unexpected health version: $health"

say "LunarX Home 3.1.3 is online."
say "Open the Android host LAN/Tailscale address on port $PORT."
say "Boot persistence and SSH startup are configured in $BOOTFILE."
printf '%s\n' "$health"
