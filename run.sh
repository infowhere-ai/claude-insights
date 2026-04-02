#!/usr/bin/env bash
# claude-monitor — start/stop/restart/status/help
# Usage: ./run.sh <command> [--app]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.claude-monitor.pid"
LOG_FILE="$SCRIPT_DIR/.claude-monitor.log"
VENV="$SCRIPT_DIR/.venv"
PORT="${PORT:-19001}"
URL="http://localhost:$PORT"
PROJECTS_ROOT="${PROJECTS_ROOT:-"$(dirname "$SCRIPT_DIR")"}"

green()  { echo -e "\033[0;32m$*\033[0m"; }
yellow() { echo -e "\033[0;33m$*\033[0m"; }
red()    { echo -e "\033[0;31m$*\033[0m"; }
info()   { echo -e "\033[0;36m→\033[0m $*"; }

# ── Ensure venv ──────────────────────────────────────────────────────────────
ensure_venv() {
    if [ ! -f "$VENV/bin/uvicorn" ]; then
        info "Creating venv and installing dependencies..."
        python3 -m venv "$VENV"
        "$VENV/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"
        green "✓ Dependencies installed"
    fi
}

# ── Open browser or app window ────────────────────────────────────────────────
open_ui() {
    local mode="${1:-browser}"
    if [ "$mode" = "app" ]; then
        local chrome="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        local edge="/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
        if [ -x "$chrome" ]; then
            "$chrome" --app="$URL" 2>/dev/null &
            green "✓ Opened as standalone app (Chrome)"
        elif [ -x "$edge" ]; then
            "$edge" --app="$URL" 2>/dev/null &
            green "✓ Opened as standalone app (Edge)"
        else
            yellow "Chrome/Edge not found — opening in default browser"
            open "$URL" 2>/dev/null || true
        fi
    else
        open "$URL" 2>/dev/null || true
    fi
}

# ── Check hooks ──────────────────────────────────────────────────────────────
# Strict validation: exactly one monitor-hook.sh entry per event, no duplicates,
# no rogue raw-bash hooks. Prints a clear error for every problem found.
check_hooks() {
    python3 - <<'PYEOF'
import json, os, sys

SETTINGS = os.path.expanduser("~/.claude/settings.json")
HOOK_SCRIPT = os.path.expanduser("~/.claude/hooks/monitor-hook.sh")
REQUIRED_EVENTS = ["PreToolUse", "PostToolUse", "Notification", "Stop"]

errors = []

if not os.path.exists(SETTINGS):
    print("ERROR: ~/.claude/settings.json not found — run ./install.sh first", file=sys.stderr)
    sys.exit(1)

try:
    settings = json.load(open(SETTINGS))
except Exception as e:
    print(f"ERROR: Cannot parse ~/.claude/settings.json: {e}", file=sys.stderr)
    sys.exit(1)

hooks_cfg = settings.get("hooks", {})

for event in REQUIRED_EVENTS:
    entries = hooks_cfg.get(event, [])
    # Collect all commands across all entries for this event
    all_cmds = [
        h.get("command", "")
        for entry in entries
        for h in entry.get("hooks", [])
        if h.get("type") == "command"
    ]

    monitor_cmds = [c for c in all_cmds if "monitor-hook.sh" in c]
    rogue_cmds   = [c for c in all_cmds if "monitor-hook.sh" not in c]

    if not monitor_cmds:
        errors.append(f"  MISSING  {event}: monitor-hook.sh not found — run ./install.sh")
    if len(monitor_cmds) > 1:
        errors.append(f"  DUPLICATE {event}: {len(monitor_cmds)} monitor-hook.sh entries found (should be 1)")
    if rogue_cmds:
        for cmd in rogue_cmds:
            short = cmd[:80] + "..." if len(cmd) > 80 else cmd
            errors.append(f"  ROGUE    {event}: unexpected hook command: {short}")

if not os.path.exists(HOOK_SCRIPT):
    errors.append(f"  MISSING  hook script: {HOOK_SCRIPT} — run ./install.sh")

if errors:
    print("", file=sys.stderr)
    print("Hook configuration problems detected:", file=sys.stderr)
    for e in errors:
        print(e, file=sys.stderr)
    print("", file=sys.stderr)
    print("Fix: edit ~/.claude/settings.json and remove rogue/duplicate entries,", file=sys.stderr)
    print("     or run ./install.sh to reset hooks to the correct state.", file=sys.stderr)
    print("", file=sys.stderr)
    sys.exit(1)

sys.exit(0)
PYEOF
}

