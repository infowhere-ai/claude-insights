# Homebrew formula template for claude-insights.
# This file is auto-updated by the release workflow via update-homebrew-formula.sh.
# Do not edit manually — changes will be overwritten on next release.
#
# Live formula lives in: https://github.com/infowhere-be/homebrew-claude-insights

class ClaudeInsights < Formula
  desc "Real-time dashboard for Claude Code sessions"
  homepage "https://github.com/infowhere-be/claude-monitor"
  version "FORMULA_VERSION"
  license "MIT"

  on_macos do
    on_arm do
      url "https://github.com/infowhere-be/claude-monitor/releases/download/vFORMULA_VERSION/claude-insights-macos-arm64"
      sha256 "FORMULA_SHA256_ARM64"
    end

    on_intel do
      url "https://github.com/infowhere-be/claude-monitor/releases/download/vFORMULA_VERSION/claude-insights-macos-x86_64"
      sha256 "FORMULA_SHA256_X86_64"
    end
  end

  def install
    if Hardware::CPU.arm?
      bin.install "claude-insights-macos-arm64" => "claude-insights"
    else
      bin.install "claude-insights-macos-x86_64" => "claude-insights"
    end
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
