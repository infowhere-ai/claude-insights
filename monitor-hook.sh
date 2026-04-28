#!/usr/bin/env bash
# claude-monitor hook — writes status to .claude/status.json in the current project
# Called by Claude Code hooks for PreToolUse, PostToolUse, Notification, Stop, PreCompact
# Data arrives via stdin as JSON (not env vars).

EVENT="${CLAUDE_HOOK_EVENT:-unknown}"

# Resolve to the main worktree root regardless of where Claude was opened.
# Handles three cases:
#   1. Project root (normal) → git show-toplevel
#   2. Subdirectory of a project → walks up to the git root
#   3. Worktree → git-common-dir points to the main worktree's .git
_git_common_dir="$(git -C "$(pwd)" rev-parse --git-common-dir 2>/dev/null)"
if [[ "$_git_common_dir" == /* ]]; then
    # Absolute path → we are inside a worktree; main project is the parent of .git
    PROJECT_DIR="$(dirname "$_git_common_dir")"
else
    # Relative .git → main worktree or subdir; use show-toplevel to get the root
    PROJECT_DIR="$(git -C "$(pwd)" rev-parse --show-toplevel 2>/dev/null || pwd)"
fi

STATUS_FILE="$PROJECT_DIR/.claude/status.json"

mkdir -p "$(dirname "$STATUS_FILE")"

TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

case "$EVENT" in
    PreToolUse)
        # Claude Code passes hook data via stdin as JSON with field "tool_name"
        STDIN_DATA="$(cat)"
        TOOL=$(echo "$STDIN_DATA" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('tool_name','unknown'))" 2>/dev/null || echo "unknown")
        echo "{\"status\":\"working\",\"tool\":\"$TOOL\",\"ts\":\"$TS\"}" > "$STATUS_FILE"
        ;;
    PostToolUse)
        cat > /dev/null  # consume stdin
        echo "{\"status\":\"idle\",\"ts\":\"$TS\"}" > "$STATUS_FILE"
        ;;
    Notification)
        # stdin JSON has a "message" field with the notification text
        # Write notification as {"message":"..."} object to match frontend expectations
        STDIN_DATA="$(cat)"
        NOTIF_JSON=$(echo "$STDIN_DATA" | python3 -c "
import json, sys
d = json.load(sys.stdin)
msg = d.get('message', '')
print(json.dumps({'message': msg[:400]}))
" 2>/dev/null || echo '{"message":""}')
        echo "{\"status\":\"waiting\",\"state\":\"waiting\",\"notification\":$NOTIF_JSON,\"ts\":\"$TS\"}" > "$STATUS_FILE"
        ;;
    PreCompact)
        cat > /dev/null  # consume stdin
        echo "{\"status\":\"compacting\",\"state\":\"compacting\",\"ts\":\"$TS\"}" > "$STATUS_FILE"
        ;;
    Stop)
        cat > /dev/null  # consume stdin
        echo "{\"status\":\"stopped\",\"ts\":\"$TS\"}" > "$STATUS_FILE"
        ;;
    *)
        cat > /dev/null
        echo "{\"status\":\"idle\",\"ts\":\"$TS\"}" > "$STATUS_FILE"
        ;;
esac
