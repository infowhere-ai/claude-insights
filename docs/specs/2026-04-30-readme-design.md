# README Design — claude-insights public repo

> Date: 2026-04-30
> Repo: infowhere-ai/claude-insights
> Status: Approved (post-review v2)

---

## Goal

Rewrite the existing README for the public `infowhere-ai/claude-insights` repo. The current README is minimal and focused on `git clone`. The new README must serve as the primary entry point for any developer who discovers the project, covering all supported installation methods, a clear explanation of how hooks work, and enough screenshots to demonstrate value.

---

## Audience

Developers who actively use Claude Code CLI but may not be familiar with the hooks mechanism. The README must be actionable for a complete newcomer to hooks while remaining concise enough for experienced users to skim quickly.

---

## Structure

### 1. Header

- Centered logo (`docs/logo.png`)
- Title: `Claude Insights`
- Tagline: `Real-time dashboard for Claude Code sessions`
- Badges: version, Python 3.10+, license (MIT), PyPI
- Author attribution: `by Leandro Siciliano`

### 2. Hero Screenshot

Full-width image: `docs/screenshots/01-dashboard-overview.png`
Caption: "Live session — WORKING state, all panels visible"

### 3. Pitch (3 sentences max)

What it is, what problem it solves, privacy guarantee:

> Claude Insights is a lightweight web dashboard that connects to Claude Code via hooks. It shows live status, token usage, session context, reasoning blocks, and uncommitted git changes — across all your projects simultaneously. Everything runs locally. No data leaves your machine.

### 4. Installation

Three methods presented with equal visual weight, no hierarchy between Homebrew and pipx:

#### Homebrew (macOS)
```bash
brew tap infowhere-ai/claude-insights
brew install claude-insights
```

#### pipx (macOS / Linux)
```bash
pipx install claude-insights
```

#### curl (one-liner, any platform)
```bash
curl -fsSL https://raw.githubusercontent.com/infowhere-ai/claude-insights/main/install.sh | bash
```

Note below the three methods: "After installing, see Quick Start below to activate the hooks."

### 5. Quick Start

Three numbered steps — clear and minimal:

1. **Activate hooks** — `claude-insights install`
   Sets up the hook script at `~/.claude/hooks/monitor-hook.sh` and registers it for 5 Claude Code events in `~/.claude/settings.json`. Existing hooks are never removed or modified.
2. **Restart Claude Code** — required for the hooks to take effect in open sessions
3. **Start the dashboard** — `claude-insights start` (opens at http://localhost:4000)

### 6. How It Works

Brief mechanism explanation (2-3 lines for hook newcomers) + ASCII diagram:

> Claude Code fires hooks at key moments — before and after each tool call, on notifications, on stop. Each hook call writes a small JSON file to `.claude/status.json` inside the current project. Claude Insights watches those files and streams updates to the browser via Server-Sent Events.

```
Claude Code  →  hook fires  →  .claude/status.json  →  Claude Insights (SSE)  →  browser
```

### 7. Features

Feature table + 4 inline screenshots:

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

Screenshots (inline, after the table):

- `docs/screenshots/02-reasoning-live.png` — Reasoning panel, live thinking block
- `docs/screenshots/03-session-context.png` — Context window token breakdown
- `docs/screenshots/07-git-panel.png` — Uncommitted files list (click any file to open the diff viewer)
- `docs/screenshots/14-renewal-window.png` — 5-hour token renewal window

Each screenshot: max width 600px, with a one-line caption.

### 8. Requirements

- Python 3.10+
- Claude Code CLI (`claude`) in PATH
- macOS or Linux
- Git

### 9. Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `4000` | HTTP port for the dashboard |
| `PROJECTS_ROOT` | Set during `claude-insights install` | Root folder containing your project directories |

```bash
PORT=8080 PROJECTS_ROOT=~/code claude-insights start
```

Auto-discovery: any directory under `PROJECTS_ROOT` that contains a `.claude/` folder is monitored automatically.

### 10. Uninstall

```bash
claude-insights uninstall
```

Removes the hook script and deregisters hooks from `settings.json`. Your other Claude Code hooks and settings are preserved.

### 11. Development / Contributing

For running from source:

```bash
git clone https://github.com/infowhere-ai/claude-insights.git
cd claude-insights
./install.sh        # sets up hooks
./run.sh start      # starts the server (equivalent to claude-insights start for source installs)
```

Stack: Python 3.10+ · FastAPI · SSE · Vanilla JS (no build step)

### 12. License

MIT — © 2026 Leandro Siciliano

---

## Screenshot Usage

| Position | File | Caption |
|----------|------|---------|
| Hero (full width) | `01-dashboard-overview.png` | Live session — WORKING state |
| Feature: Reasoning | `02-reasoning-live.png` | Live reasoning stream |
| Feature: Context | `03-session-context.png` | Token breakdown by category |
| Feature: Git | `07-git-panel.png` | Uncommitted files — click to open diff viewer |
| Feature: Renewal | `14-renewal-window.png` | 5-hour token window tracker |

---

## Implementation Notes

- The README replaces the existing `README.md` at repo root (complete rewrite)
- Screenshots are referenced as relative paths — they must exist in `docs/screenshots/` in the public repo
- `docs/logo.png` exists at the repo root level — use this path, not `static/logo.png`
- Default port for `claude-insights start` (CLI) is **4000** — `run.sh` (source path) defaults to 19001; the README covers the CLI path
- `claude-insights install`, `claude-insights start`, `claude-insights uninstall` are exposed by `claude_insights/cli.py` — verify these commands exist and work before writing
- The Homebrew tap is configured in `.github/workflows/release.yml` (step "Update Homebrew formula") — it is published as part of the release process
- The curl URL points to the `main` branch of the public repo; verify the raw URL is correct
- Do not add a Table of Contents — the README is short enough to scan without one
- Badges use shields.io — use static badges for version; PyPI badge can use the live endpoint
- **Delete `docs/screenshot.png`** — old hero image (outdated dashboard), referenced in the current README. The new hero is `docs/screenshots/01-dashboard-overview.png`
