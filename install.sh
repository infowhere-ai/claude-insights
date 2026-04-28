#!/usr/bin/env bash
# Claude Insights — Installer
# https://github.com/infowhere-be/claude-monitor

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
HOOK_DIR="$HOME/.claude/hooks"
HOOK_DEST="$HOOK_DIR/monitor-hook.sh"
HOOK_SRC="$SCRIPT_DIR/monitor-hook.sh"
SETTINGS="$HOME/.claude/settings.json"
ENV_FILE="$SCRIPT_DIR/.env"
EVENTS=(PreToolUse PostToolUse Notification Stop PreCompact)

green()  { echo -e "\033[0;32m  ✓ $*\033[0m"; }
red()    { echo -e "\033[0;31m  ✗ $*\033[0m"; }
info()   { echo -e "\033[0;36m  →\033[0m $*"; }
bold()   { echo -e "\033[1m$*\033[0m"; }
dim()    { echo -e "\033[2m    $*\033[0m"; }

# ── Help ──────────────────────────────────────────────────────────────────────
cmd_help() {
    echo ""
    bold "Claude Insights — Installer"
    echo ""
    echo "  Usage:"
    echo "    ./install.sh              Install and configure"
    echo "    ./install.sh --uninstall  Remove all changes made by this installer"
    echo "    ./install.sh --help       Show this help"
    echo ""
    bold "  What this installer does:"
    dim "1. Checks that python3 (3.10+), claude CLI, and git are available"
    dim "2. Creates a Python virtual environment in .venv/"
    dim "3. Installs Python dependencies (FastAPI, uvicorn)"
    dim "4. Copies monitor-hook.sh to ~/.claude/hooks/"
    dim "5. Adds 5 hook entries to ~/.claude/settings.json"
    dim "   (existing hooks are never removed or modified)"
    dim "6. Creates .env with your PROJECTS_ROOT path"
    echo ""
    bold "  What it touches on your machine:"
    dim "~/.claude/hooks/monitor-hook.sh   hook script (new file)"
    dim "~/.claude/settings.json           hook entries added (existing preserved)"
    dim ".venv/                            Python virtualenv (inside this folder)"
    dim ".env                              your PROJECTS_ROOT (inside this folder)"
    echo ""
    bold "  What --uninstall does:"
    dim "Removes ~/.claude/hooks/monitor-hook.sh"
    dim "Removes the 5 monitor-hook entries from ~/.claude/settings.json"
    dim "Deletes .venv/ and .env"
    dim "(your other hooks and Claude settings are preserved)"
    echo ""
}

# ── Uninstall ─────────────────────────────────────────────────────────────────
cmd_uninstall() {
    bold "\nClaude Insights — Uninstall"
    echo ""

    echo "  This will remove:"
    echo "    $HOOK_DEST"
    echo "    5 monitor-hook entries from $SETTINGS"
    echo "    $VENV"
    echo "    $ENV_FILE"
    echo ""
    printf "  Proceed? [y/N] "
    read -r CONFIRM
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        echo "  Aborted."
        exit 0
    fi

    echo ""

    # Remove hook script
    if [ -f "$HOOK_DEST" ]; then
        rm "$HOOK_DEST"
        green "Removed $HOOK_DEST"
    else
        info "Hook script not found — skipping"
    fi

    # Remove hook entries from settings.json
    if [ -f "$SETTINGS" ]; then
        python3 - <<PYEOF
import json, os

settings_path = "$SETTINGS"
try:
    s = json.load(open(settings_path))
except Exception as e:
    print(f"  Could not parse {settings_path}: {e}")
    exit(1)

hooks = s.get("hooks", {})
removed = 0
for event in list(hooks.keys()):
    entries = hooks[event]
    new_entries = []
    for entry in entries:
        new_hooks = [h for h in entry.get("hooks", []) if "monitor-hook.sh" not in h.get("command", "")]
        if new_hooks:
            entry["hooks"] = new_hooks
            new_entries.append(entry)
        else:
            removed += len(entry.get("hooks", []))
    if new_entries:
        hooks[event] = new_entries
    else:
        del hooks[event]
        removed += 1

s["hooks"] = hooks
with open(settings_path, "w") as f:
    json.dump(s, f, indent=2)
print(f"  \033[0;32m  ✓ Removed {removed} hook entry/entries from {settings_path}\033[0m")
PYEOF
    else
        info "settings.json not found — skipping"
    fi

    # Remove venv
    if [ -d "$VENV" ]; then
        rm -rf "$VENV"
        green "Removed .venv/"
    else
        info ".venv/ not found — skipping"
    fi

    # Remove .env
    if [ -f "$ENV_FILE" ]; then
        rm "$ENV_FILE"
        green "Removed .env"
    else
        info ".env not found — skipping"
    fi

    echo ""
    green "Uninstall complete."
    echo ""
    echo "  Restart any open Claude Code sessions to deactivate the hooks."
    echo ""
}

