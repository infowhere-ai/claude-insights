# Contributing to claude-insights

Thank you for your interest in contributing!

## Development Setup

```bash
git clone https://github.com/infowhere-ai/claude-insights
cd claude-insights

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -e ".[dev]"
playwright install chromium
```

## Running the App Locally

```bash
python -m uvicorn claude_monitor.main:app --reload --port 4000
# Open http://localhost:4000
```

## Running Tests

```bash
# Unit + integration tests
pytest tests/ --ignore=tests/e2e -v

# E2E tests (requires the app to be running)
pytest tests/e2e/ -v --timeout=30

# With coverage
pytest tests/ --ignore=tests/e2e --cov=claude_monitor
```

## Code Style

This project uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting.

```bash
ruff check .           # lint
ruff format .          # format
ruff format --check .  # check formatting without applying
```

## Pull Request Guidelines

- One feature or fix per PR
- Tests must pass: `pytest tests/ --ignore=tests/e2e`
- Code must be formatted: `ruff format --check .`
- Update `CHANGELOG.md` under `[Unreleased]`
- Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `chore:`

## Project Structure

```
claude_monitor/        # FastAPI backend — one directory per domain
├── main.py            # App creation, middleware, routers, lifespan (≤100 lines)
├── config.py          # Environment variables and constants
├── core/              # SSE, background loops, shared utilities
├── sessions/          # Session and agent history
├── stats/             # Token usage stats
├── projects/          # Project discovery and status
└── ...

claude_insights/       # CLI entry point (claude-insights command)
static/                # Frontend — single HTML file, no build step
tests/                 # Mirrors domain structure; acceptance/ and e2e/ subdirectories
site/                  # Landing page (Cloudflare Pages) — not part of the package
```

## Reporting Issues

Use the [GitHub issue tracker](https://github.com/infowhere-ai/claude-insights/issues).
For security vulnerabilities, see [SECURITY.md](SECURITY.md).
