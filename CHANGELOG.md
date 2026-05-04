# Changelog

All notable changes to this project will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

## [Unreleased]

## [1.0.0b1] - 2026-05-01

### Added
- Real-time dashboard for Claude Code sessions via Server-Sent Events (SSE)
- Token usage tracking (input, output, cache creation, cache read)
- Session and agent history with SQLite persistence
- Context inspector — shows rules, CLAUDE.md files, and reads loaded into context
- Git diff and pending changes viewer
- File browser and preview
- Integrated terminal (WebSocket PTY)
- Skills browser (reads `~/.claude/skills/`)
- Weekly stats and usage window
- Homebrew tap, pipx, and curl-install support
- macOS arm64 and Linux x86_64 standalone binaries
- Docker image with health check
- Playwright E2E tests

[Unreleased]: https://github.com/infowhere-ai/claude-insights/compare/v1.0.0b1...HEAD
[1.0.0b1]: https://github.com/infowhere-ai/claude-insights/releases/tag/v1.0.0b1
