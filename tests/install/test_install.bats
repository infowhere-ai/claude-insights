#!/usr/bin/env bats
# Tests for install.sh
#
# Requires: bats-core (https://github.com/bats-core/bats-core)
# Run: bats tests/install/test_install.bats

INSTALL="${BATS_TEST_DIRNAME}/../../install.sh"

# ── helpers ───────────────────────────────────────────────────────────────────

setup() {
    export TEST_HOME="$(mktemp -d)"
    export FAKE_SETTINGS="$TEST_HOME/.claude/settings.json"
    export FAKE_HOOKS_DIR="$TEST_HOME/.claude/hooks"
    mkdir -p "$TEST_HOME/.claude"
}

teardown() {
    rm -rf "$TEST_HOME"
}

# ── --help ────────────────────────────────────────────────────────────────────

@test "--help exits with code 0" {
    run bash "$INSTALL" --help
    [ "$status" -eq 0 ]
}

@test "--help prints usage line" {
    run bash "$INSTALL" --help
    [[ "$output" == *"./install.sh"* ]]
}

@test "--help mentions --uninstall" {
    run bash "$INSTALL" --help
    [[ "$output" == *"--uninstall"* ]]
}

@test "--help mentions PROJECTS_ROOT" {
    run bash "$INSTALL" --help
    [[ "$output" == *"PROJECTS_ROOT"* ]]
}

@test "--help does not modify settings.json" {
    run bash "$INSTALL" --help
    [ ! -f "$FAKE_SETTINGS" ]
}

# ── unknown option ────────────────────────────────────────────────────────────

@test "Unknown option exits with non-zero" {
    run bash "$INSTALL" --bogus-flag
    [ "$status" -ne 0 ]
}

@test "Unknown option prints hint" {
    run bash "$INSTALL" --bogus-flag
    [[ "$output" == *"--help"* ]]
}

# ── settings.json hook detection (inline Python logic) ───────────────────────

@test "Hook detection finds existing monitor-hook entry" {
    # Build a settings.json that already has a monitor-hook entry
    cat > "$FAKE_SETTINGS" <<'EOF'
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "",
        "hooks": [
          {"type": "command", "command": "CLAUDE_HOOK_EVENT=PreToolUse /home/user/.claude/hooks/monitor-hook.sh"}
        ]
      }
    ]
  }
}
EOF
    # Run the detection snippet (extracted from install.sh Step 4)
    result=$(python3 - <<PYEOF
import json
settings = json.load(open("$FAKE_SETTINGS"))
hooks = settings.get("hooks", {})
found = any(
    "monitor-hook.sh" in h.get("command", "")
    for entries in hooks.values()
    for entry in entries
    for h in entry.get("hooks", [])
    if h.get("type") == "command"
)
print("found" if found else "not_found")
PYEOF
)
    [ "$result" = "found" ]
}

@test "Hook detection returns not_found for empty settings" {
    echo '{}' > "$FAKE_SETTINGS"
    result=$(python3 - <<PYEOF
import json
settings = json.load(open("$FAKE_SETTINGS"))
hooks = settings.get("hooks", {})
found = any(
    "monitor-hook.sh" in h.get("command", "")
    for entries in hooks.values()
    for entry in entries
    for h in entry.get("hooks", [])
    if h.get("type") == "command"
)
print("found" if found else "not_found")
PYEOF
)
    [ "$result" = "not_found" ]
}

# ── uninstall hook removal (inline Python logic) ─────────────────────────────

@test "Uninstall Python snippet removes monitor-hook entries" {
    # Settings with one monitor-hook and one other hook
    cat > "$FAKE_SETTINGS" <<'EOF'
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "",
        "hooks": [
          {"type": "command", "command": "CLAUDE_HOOK_EVENT=PreToolUse /home/user/.claude/hooks/monitor-hook.sh"},
          {"type": "command", "command": "some-other-hook.sh"}
        ]
      }
    ]
  }
}
EOF
    python3 - <<PYEOF
