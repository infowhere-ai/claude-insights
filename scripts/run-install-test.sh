#!/usr/bin/env bash
# Build the wheel and run the Multipass installation test.
#
# Usage: ./scripts/run-install-test.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'
info()    { echo -e "${BLUE}→${RESET} $*"; }
success() { echo -e "${GREEN}✓${RESET} $*"; }
error()   { echo -e "${RED}✗${RESET} $*" >&2; }
header()  { echo -e "\n${BOLD}$*${RESET}"; }

# ── Build wheel ───────────────────────────────────────────────────────────────

header "ETAPA 1/2 — Build wheel"
cd "$REPO_DIR"

info "Installing hatchling..."
pip install --quiet hatchling

info "Building wheel..."
python -m hatchling build --target wheel

WHEEL=$(ls dist/*.whl | sort -V | tail -1)
[[ -f "$WHEEL" ]] || { error "Wheel not found in dist/"; exit 1; }
success "Wheel built: $WHEEL"

# ── Run Multipass test ────────────────────────────────────────────────────────

header "ETAPA 2/2 — Run Multipass installation test"
bash "$SCRIPT_DIR/test-install-multipass.sh" --wheel "$WHEEL"
