# Homebrew formula template for claude-insights.
# Auto-updated by the release workflow via update-homebrew-formula.sh.
# Do not edit manually — changes will be overwritten on next release.
#
# Live formula: https://github.com/infowhere-be/homebrew-claude-insights

class ClaudeInsights < Formula
  desc "Real-time dashboard for Claude Code sessions"
  homepage "https://github.com/infowhere-be/claude-monitor"
  version "FORMULA_VERSION"
  license "MIT"

  on_macos do
    url "https://github.com/infowhere-be/claude-monitor/releases/download/vFORMULA_VERSION/claude-insights-macos-universal"
    sha256 "FORMULA_SHA256_UNIVERSAL"
  end

  def install
    bin.install "claude-insights-macos-universal" => "claude-insights"
  end

  def caveats
    <<~EOS
      To install the Claude Code hook, run:
        claude-insights install

      To start the dashboard:
        claude-insights start

      The dashboard will be available at http://localhost:4000
    EOS
  end

  test do
    assert_match "claude-insights", shell_output("#{bin}/claude-insights --version")
  end
end
