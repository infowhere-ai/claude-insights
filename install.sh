#!/usr/bin/env bash
# claude-monitor installer
# Installs dependencies and configures Claude Code hooks in ~/.claude/settings.json

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"

green()  { echo -e "\033[0;32m✓ $*\033[0m"; }
yellow() { echo -e "\033[0;33m⚠ $*\033[0m"; }
red()    { echo -e "\033[0;31m✗ $*\033[0m"; }
info()   { echo -e "\033[0;36m→\033[0m $*"; }
header() { echo -e "\n\033[1m$*\033[0m"; }

# ── 1. Check requirements ─────────────────────────────────────────────────────
header "Checking requirements..."

if ! command -v python3 &>/dev/null; then
    red "python3 not found. Install Python 3.10+ and try again."
    exit 1
fi
green "python3 $(python3 --version | cut -d' ' -f2)"

if ! command -v claude &>/dev/null; then
    red "claude CLI not found. Install Claude Code first: https://claude.ai/code"
    exit 1
fi
green "claude CLI $(claude --version 2>/dev/null | head -1 || echo 'found')"

# ── 2. Install Python dependencies ───────────────────────────────────────────
header "Installing dependencies..."

if [ ! -f "$VENV/bin/uvicorn" ]; then
    info "Creating virtual environment..."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"
    green "Dependencies installed"
else
    green "Dependencies already installed"
fi

# ── 3. Configure Claude Code hooks ───────────────────────────────────────────
header "Configuring Claude Code hooks..."

# Check current state and what needs to change
HOOK_STATUS=$(python3 - <<'PYEOF'
import json, os

settings_path = os.path.expanduser("~/.claude/settings.json")
SNIPPET = "status.json"

def has_hook(lst):
    return any(
        h.get("type") == "command" and SNIPPET in h.get("command", "")
        for entry in lst for h in entry.get("hooks", [])
    )

if not os.path.exists(settings_path):
    print("missing_pre missing_post new_file")
else:
    try:
        s = json.load(open(settings_path))
    except Exception:
        print("parse_error")
        exit()
    hooks = s.get("hooks", {})
    existing_hooks = sum([
        len(hooks.get("PreToolUse", [])),
        len(hooks.get("PostToolUse", [])),
        len(hooks.get("Stop", [])),
    ])
    pre_ok  = has_hook(hooks.get("PreToolUse",  []))
    post_ok = has_hook(hooks.get("PostToolUse", []))
    status  = []
    if not pre_ok:  status.append("missing_pre")
    if not post_ok: status.append("missing_post")
    if pre_ok and post_ok:
        status.append("already_installed")
    status.append(f"existing_hooks:{existing_hooks}")
    print(" ".join(status))
PYEOF
)

if echo "$HOOK_STATUS" | grep -q "parse_error"; then
    red "Could not parse $CLAUDE_SETTINGS — please check the file manually."
    exit 1
fi

if echo "$HOOK_STATUS" | grep -q "already_installed"; then
    green "Claude Code hooks already configured — nothing to do"
else
    # Show what will change
    echo ""
    echo "  The following hooks will be added to:"
    echo "  $CLAUDE_SETTINGS"
    echo ""
    echo "  ┌─ PreToolUse ────────────────────────────────────────────────────────"
    echo "  │  Runs before every Claude tool call."
    echo "  │  Writes {status: \"working\", tool: \"<tool_name>\"} to .claude/status.json"
    echo "  │  in the current project directory."
    echo "  │"
    echo "  ├─ PostToolUse ───────────────────────────────────────────────────────"
    echo "  │  Runs after every Claude tool call."
    echo "  │  Writes {status: \"idle\"} to .claude/status.json."
    echo "  └─────────────────────────────────────────────────────────────────────"
    echo ""
    echo "  These hooks only write a small JSON file — they do not send data"
    echo "  anywhere. Claude Monitor reads those files locally via SSE."
    echo ""

    EXISTING_COUNT=$(echo "$HOOK_STATUS" | grep -o 'existing_hooks:[0-9]*' | cut -d: -f2 || echo "0")
    if [ "$EXISTING_COUNT" -gt 0 ]; then
        yellow "You already have $EXISTING_COUNT hook(s) configured. They will be preserved — only the new hooks will be added."
        echo ""
    fi

    if echo "$HOOK_STATUS" | grep -q "new_file"; then
        info "settings.json does not exist yet — it will be created."
        echo ""
    fi

    # Ask for confirmation
    printf "  Proceed? [y/N] "
    read -r CONFIRM
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        yellow "Aborted. Hooks were not modified."
        echo ""
        echo "  You can configure them manually — see the README for the hook commands."
        exit 0
    fi

    # Apply hooks
    python3 - <<'PYEOF'
import json, os

settings_path = os.path.expanduser("~/.claude/settings.json")
SNIPPET = "status.json"

pre_hook = {
    "type": "command",
    "command": "bash -c 'PROJECT_DIR=\"$(pwd)\"; STATUS_FILE=\"$PROJECT_DIR/.claude/status.json\"; mkdir -p \"$(dirname \"$STATUS_FILE\")\"; TOOL=$(echo \"$CLAUDE_TOOL_INPUT\" | python3 -c \"import json,sys; d=json.load(sys.stdin); print(d.get(\\\"tool_name\\\",\\\"\\\"))\" 2>/dev/null || echo \"unknown\"); echo \"{\\\"status\\\":\\\"working\\\",\\\"tool\\\":\\\"$TOOL\\\",\\\"ts\\\":\\\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\\\"}\" > \"$STATUS_FILE\"'"
}

post_hook = {
    "type": "command",
    "command": "bash -c 'PROJECT_DIR=\"$(pwd)\"; STATUS_FILE=\"$PROJECT_DIR/.claude/status.json\"; mkdir -p \"$(dirname \"$STATUS_FILE\")\"; echo \"{\\\"status\\\":\\\"idle\\\",\\\"ts\\\":\\\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\\\"}\" > \"$STATUS_FILE\"'"
}

def has_hook(lst):
    return any(
        h.get("type") == "command" and SNIPPET in h.get("command", "")
        for entry in lst for h in entry.get("hooks", [])
    )

settings = {}
if os.path.exists(settings_path):
    with open(settings_path) as f:
        settings = json.load(f)

hooks     = settings.setdefault("hooks", {})
pre_list  = hooks.setdefault("PreToolUse",  [])
post_list = hooks.setdefault("PostToolUse", [])

if not has_hook(pre_list):
    pre_list.append({"matcher": "", "hooks": [pre_hook]})
    print("  Added PreToolUse hook")

if not has_hook(post_list):
    post_list.append({"matcher": "", "hooks": [post_hook]})
    print("  Added PostToolUse hook")

os.makedirs(os.path.dirname(settings_path), exist_ok=True)
with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)
print(f"  Saved {settings_path}")
PYEOF

    green "Hooks configured"
fi

# ── 4. Done ───────────────────────────────────────────────────────────────────
echo ""
green "Installation complete!"
echo ""
echo "  Start the monitor:"
echo "    ./run.sh start"
echo "    ./run.sh start --app   # open as standalone window"
echo ""
echo "  Note: restart any open Claude Code sessions to activate the hooks."
echo ""
