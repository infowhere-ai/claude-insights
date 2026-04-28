#!/usr/bin/env bash
# Update the Homebrew formula with new version and SHA256 checksum.
#
# Usage: bash dist/update-homebrew-formula.sh <version> <sha256_arm64> <formula_path>
#
# Called by the release GitHub Actions workflow after computing checksums.

set -euo pipefail

VERSION="${1:?Missing version}"
SHA256_ARM64="${2:?Missing sha256_arm64}"
FORMULA_PATH="${3:?Missing formula_path}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="${SCRIPT_DIR}/homebrew/claude-insights.rb"

echo "→ Updating Homebrew formula for claude-insights v${VERSION}"

cp "${TEMPLATE}" "${FORMULA_PATH}"

sed -i "s/FORMULA_VERSION/${VERSION}/g" "${FORMULA_PATH}"
sed -i "s/FORMULA_SHA256_ARM64/${SHA256_ARM64}/" "${FORMULA_PATH}"

echo "✓ Updated: ${FORMULA_PATH}"
grep -E "version|sha256|url" "${FORMULA_PATH}"
