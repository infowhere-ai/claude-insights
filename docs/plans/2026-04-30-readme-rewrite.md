# README Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the outdated README.md with a complete, public-facing README for infowhere-ai/claude-insights covering Homebrew/pipx/curl installation, hook setup, features with screenshots, and configuration.

**Architecture:** Single-file rewrite of `README.md`. No code changes — documentation only. All screenshots already exist in `docs/screenshots/`. The old `docs/screenshot.png` must be deleted.

**Spec:** `docs/specs/2026-04-30-readme-design.md`

**Tech Stack:** Markdown, GitHub-flavored HTML (centered header), shields.io badges

---

## File Map

| Action | File | Purpose |
|--------|------|---------|
| Rewrite | `README.md` | Complete replacement of existing README |
| Delete | `docs/screenshot.png` | Old hero image — outdated, replaced by `docs/screenshots/01-dashboard-overview.png` |

---

### Task 1: Verify all assets exist

Before writing, confirm every image referenced in the spec exists on disk.

**Files:**
- Read: `docs/screenshots/` (verify files)

- [ ] **Step 1: Confirm screenshots exist**

Run:
```bash
ls docs/screenshots/01-dashboard-overview.png \
   docs/screenshots/02-reasoning-live.png \
   docs/screenshots/03-session-context.png \
   docs/screenshots/07-git-panel.png \
   docs/screenshots/14-renewal-window.png \
   docs/logo.png
```
Expected: all 6 files listed, no "No such file" errors.

- [ ] **Step 2: Confirm CLI commands exist**

Run:
```bash
grep -n "def.*install\|def.*start\|def.*uninstall" claude_insights/cli.py
```
Expected: lines matching `install`, `start`, and `uninstall` subcommands.

---

### Task 2: Delete old screenshot

- [ ] **Step 1: Remove `docs/screenshot.png`**

```bash
rm docs/screenshot.png
```

---

### Task 3: Write the new README.md

**Files:**
- Rewrite: `README.md`

- [ ] **Step 1: Write the full README**

Complete content (copy exactly):

