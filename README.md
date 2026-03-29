# Claude Monitor

Real-time dashboard for [Claude Code](https://claude.ai/code) sessions. See what Claude is doing across all your projects — current status, active agents, changed files, git diffs, token usage, and a full event log.

![Claude Monitor screenshot](https://github.com/user-attachments/assets/placeholder)

## Features

- **Live status** — see Claude's current action in real time via SSE (no polling)
- **Multi-project** — monitors all projects under a root folder simultaneously
- **Active agents** — shows sub-agents spawned by Claude, their description and state
- **Changed files** — lists uncommitted files with git status and inline diff viewer
- **Event log** — full history of tool calls, file edits, and bash commands per session
- **Token usage** — weekly token breakdown (input, output, cache) across all projects
- **Account tab** — daily activity chart and model info from your Claude settings
- **Skills viewer** — browse available Claude Code skills from `~/.claude/skills/`
- **CLAUDE.md viewer** — read the project instructions loaded in each session
- **Terminal** — embedded Claude Code terminal (requires `claude` in PATH)
- **PWA** — installable as a standalone desktop app (no browser chrome)
- **Dark theme** — Catppuccin Mocha palette

## How it works

Claude Code writes its current state to `.claude/status.json` in each project directory. Claude Monitor watches those files and streams updates to the browser via [Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events).

You need to configure Claude Code hooks to write the status file. See [Hook setup](#hook-setup) below.

## Requirements

- Python 3.10+
- Claude Code CLI (`claude`) installed and configured
- macOS or Linux (the terminal feature uses PTY — not supported on Windows)

## Quick start

```bash
git clone https://github.com/infowhere-be/claude-monitor.git
cd claude-monitor
./run.sh start
```

The first run creates a virtual environment and installs dependencies automatically. The app starts on **http://localhost:19001**.

```
./run.sh start     # start in background
./run.sh stop      # stop
./run.sh restart   # restart
./run.sh status    # check if running
```

## Hook setup

Claude Monitor reads `.claude/status.json` files written by Claude Code hooks. Add the following hooks to your `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "bash -c 'PROJECT_DIR=\"$(pwd)\"; STATUS_FILE=\"$PROJECT_DIR/.claude/status.json\"; mkdir -p \"$(dirname \"$STATUS_FILE\")\"; TOOL=$(echo \"$CLAUDE_TOOL_INPUT\" | python3 -c \"import json,sys; d=json.load(sys.stdin); print(d.get(\\\"tool_name\\\",\\\"\\\"))\" 2>/dev/null || echo \"unknown\"); echo \"{\\\"status\\\":\\\"working\\\",\\\"tool\\\":\\\"$TOOL\\\",\\\"ts\\\":\\\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\\\"}\" > \"$STATUS_FILE\"'"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "bash -c 'PROJECT_DIR=\"$(pwd)\"; STATUS_FILE=\"$PROJECT_DIR/.claude/status.json\"; mkdir -p \"$(dirname \"$STATUS_FILE\")\"; echo \"{\\\"status\\\":\\\"idle\\\",\\\"ts\\\":\\\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\\\"}\" > \"$STATUS_FILE\"'"
          }
        ]
      }
    ]
  }
}
```

> **Note:** For richer status data (file paths, commands, event log), you can extend the hook script to include more fields from `$CLAUDE_TOOL_INPUT`. The monitor displays whatever JSON fields are present in `status.json`.

## Configuration

Environment variables (set before running `./run.sh start` or in Docker):

| Variable | Default | Description |
|----------|---------|-------------|
| `PROJECTS_ROOT` | `~/projects` | Root folder containing your project directories |
| `PORT` | `19001` | HTTP port |
| `POLL_INTERVAL` | `1.0` | How often (seconds) to check for file changes |
| `DISCOVERY_INTERVAL` | `60.0` | How often (seconds) to scan for new projects |
| `BUILD_DATE` | today's date | Build date shown in the preferences footer |

Example:

```bash
PROJECTS_ROOT=~/code PORT=8080 ./run.sh start
```

You can also add extra project roots directly in the UI via **Settings → Monitored folders → Add folder**.

## Docker

```bash
docker compose up -d
```

Edit `docker-compose.yml` to set `PROJECTS_ROOT` to the path containing your projects:

```yaml
environment:
  - PROJECTS_ROOT=/projects
volumes:
  - /path/to/your/projects:/projects:ro
```

The volume is mounted read-only — Claude Monitor never writes to your project files.

## Install as PWA (desktop app)

Claude Monitor is a Progressive Web App. To install it as a standalone window without browser chrome:

1. Open **http://localhost:19001** in Chrome or Edge
2. Click the install icon (⊕) in the address bar, or open the browser menu and choose **Install Claude Monitor**
3. The app opens in its own window and appears in your dock/taskbar

## Project structure

```
claude-monitor/
├── app.py          # FastAPI backend — SSE, git diff, config API
├── static/
│   ├── index.html  # Single-page frontend (vanilla JS, no build step)
│   ├── manifest.json
│   ├── sw.js
│   ├── icon-192.png
│   └── icon-512.png
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── run.sh          # Start/stop helper script
```

## License

MIT
