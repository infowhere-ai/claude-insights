#!/usr/bin/env bash
# Build a .deb package for claude-insights from the Linux binary.
#
# Usage: bash dist/build-deb.sh <binary_path> <version>
# Output: dist/claude-insights_<version>_amd64.deb
#
# Called by the release GitHub Actions workflow.

set -euo pipefail

BINARY="${1:?Usage: build-deb.sh <binary_path> <version>}"
VERSION="${2:?Usage: build-deb.sh <binary_path> <version>}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(mktemp -d)/claude-insights_${VERSION}_amd64"
OUTPUT="${SCRIPT_DIR}/claude-insights_${VERSION}_amd64.deb"

echo "→ Building .deb for claude-insights v${VERSION}"

# ── Package layout ────────────────────────────────────────────────────────────

mkdir -p "${PKG_DIR}/usr/local/bin"
mkdir -p "${PKG_DIR}/DEBIAN"

cp "${BINARY}" "${PKG_DIR}/usr/local/bin/claude-insights"
chmod 755 "${PKG_DIR}/usr/local/bin/claude-insights"

# ── Control file ──────────────────────────────────────────────────────────────

cat > "${PKG_DIR}/DEBIAN/control" << EOF
Package: claude-insights
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: amd64
Maintainer: Leandro Siciliano <infowhere@infowhere.ai>
Description: Real-time dashboard for Claude Code sessions
 Monitor what Claude Code is doing in real time — active tool,
 session history, token usage, and thinking blocks.
Homepage: https://github.com/infowhere-ai/claude-insights
EOF

# ── Post-install script ────────────────────────────────────────────────────────

cat > "${PKG_DIR}/DEBIAN/postinst" << 'EOF'
#!/bin/sh
set -e
echo ""
echo "claude-insights installed successfully."
echo "Run 'claude-insights install' to set up the Claude Code hook."
echo "Run 'claude-insights start' to launch the dashboard."
echo ""
EOF
chmod 755 "${PKG_DIR}/DEBIAN/postinst"

# ── Build .deb ────────────────────────────────────────────────────────────────

dpkg-deb --build "${PKG_DIR}" "${OUTPUT}"

echo "✓ Built: ${OUTPUT}"