```markdown
<div align="center">
  <img src="docs/logo.png" alt="Claude Insights" width="220"/>

  # Claude Insights

  **Real-time dashboard for Claude Code sessions**

  Monitor what Claude is doing across all your projects — live status, token usage,
  session context, reasoning history, and git changes.

  ![Version](https://img.shields.io/badge/version-1.0.0--beta-blue)
  ![Python](https://img.shields.io/badge/python-3.10%2B-blue)
  ![License](https://img.shields.io/badge/license-MIT-green)
  [![PyPI](https://img.shields.io/pypi/v/claude-insights)](https://pypi.org/project/claude-insights/)

  *by [Leandro Siciliano](https://github.com/ltsiciliano) · InfoWhere*
</div>

---

![Claude Insights dashboard](docs/screenshots/01-dashboard-overview.png)
*Live session — WORKING state, all panels visible*

Claude Insights is a lightweight web dashboard that connects to Claude Code via hooks.
It shows live status, token usage, session context, reasoning blocks, and uncommitted
git changes — across all your projects simultaneously. Everything runs locally.
No data leaves your machine.

---

## Installation

**Homebrew** (macOS)

```bash
brew tap infowhere-ai/claude-insights
brew install claude-insights
```

**pipx** (macOS / Linux)

```bash
pipx install claude-insights
```

**curl** (one-liner, any platform)

```bash
curl -fsSL https://raw.githubusercontent.com/infowhere-ai/claude-insights/main/install.sh | bash
```

After installing, see [Quick Start](#quick-start) below to activate the hooks.

---

## Quick Start

1. **Activate hooks** — sets up the hook script at `~/.claude/hooks/monitor-hook.sh` and registers it for 5 Claude Code events in `~/.claude/settings.json`:

   ```bash
   claude-insights install
   ```

   > Existing hooks are never removed or modified.

2. **Restart Claude Code** — required for the hooks to take effect in open sessions.

3. **Start the dashboard**:

   ```bash
   claude-insights start
   ```

   Opens at **http://localhost:4000**

---

## How It Works

> Claude Code fires hooks at key moments — before and after each tool call, on
> notifications, on stop. Each hook writes a small JSON file to `.claude/status.json`
> inside the current project. Claude Insights watches those files and streams
> updates to the browser via Server-Sent Events.

```
Claude Code  →  hook fires  →  .claude/status.json  →  Claude Insights (SSE)  →  browser
```

---

## Features

| Area | What you see |
|------|-------------|
| **Live status** | Current state: working, waiting, compacting, idle — with the exact tool name |
| **Reasoning** | Claude's internal thinking live as it arrives; full history browsable |
| **Session context** | Context window breakdown: fixed rules, conversation, tool results — with token costs |
| **Token usage** | Input, output, cache reads — per session and 5-hour renewal window |
| **Commands** | Every tool call with path, duration, and success/failure |
| **To commit** | Uncommitted git changes; click any file for a side-by-side diff viewer |
| **Multi-project** | Monitors all projects under a root folder — auto-discovered |
| **Session history** | Browse past sessions; replay events and reasoning blocks |

<div align="center">
  <img src="docs/screenshots/02-reasoning-live.png" width="600" alt="Reasoning panel — live thinking stream"/>
  <p><em>Live reasoning stream — Claude's internal thinking as it arrives</em></p>
</div>

<div align="center">
  <img src="docs/screenshots/03-session-context.png" width="600" alt="Session context — token breakdown"/>
  <p><em>Context window breakdown by category with token costs</em></p>
</div>

<div align="center">
  <img src="docs/screenshots/07-git-panel.png" width="600" alt="Git panel — uncommitted files"/>
  <p><em>Uncommitted files — click any file to open the side-by-side diff viewer</em></p>
</div>

<div align="center">
  <img src="docs/screenshots/14-renewal-window.png" width="600" alt="5-hour token renewal window"/>
  <p><em>5-hour token renewal window tracker</em></p>
</div>

---

## Requirements

- Python 3.10+
- [Claude Code CLI](https://claude.ai/code) (`claude`) in PATH
- macOS or Linux
- Git

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `4000` | HTTP port for the dashboard |
| `PROJECTS_ROOT` | Set during `claude-insights install` | Root folder containing your project directories |

```bash
PORT=8080 PROJECTS_ROOT=~/code claude-insights start
```

Any directory under `PROJECTS_ROOT` that contains a `.claude/` folder is monitored automatically.

---

## Uninstall

```bash
claude-insights uninstall
```

Removes the hook script and deregisters hooks from `~/.claude/settings.json`.
Your other Claude Code hooks and settings are preserved.

---

## Development

For running from source:

```bash
git clone https://github.com/infowhere-ai/claude-insights.git
cd claude-insights
./install.sh        # sets up hooks
./run.sh start      # starts the server (source install default port: 19001, not 4000)
```

Stack: Python 3.10+ · FastAPI · SSE · Vanilla JS (no build step)

---

## License

MIT — © 2026 Leandro Siciliano
```

- [ ] **Step 2: Verify the file was written**

Run:
```bash
wc -l README.md
```
Expected: ~140 lines

---

### Task 4: Commit and push

- [ ] **Step 1: Stage changes**

```bash
git add README.md
git rm docs/screenshot.png
```

- [ ] **Step 2: Commit**

```bash
git commit -m "docs(readme): rewrite for public release with install methods and screenshots"
```

- [ ] **Step 3: Push**

```bash
git push origin develop
```

- [ ] **Step 4: Verify on GitHub**

Open the `develop` branch on GitHub and confirm:
- Logo renders in the header
- Hero screenshot (`01-dashboard-overview.png`) shows correctly
- All 4 feature screenshots render
- No broken image icons
- Badges display correctly