import json
settings_path = "$FAKE_SETTINGS"
s = json.load(open(settings_path))
hooks = s.get("hooks", {})
for event in list(hooks.keys()):
    entries = hooks[event]
    new_entries = []
    for entry in entries:
        new_hooks = [h for h in entry.get("hooks", []) if "monitor-hook.sh" not in h.get("command", "")]
        if new_hooks:
            entry["hooks"] = new_hooks
            new_entries.append(entry)
    if new_entries:
        hooks[event] = new_entries
    else:
        del hooks[event]
s["hooks"] = hooks
with open(settings_path, "w") as f:
    json.dump(s, f, indent=2)
PYEOF

    # monitor-hook.sh should be gone; other hook preserved
    result=$(python3 -c "
import json
s = json.load(open('$FAKE_SETTINGS'))
hooks = s.get('hooks', {})
has_monitor = any(
    'monitor-hook.sh' in h.get('command', '')
    for entries in hooks.values()
    for entry in entries
    for h in entry.get('hooks', [])
)
has_other = any(
    'some-other-hook.sh' in h.get('command', '')
    for entries in hooks.values()
    for entry in entries
    for h in entry.get('hooks', [])
)
print(f'monitor={has_monitor} other={has_other}')
")
    [[ "$result" == *"monitor=False"* ]]
    [[ "$result" == *"other=True"* ]]
}

@test "Uninstall Python snippet removes event key when no hooks remain" {
    cat > "$FAKE_SETTINGS" <<'EOF'
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "",
        "hooks": [
          {"type": "command", "command": "CLAUDE_HOOK_EVENT=PreToolUse /home/user/.claude/hooks/monitor-hook.sh"}
        ]
      }
    ]
  }
}
EOF
    python3 - <<PYEOF
import json
settings_path = "$FAKE_SETTINGS"
s = json.load(open(settings_path))
hooks = s.get("hooks", {})
for event in list(hooks.keys()):
    entries = hooks[event]
    new_entries = []
    for entry in entries:
        new_hooks = [h for h in entry.get("hooks", []) if "monitor-hook.sh" not in h.get("command", "")]
        if new_hooks:
            entry["hooks"] = new_hooks
            new_entries.append(entry)
    if new_entries:
        hooks[event] = new_entries
    else:
        del hooks[event]
s["hooks"] = hooks
with open(settings_path, "w") as f:
    json.dump(s, f, indent=2)
PYEOF

    result=$(python3 -c "
import json
s = json.load(open('$FAKE_SETTINGS'))
print('PreToolUse' in s.get('hooks', {}))
")
    [ "$result" = "False" ]
}

# ── idempotent hook insertion ─────────────────────────────────────────────────

@test "Adding hook to empty settings produces valid JSON" {
    echo '{}' > "$FAKE_SETTINGS"
    HOOK_DEST="/fake/.claude/hooks/monitor-hook.sh"
    python3 - <<PYEOF
import json, os
settings_path = "$FAKE_SETTINGS"
hook_dest = "$HOOK_DEST"
events = ["PreToolUse", "PostToolUse", "Notification", "Stop", "PreCompact"]
settings = json.load(open(settings_path))
hooks = settings.setdefault("hooks", {})
for event in events:
    entries = hooks.setdefault(event, [])
    entries.append({
        "matcher": "",
        "hooks": [{"type": "command", "command": f"CLAUDE_HOOK_EVENT={event} {hook_dest}"}]
    })
with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)
PYEOF

    # Must be valid JSON and contain all 5 events
    python3 - <<PYEOF
import json
s = json.load(open("$FAKE_SETTINGS"))
hooks = s["hooks"]
for event in ["PreToolUse", "PostToolUse", "Notification", "Stop", "PreCompact"]:
    assert event in hooks, f"Missing event: {event}"
print("ok")
PYEOF
}