# ── Step 1 — Pre-flight checks ────────────────────────────────────────────────
check_requirements() {
    bold "\nStep 1 — Checking requirements"
    echo ""

    local ok=true

    if ! command -v python3 &>/dev/null; then
        red "python3 not found. Install Python 3.10+ and try again."
        ok=false
    else
        local ver
        ver=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        local major minor
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 10 ]; }; then
            red "python3 $ver found but 3.10+ is required."
            ok=false
        else
            green "python3 $ver"
        fi
    fi

    if ! command -v claude &>/dev/null; then
        red "claude CLI not found. Install Claude Code first: https://claude.ai/code"
        ok=false
    else
        green "claude CLI $(claude --version 2>/dev/null | head -1 || echo 'found')"
    fi

    if ! command -v git &>/dev/null; then
        red "git not found. Install git and try again."
        ok=false
    else
        green "git $(git --version | cut -d' ' -f3)"
    fi

    if [ "$ok" = false ]; then
        echo ""
        red "Pre-flight checks failed. Fix the issues above and re-run ./install.sh"
        exit 1
    fi
}

# ── Step 2 — Python dependencies ──────────────────────────────────────────────
install_deps() {
    bold "\nStep 2 — Python dependencies"
    echo ""

    if [ -f "$VENV/bin/uvicorn" ]; then
        green "Virtual environment already exists — skipping"
    else
        info "Creating virtual environment..."
        python3 -m venv "$VENV"
        info "Installing dependencies..."
        "$VENV/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"
        green "Dependencies installed"
    fi
}

# ── Step 3 — Hook script ──────────────────────────────────────────────────────
install_hook_script() {
    bold "\nStep 3 — Hook script"
    echo ""

    if [ ! -f "$HOOK_SRC" ]; then
        red "monitor-hook.sh not found in $SCRIPT_DIR"
        red "Re-clone the repository and try again."
        exit 1
    fi

    mkdir -p "$HOOK_DIR"

    if [ -f "$HOOK_DEST" ]; then
        if cmp -s "$HOOK_SRC" "$HOOK_DEST"; then
            green "Hook script already up to date"
        else
            cp "$HOOK_SRC" "$HOOK_DEST"
            chmod +x "$HOOK_DEST"
            green "Hook script updated at $HOOK_DEST"
        fi
    else
        cp "$HOOK_SRC" "$HOOK_DEST"
        chmod +x "$HOOK_DEST"
        green "Hook script installed at $HOOK_DEST"
    fi
}

# ── Step 4 — Register hooks in settings.json ──────────────────────────────────
install_hooks() {
    bold "\nStep 4 — Claude Code hooks"
    echo ""

    python3 - <<PYEOF
import json, os, sys

settings_path = "$SETTINGS"
hook_dest     = "$HOOK_DEST"
events        = ["PreToolUse", "PostToolUse", "Notification", "Stop", "PreCompact"]

def hook_cmd(event):
    return f"CLAUDE_HOOK_EVENT={event} {hook_dest}"

def has_monitor_hook(entries):
    return any(
        "monitor-hook.sh" in h.get("command", "")
        for entry in entries
        for h in entry.get("hooks", [])
        if h.get("type") == "command"
    )

# Load or create settings
settings = {}
if os.path.exists(settings_path):
    try:
        settings = json.load(open(settings_path))
    except Exception as e:
        print(f"  \033[0;31m  ✗ Cannot parse {settings_path}: {e}\033[0m")
        sys.exit(1)

hooks = settings.setdefault("hooks", {})

# Analyse what needs to be done
to_add   = []
already  = []
for event in events:
    entries = hooks.get(event, [])
    if has_monitor_hook(entries):
        already.append(event)
    else:
        to_add.append(event)

# Show plan
if already:
    for event in already:
        print(f"    \033[0;32m✓\033[0m {event:<14} → monitor-hook.sh  (already present, skipping)")
if to_add:
    for event in to_add:
        print(f"    \033[0;36m+\033[0m {event:<14} → monitor-hook.sh  (will add)")

if not to_add:
    print("")
    print("  \033[0;32m  ✓ All 5 hooks already configured — nothing to do.\033[0m")
    sys.exit(0)

# Show what will be modified
print("")
print(f"  ~/.claude/settings.json will be updated.")
print(f"  Your existing hooks will NOT be removed or modified.")
print(f"  Only the missing entries above will be added.")
print("")
response = input("  Proceed? [y/N] ").strip().lower()
if response not in ("y", "yes"):
    print("  Aborted. Hooks were not modified.")
    sys.exit(0)

# Apply
os.makedirs(os.path.dirname(settings_path), exist_ok=True)
for event in to_add:
    entries = hooks.setdefault(event, [])
    entries.append({"matcher": "", "hooks": [{"type": "command", "command": hook_cmd(event)}]})

with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)

