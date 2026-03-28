#!/usr/bin/env bash
# claude-monitor — start/stop/status
# Uso: ./run.sh [start|stop|status]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.claude-monitor.pid"
LOG_FILE="$SCRIPT_DIR/.claude-monitor.log"
VENV="$SCRIPT_DIR/.venv"
PORT="${PORT:-19001}"
URL="http://localhost:$PORT"

green()  { echo -e "\033[0;32m$*\033[0m"; }
yellow() { echo -e "\033[0;33m$*\033[0m"; }
red()    { echo -e "\033[0;31m$*\033[0m"; }
info()   { echo -e "\033[0;36m→\033[0m $*"; }

# ── Verificar venv ──────────────────────────────────────────────────────────
ensure_venv() {
    if [ ! -f "$VENV/bin/uvicorn" ]; then
        info "A criar venv e instalar dependências..."
        python3 -m venv "$VENV"
        "$VENV/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"
        green "✓ Dependências instaladas"
    fi
}

# ── Status ──────────────────────────────────────────────────────────────────
cmd_status() {
    if [ -f "$PID_FILE" ]; then
        PID="$(cat "$PID_FILE")"
        if kill -0 "$PID" 2>/dev/null; then
            green "● claude-monitor está a correr (PID $PID)"
            echo "  URL: $URL"
            echo "  Log: $LOG_FILE"
            return 0
        else
            yellow "○ claude-monitor não está a correr (PID $PID obsoleto)"
            rm -f "$PID_FILE"
            return 1
        fi
    else
        yellow "○ claude-monitor não está a correr"
        return 1
    fi
}

# ── Start ───────────────────────────────────────────────────────────────────
cmd_start() {
    if [ -f "$PID_FILE" ]; then
        PID="$(cat "$PID_FILE")"
        if kill -0 "$PID" 2>/dev/null; then
            yellow "claude-monitor já está a correr (PID $PID) — $URL"
            return 0
        fi
        rm -f "$PID_FILE"
    fi

    ensure_venv

    info "A iniciar claude-monitor na porta $PORT..."
    "$VENV/bin/uvicorn" app:app \
        --host 0.0.0.0 \
        --port "$PORT" \
        --app-dir "$SCRIPT_DIR" \
        >> "$LOG_FILE" 2>&1 &

    echo $! > "$PID_FILE"
    PID="$(cat "$PID_FILE")"

    # Aguardar até 10s que o servidor responda
    for i in $(seq 1 20); do
        sleep 0.5
        if curl -sf "$URL/health" > /dev/null 2>&1; then
            green "✓ claude-monitor a correr (PID $PID)"
            echo "  → $URL"
            return 0
        fi
    done

    red "✗ Falhou a iniciar. Ver log: $LOG_FILE"
    cat "$LOG_FILE" | tail -20
    rm -f "$PID_FILE"
    exit 1
}

# ── Stop ────────────────────────────────────────────────────────────────────
cmd_stop() {
    if [ ! -f "$PID_FILE" ]; then
        yellow "claude-monitor não está a correr"
        return 0
    fi

    PID="$(cat "$PID_FILE")"
    if kill -0 "$PID" 2>/dev/null; then
        info "A parar claude-monitor (PID $PID)..."
        kill "$PID"
        # Aguardar até 5s
        for i in $(seq 1 10); do
            sleep 0.5
            kill -0 "$PID" 2>/dev/null || break
        done
        if kill -0 "$PID" 2>/dev/null; then
            kill -9 "$PID" 2>/dev/null || true
        fi
        green "✓ claude-monitor parado"
    else
        yellow "Processo $PID já não existe"
    fi
    rm -f "$PID_FILE"
}

# ── Dispatch ─────────────────────────────────────────────────────────────────
CMD="${1:-status}"

case "$CMD" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_stop; cmd_start ;;
    status)  cmd_status ;;
    *)
        echo "Uso: $(basename "$0") [start|stop|restart|status]"
        exit 1
        ;;
esac