# ── Status ───────────────────────────────────────────────────────────────────
cmd_status() {
    if [ -f "$PID_FILE" ]; then
        PID="$(cat "$PID_FILE")"
        if kill -0 "$PID" 2>/dev/null; then
            green "● claude-monitor is running (PID $PID)"
            echo "  URL: $URL"
            echo "  Log: $LOG_FILE"
            return 0
        else
            yellow "○ claude-monitor is not running (stale PID $PID)"
            rm -f "$PID_FILE"
            return 1
        fi
    else
        yellow "○ claude-monitor is not running"
        return 1
    fi
}

# ── Start ────────────────────────────────────────────────────────────────────
cmd_start() {
    local open_mode="${1:-browser}"

    if ! check_hooks; then
        red "Claude Code hooks are not configured."
        echo "  Run ./install.sh first to set up the required hooks."
        exit 1
    fi

    if [ -f "$PID_FILE" ]; then
        PID="$(cat "$PID_FILE")"
        if kill -0 "$PID" 2>/dev/null; then
            yellow "claude-monitor is already running (PID $PID) — $URL"
            open_ui "$open_mode"
            return 0
        fi
        rm -f "$PID_FILE"
    fi

    ensure_venv

    # Kill any process already using the port
    EXISTING="$(lsof -ti tcp:"$PORT" 2>/dev/null || true)"
    if [ -n "$EXISTING" ]; then
        yellow "Port $PORT in use (PID $EXISTING) — killing..."
        kill -9 $EXISTING 2>/dev/null || true
        sleep 0.5
    fi

    info "Starting claude-monitor on port $PORT..."
    "$VENV/bin/uvicorn" app:app \
        --host 0.0.0.0 \
        --port "$PORT" \
        --app-dir "$SCRIPT_DIR" \
        >> "$LOG_FILE" 2>&1 &

    echo $! > "$PID_FILE"
    PID="$(cat "$PID_FILE")"

    # Wait up to 10s for the server to respond
    for i in $(seq 1 20); do
        sleep 0.5
        if curl -sf "$URL/health" > /dev/null 2>&1; then
            green "✓ claude-monitor running (PID $PID)"
            echo "  → $URL"
            open_ui "$open_mode"
            return 0
        fi
    done

    red "✗ Failed to start. Check log: $LOG_FILE"
    tail -20 "$LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
}

# ── Stop ─────────────────────────────────────────────────────────────────────
cmd_stop() {
    if [ ! -f "$PID_FILE" ]; then
        yellow "claude-monitor is not running"
        return 0
    fi

    PID="$(cat "$PID_FILE")"
    if kill -0 "$PID" 2>/dev/null; then
        info "Stopping claude-monitor (PID $PID)..."
        kill "$PID"
        for i in $(seq 1 10); do
            sleep 0.5
            kill -0 "$PID" 2>/dev/null || break
        done
        if kill -0 "$PID" 2>/dev/null; then
            kill -9 "$PID" 2>/dev/null || true
        fi
        green "✓ claude-monitor stopped"
    else
        yellow "Process $PID no longer exists"
    fi
    rm -f "$PID_FILE"
}

# ── Help ──────────────────────────────────────────────────────────────────────
cmd_help() {
    echo ""
    echo "  claude-monitor — real-time Claude Code session dashboard"
    echo ""
    echo "  Usage: $(basename "$0") <command> [options]"
    echo ""
    echo "  Commands:"
    echo "    start [--app]   Start the server and open in the default browser"
    echo "                    --app  open as a standalone app window (requires Chrome or Edge)"
    echo "    stop            Stop the server"
    echo "    restart [--app] Stop and start the server"
    echo "    status          Show whether the server is running"
    echo "    help            Show this help message"
    echo ""
    echo "  Environment variables:"
    echo "    PORT            HTTP port (default: 19001)"
    echo "    PROJECTS_ROOT   Root folder containing your projects"
    echo "                    (default: parent directory of claude-monitor)"
    echo ""
    echo "  Examples:"
    echo "    ./run.sh start"
    echo "    ./run.sh start --app"
    echo "    PORT=8080 ./run.sh start"
    echo ""
}

# ── Dispatch ──────────────────────────────────────────────────────────────────
if [ $# -eq 0 ]; then
    cmd_help
    exit 1
fi

CMD="$1"
OPEN_MODE="browser"
[ "${2:-}" = "--app" ] && OPEN_MODE="app"

case "$CMD" in
    start)   cmd_start "$OPEN_MODE" ;;
    stop)    cmd_stop ;;
    restart) cmd_stop; cmd_start "$OPEN_MODE" ;;
    status)  cmd_status ;;
    help)    cmd_help ;;
    *)
        echo "Unknown command: $CMD"
        cmd_help
        exit 1
        ;;
esac