print("")
print(f"  \033[0;32m  ✓ {len(to_add)} hook(s) added to {settings_path}\033[0m")
PYEOF
}

# ── Step 5 — PROJECTS_ROOT ────────────────────────────────────────────────────
configure_projects_root() {
    bold "\nStep 5 — Projects root"
    echo ""

    # Load existing value if present
    if [ -f "$ENV_FILE" ]; then
        # shellcheck disable=SC1090
        source "$ENV_FILE" 2>/dev/null || true
    fi

    if [ -n "${PROJECTS_ROOT:-}" ]; then
        green "PROJECTS_ROOT already set to: $PROJECTS_ROOT"
        return
    fi

    # Auto-detect: parent of claude-monitor
    local detected
    detected="$(dirname "$SCRIPT_DIR")"

    info "Detecting projects root..."
    echo ""
    echo "  Auto-detected: $detected"
    echo "  (parent directory of claude-monitor)"
    echo ""
    printf "  Accept this? [Y/n] "
    read -r ACCEPT

    local root
    if [[ "$ACCEPT" =~ ^[Nn]$ ]]; then
        printf "  Enter your projects root path: "
        read -r root
        root="${root/#\~/$HOME}"  # expand ~ if present
        if [ ! -d "$root" ]; then
            red "Directory not found: $root"
            exit 1
        fi
    else
        root="$detected"
    fi

    echo "PROJECTS_ROOT=$root" > "$ENV_FILE"
    green "PROJECTS_ROOT=$root  (saved to .env)"
}

# ── Step 6 — Verify ───────────────────────────────────────────────────────────
verify_install() {
    bold "\nStep 6 — Verifying installation"
    echo ""

    python3 - <<PYEOF
import json, os, sys

settings_path = "$SETTINGS"
hook_script   = "$HOOK_DEST"
events        = ["PreToolUse", "PostToolUse", "Notification", "Stop", "PreCompact"]
errors        = []

if not os.path.exists(hook_script):
    errors.append(f"Hook script missing: {hook_script}")

if not os.path.exists(settings_path):
    errors.append(f"settings.json not found: {settings_path}")
else:
    try:
        s = json.load(open(settings_path))
        hooks = s.get("hooks", {})
        for event in events:
            entries = hooks.get(event, [])
            found = any(
                "monitor-hook.sh" in h.get("command", "")
                for entry in entries
                for h in entry.get("hooks", [])
                if h.get("type") == "command"
            )
            if not found:
                errors.append(f"Hook not registered: {event}")
    except Exception as e:
        errors.append(f"Cannot parse settings.json: {e}")

if errors:
    for e in errors:
        print(f"  \033[0;31m  ✗ {e}\033[0m")
    sys.exit(1)
else:
    print(f"  \033[0;32m  ✓ All 5 hooks registered\033[0m")
    print(f"  \033[0;32m  ✓ Hook script present at {hook_script}\033[0m")
PYEOF
}

# ── Done ──────────────────────────────────────────────────────────────────────
done_message() {
    echo ""
    echo -e "\033[0;32m────────────────────────────────────────\033[0m"
    bold "  Installation complete!"
    echo -e "\033[0;32m────────────────────────────────────────\033[0m"
    echo ""
    echo "  Start the monitor:"
    echo "    ./run.sh start"
    echo ""
    echo "  Important: restart any open Claude Code sessions"
    echo "  to activate the hooks."
    echo ""
}

# ── Dispatch ──────────────────────────────────────────────────────────────────
case "${1:-}" in
    --help|-h)
        cmd_help
        ;;
    --uninstall)
        cmd_uninstall
        ;;
    "")
        check_requirements
        install_deps
        install_hook_script
        install_hooks
        configure_projects_root
        verify_install
        done_message
        ;;
    *)
        echo "Unknown option: $1"
        echo "Run ./install.sh --help for usage."
        exit 1
        ;;
esac
