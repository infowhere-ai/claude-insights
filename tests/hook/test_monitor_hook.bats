#!/usr/bin/env bats
# Tests for monitor-hook.sh
#
# Requires: bats-core (https://github.com/bats-core/bats-core)
# Install: brew install bats-core  OR  apt install bats
#
# Run: bats tests/hook/test_monitor_hook.bats

# Prefer the source in the repo root; fall back to the installed copy in ~/.claude/hooks/
_REPO_HOOK="${BATS_TEST_DIRNAME}/../../monitor-hook.sh"
_INSTALLED_HOOK="$HOME/.claude/hooks/monitor-hook.sh"
if [ -f "$_REPO_HOOK" ]; then
    HOOK="$_REPO_HOOK"
else
    HOOK="$_INSTALLED_HOOK"
fi

# ── helpers ───────────────────────────────────────────────────────────────────

setup() {
    # Create a temp git repo so the hook can resolve the project dir
    export TEST_DIR="$(mktemp -d)"
    cd "$TEST_DIR"
    git init -q
    git config user.email "test@test.com"
    git config user.name "Test"
    git commit --allow-empty -q -m "init"

    export STATUS_FILE="$TEST_DIR/.claude/status.json"
}

teardown() {
    rm -rf "$TEST_DIR"
}

run_hook() {
    local event="$1"
    local stdin_data="${2:-}"
    echo "$stdin_data" | CLAUDE_HOOK_EVENT="$event" bash "$HOOK"
}

read_status() {
    cat "$STATUS_FILE"
}

# ── PreToolUse ────────────────────────────────────────────────────────────────

@test "PreToolUse writes status=working" {
    run_hook "PreToolUse" '{"tool_name":"Read"}'
    [ -f "$STATUS_FILE" ]
    status=$(read_status)
    [[ "$status" == *'"status":"working"'* ]]
}

@test "PreToolUse writes tool name from stdin" {
    run_hook "PreToolUse" '{"tool_name":"Bash"}'
    status=$(read_status)
    [[ "$status" == *'"tool":"Bash"'* ]]
}

@test "PreToolUse with invalid JSON still writes status" {
    run_hook "PreToolUse" 'not json'
    [ -f "$STATUS_FILE" ]
    status=$(read_status)
    [[ "$status" == *'"status":"working"'* ]]
}

@test "PreToolUse writes timestamp" {
    run_hook "PreToolUse" '{"tool_name":"Read"}'
    status=$(read_status)
    [[ "$status" == *'"ts":'* ]]
}

# ── PostToolUse ───────────────────────────────────────────────────────────────

@test "PostToolUse writes status=idle" {
    run_hook "PostToolUse" ""
    [ -f "$STATUS_FILE" ]
    status=$(read_status)
    [[ "$status" == *'"status":"idle"'* ]]
}

@test "PostToolUse writes timestamp" {
    run_hook "PostToolUse" ""
    status=$(read_status)
    [[ "$status" == *'"ts":'* ]]
}

# ── Notification ──────────────────────────────────────────────────────────────

@test "Notification writes status=waiting" {
    run_hook "Notification" '{"message":"Press Enter to continue"}'
    [ -f "$STATUS_FILE" ]
    status=$(read_status)
    [[ "$status" == *'"status":"waiting"'* ]]
}

@test "Notification writes notification object with message" {
    run_hook "Notification" '{"message":"Hello user"}'
    content=$(read_status)
    # Python json.dumps adds a space after ":", so match "Hello user" anywhere in the JSON
    [[ "$content" == *"Hello user"* ]]
}

@test "Notification truncates message at 400 chars" {
    long_msg=$(python3 -c "print('x' * 500)")
    run_hook "Notification" "{\"message\":\"$long_msg\"}"
    status=$(read_status)
    # message value should be 400 x's — verify file is valid JSON
    python3 -c "import json, sys; d=json.loads(open('$STATUS_FILE').read()); assert len(d['notification']['message']) == 400"
}

# ── PreCompact ────────────────────────────────────────────────────────────────

@test "PreCompact writes status=compacting" {
    run_hook "PreCompact" ""
    [ -f "$STATUS_FILE" ]
    status=$(read_status)
    [[ "$status" == *'"status":"compacting"'* ]]
}

@test "PreCompact writes state=compacting" {
    run_hook "PreCompact" ""
    status=$(read_status)
    [[ "$status" == *'"state":"compacting"'* ]]
}

# ── Stop ──────────────────────────────────────────────────────────────────────

@test "Stop writes status=stopped" {
    run_hook "Stop" ""
    [ -f "$STATUS_FILE" ]
    status=$(read_status)
    [[ "$status" == *'"status":"stopped"'* ]]
}

# ── Unknown event ─────────────────────────────────────────────────────────────

@test "Unknown event writes status=idle" {
    run_hook "SomeUnknownEvent" ""
    [ -f "$STATUS_FILE" ]
    status=$(read_status)
    [[ "$status" == *'"status":"idle"'* ]]
}

# ── Output is valid JSON ──────────────────────────────────────────────────────

@test "PreToolUse output is valid JSON" {
    run_hook "PreToolUse" '{"tool_name":"Read"}'
    python3 -c "import json; json.load(open('$STATUS_FILE'))"
}

@test "PostToolUse output is valid JSON" {
    run_hook "PostToolUse" ""
    python3 -c "import json; json.load(open('$STATUS_FILE'))"
}

@test "Notification output is valid JSON" {
    run_hook "Notification" '{"message":"test"}'
    python3 -c "import json; json.load(open('$STATUS_FILE'))"
}

@test "Stop output is valid JSON" {
    run_hook "Stop" ""
    python3 -c "import json; json.load(open('$STATUS_FILE'))"
}

# ── Creates .claude/ directory if missing ─────────────────────────────────────

@test "Hook creates .claude directory if missing" {
    rm -rf "$TEST_DIR/.claude"
    run_hook "Stop" ""
    [ -d "$TEST_DIR/.claude" ]
    [ -f "$STATUS_FILE" ]
}

# ── Sequential writes ─────────────────────────────────────────────────────────

@test "Multiple events overwrite the same status file" {
    run_hook "PreToolUse" '{"tool_name":"Read"}'
    run_hook "PostToolUse" ""
    status=$(read_status)
    [[ "$status" == *'"status":"idle"'* ]]
    [[ "$status" != *'"status":"working"'* ]]
}
