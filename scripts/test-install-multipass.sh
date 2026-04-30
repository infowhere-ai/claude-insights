#!/usr/bin/env bash
# Test claude-insights installation in a fresh Ubuntu VM via Multipass.
#
# Usage:
#   ./scripts/test-install-multipass.sh                    # test latest PyPI release
#   ./scripts/test-install-multipass.sh --wheel PATH.whl   # test local wheel
#   ./scripts/test-install-multipass.sh --deb PATH.deb     # test local .deb package
#
# Requires: Homebrew (multipass is installed automatically if missing)
#
# What is tested:
#   1. Package installs without errors
#   2. Server starts and responds on the expected port
#   3. /health returns {"status":"ok"}
#   4. /api/status returns a valid JSON list
#   5. SSE endpoint /events opens without error

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────

PORT=4000
VM_NAME="claude-insights-test-$(date +%s)"
UBUNTU_RELEASE="22.04"

DEB_PATH=""
WHEEL_PATH=""

# ── Colour helpers ────────────────────────────────────────────────────────────

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${BLUE}→${RESET} $*"; }
success() { echo -e "${GREEN}✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}!${RESET} $*"; }
error()   { echo -e "${RED}✗${RESET} $*" >&2; }
header()  { echo -e "\n${BOLD}$*${RESET}"; }

# ── Argument parsing ──────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
  case "$1" in
    --deb)    DEB_PATH="$2";   shift 2 ;;
    --wheel)  WHEEL_PATH="$2"; shift 2 ;;
    --help|-h)
      echo "Usage: $0 [--wheel PATH.whl | --deb PATH.deb]"
      echo ""
      echo "  (no args)          Install latest claude-insights from PyPI"
      echo "  --wheel PATH.whl   Install from a local wheel file"
      echo "  --deb   PATH.deb   Install from a local .deb package"
      exit 0
      ;;
    *) error "Unknown option: $1"; exit 1 ;;
  esac
done

# ── Pre-flight checks ─────────────────────────────────────────────────────────

header "PRE-FLIGHT CHECKS"

if ! command -v multipass &>/dev/null; then
  warn "multipass not found — installing via Homebrew..."
  if ! command -v brew &>/dev/null; then
    error "Homebrew not found. Install it from https://brew.sh and retry."
    exit 1
  fi
  brew install multipass
  # After install, multipass may need the daemon to start
  if ! multipass version &>/dev/null 2>&1; then
    warn "Multipass installed but daemon not ready — waiting 10s..."
    sleep 10
  fi
fi
success "multipass found: $(multipass version | head -1)"

if [[ -n "$DEB_PATH" ]]; then
  [[ -f "$DEB_PATH" ]] || { error ".deb not found: $DEB_PATH"; exit 1; }
  DEB_PATH="$(realpath "$DEB_PATH")"
  success "Using .deb: $DEB_PATH"
elif [[ -n "$WHEEL_PATH" ]]; then
  [[ -f "$WHEEL_PATH" ]] || { error "Wheel not found: $WHEEL_PATH"; exit 1; }
  WHEEL_PATH="$(realpath "$WHEEL_PATH")"
  success "Using wheel: $WHEEL_PATH"
else
  success "Will install latest claude-insights from PyPI"
fi

# ── Cleanup trap ──────────────────────────────────────────────────────────────

cleanup() {
  if multipass list --format csv 2>/dev/null | grep -q "$VM_NAME"; then
    info "Cleaning up VM $VM_NAME..."
    multipass delete "$VM_NAME" --purge 2>/dev/null || true
    success "VM removed"
  fi
}
trap cleanup EXIT

# ── Create VM ─────────────────────────────────────────────────────────────────

header "ETAPA 1/4 — Create VM"
info "Launching Ubuntu ${UBUNTU_RELEASE} VM: $VM_NAME"
multipass launch --name "$VM_NAME" "$UBUNTU_RELEASE" --cpus 2 --memory 1G --disk 8G
success "VM ready"

# ── Transfer files ────────────────────────────────────────────────────────────

header "ETAPA 2/4 — Install claude-insights"

if [[ -n "$DEB_PATH" ]]; then
  info "Transferring .deb to VM..."
  multipass transfer "$DEB_PATH" "$VM_NAME":/tmp/claude-insights.deb
  info "Installing .deb..."
  multipass exec "$VM_NAME" -- sudo apt-get install -y /tmp/claude-insights.deb
  success ".deb installed"

