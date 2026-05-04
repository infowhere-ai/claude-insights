# claude-insights

Real-time dashboard for Claude Code sessions — shows live status, token usage, session context, git changes, and reasoning history.

## Stack

- Python 3.10+ + FastAPI + SSE
- Vanilla HTML/JS (no framework, no build step)
- Claude Code hooks write `.claude/status.json`; the app reads and broadcasts via SSE

## Architecture

```
Claude Code  →  hook fires  →  .claude/status.json  →  FastAPI (SSE)  →  browser
```

The backend (`claude_monitor/`) polls project directories under `PROJECTS_ROOT` for `.claude/` directories and streams updates via Server-Sent Events. Entry point: `claude_monitor/main.py`. The frontend (`static/insights.html`) is a single-file dashboard with no build step.

`site/` is a separate landing page deployed to Cloudflare Pages — not part of the app.

## Package structure

```
claude_monitor/        # FastAPI application package
├── main.py            # app creation, middleware, routers, startup (≤100 lines)
├── config.py          # env vars and constants
├── state.py           # shared mutable in-memory state
├── db.py              # SQLite persistence
├── core/              # broadcast, SSE, background loops, basic pages
├── projects/          # /api/status + project discovery
├── jsonl/             # JSONL session file parsing
├── sessions/          # /api/sessions + agent/session persistence
├── stats/             # /api/insights-stats, weekly-stats, usage-window
├── git_ops/           # /api/diff, /api/pending
├── skills/            # /api/skills
├── files/             # /api/browse, file-preview, DELETE /api/file
├── app_config/        # /api/config, /api/claude-md
├── account/           # /api/account
├── context/           # /api/context-inspect
└── terminal/          # WebSocket /ws/terminal
```

## Git Scopes

- `app` — FastAPI backend (`claude_monitor/`)
- `frontend` — HTML/JS dashboard (`static/`)
- `config` — deployment and configuration
