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

The backend (`app.py`) polls project directories under `PROJECTS_ROOT` for `.claude/` directories and streams updates via Server-Sent Events. The frontend (`static/insights.html`) is a single-file dashboard with no build step.

## Git Scopes

- `app` — FastAPI backend (SSE, git diff, context inspector)
- `frontend` — HTML/JS dashboard
- `config` — deployment and configuration