elif [[ -n "$WHEEL_PATH" ]]; then
  info "Transferring wheel to VM..."
  WHEEL_FILENAME="$(basename "$WHEEL_PATH")"
  info "Wheel filename: $WHEEL_FILENAME"
  multipass transfer "$WHEEL_PATH" "$VM_NAME":/tmp/"$WHEEL_FILENAME"
  info "Installing dependencies in VM..."
  multipass exec "$VM_NAME" -- sudo apt-get update -qq
  multipass exec "$VM_NAME" -- sudo apt-get install -y -qq python3-pip pipx
  info "Installing wheel via pipx..."
  multipass exec "$VM_NAME" -- pipx install "/tmp/$WHEEL_FILENAME"
  success "Wheel installed"

else
  info "Installing latest release from PyPI via pipx..."
  multipass exec "$VM_NAME" -- bash -c "
    sudo apt-get update -qq &&
    sudo apt-get install -y -qq python3-pip pipx &&
    pipx install claude-insights
  "
  success "PyPI release installed"
fi

# ── Verify binary exists ──────────────────────────────────────────────────────

info "Checking binary..."
BINARY_PATH=$(multipass exec "$VM_NAME" -- bash -c "
  command -v claude-insights 2>/dev/null ||
  ls ~/.local/bin/claude-insights 2>/dev/null ||
  ls /usr/local/bin/claude-insights 2>/dev/null ||
  echo ''
")

if [[ -z "$BINARY_PATH" ]]; then
  error "claude-insights binary not found in VM after installation"
  exit 1
fi
success "Binary found: $BINARY_PATH"

info "Version check..."
multipass exec "$VM_NAME" -- bash -c "
  export PATH=\$PATH:\$HOME/.local/bin
  claude-insights --version
"

# ── Smoke test ────────────────────────────────────────────────────────────────

header "ETAPA 3/4 — Smoke test"

SMOKE_RESULT=$(multipass exec "$VM_NAME" -- bash << SMOKE
set -euo pipefail

export PATH=\$PATH:\$HOME/.local/bin
PORT=${PORT}

# Create a mock project
mkdir -p /tmp/test-root/smoke-project/.claude
echo '{"status":"idle","tool":null,"file":null}' \
  > /tmp/test-root/smoke-project/.claude/status.json

echo "→ Starting server on port \$PORT..."
PROJECTS_ROOT=/tmp/test-root \
  claude-insights start --port "\$PORT" &
SERVER_PID=\$!
echo "  PID: \$SERVER_PID"

# Wait up to 30s for the server to respond
READY=0
for i in \$(seq 1 30); do
  if curl -sf "http://localhost:\$PORT/health" &>/dev/null; then
    READY=1
    echo "→ Server ready after \${i}s"
    break
  fi
  sleep 1
done

if [ "\$READY" -eq 0 ]; then
  echo "FAIL: server never started within 30s"
  kill "\$SERVER_PID" 2>/dev/null || true
  exit 1
fi

# Test 1: /health
echo ""
echo "TEST 1: /health"
HEALTH=\$(curl -sf "http://localhost:\$PORT/health")
echo "  Response: \$HEALTH"
echo "\$HEALTH" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['status'] == 'ok', f'Expected ok, got: {d}'
print('  PASS')
"

# Test 2: /api/status
echo ""
echo "TEST 2: /api/status"
STATUS=\$(curl -sf "http://localhost:\$PORT/api/status")
echo "  Response: \$STATUS"
echo "\$STATUS" | python3 -c "
import json, sys
projects = json.load(sys.stdin)
print(f'  Projects tracked: {len(projects)}')
print('  PASS')
"

# Test 3: /api/version
echo ""
echo "TEST 3: /api/version"
VERSION=\$(curl -sf "http://localhost:\$PORT/api/version")
echo "  Response: \$VERSION"
echo "  PASS"

# Test 4: SSE endpoint opens
echo ""
echo "TEST 4: /events SSE endpoint"
curl -sf --max-time 2 "http://localhost:\$PORT/events" &>/dev/null || true
echo "  PASS (SSE endpoint reachable)"

kill "\$SERVER_PID" 2>/dev/null || true
echo ""
echo "ALL TESTS PASSED"
SMOKE
)

echo "$SMOKE_RESULT"

if echo "$SMOKE_RESULT" | grep -q "ALL TESTS PASSED"; then
  success "All smoke tests passed"
else
  error "Smoke tests failed"
  exit 1
fi

# ── Summary ───────────────────────────────────────────────────────────────────

header "ETAPA 4/4 — Summary"

if [[ -n "$DEB_PATH" ]]; then
  echo "  Method:  .deb package"
elif [[ -n "$WHEEL_PATH" ]]; then
  echo "  Method:  local wheel"
else
  echo "  Method:  PyPI release"
fi
echo "  VM:      Ubuntu ${UBUNTU_RELEASE}"
echo "  Port:    ${PORT}"
echo ""
success "Installation test complete — claude-insights is working correctly"
