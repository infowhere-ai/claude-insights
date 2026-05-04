"""CLI entry point for claude-insights.

Commands:
    claude-insights start [--port N] [--host H]   Start the dashboard
    claude-insights install                        Install the Claude Code hook
    claude-insights uninstall                      Remove the Claude Code hook
    claude-insights --version                      Print version
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("claude-insights")
    except Exception:
        from claude_insights import __version__

        return __version__


def _pkg_dir() -> Path:
    """Directory where this package is installed (contains app.py, static/, etc.)."""
    return Path(__file__).parent


def _find(name: str) -> Path:
    """Locate a bundled file — installed package first, then cwd fallback for dev."""
    candidate = _pkg_dir() / name
    if candidate.exists():
        return candidate
    fallback = Path.cwd() / name
    if fallback.exists():
        return fallback
    raise FileNotFoundError(f"{name} not found in package ({_pkg_dir()}) or cwd ({Path.cwd()})")


def _cmd_start(host: str, port: int) -> None:
    try:
        import uvicorn
    except ImportError:
        print("uvicorn is not installed. Run: pip install 'uvicorn[standard]'", file=sys.stderr)
        sys.exit(1)

    print(f"claude-insights {_version()} — http://{host}:{port}/insights")
    uvicorn.run(
        "claude_monitor.main:app",
        host=host,
        port=port,
    )


def _cmd_install() -> None:
    subprocess.run(["bash", str(_find("install.sh"))], check=True)


def _cmd_uninstall() -> None:
    subprocess.run(["bash", str(_find("install.sh")), "--uninstall"], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="claude-insights",
        description="Real-time dashboard for Claude Code sessions.",
    )
    parser.add_argument("--version", action="version", version=f"claude-insights {_version()}")

    sub = parser.add_subparsers(dest="command", metavar="command")

    start_p = sub.add_parser("start", help="Start the dashboard server")
    start_p.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", "4000")),
        help="Port to listen on (default: 4000, env: PORT)",
    )
    start_p.add_argument(
        "--host",
        default=os.getenv("HOST", "127.0.0.1"),
        help="Host to bind to (default: 127.0.0.1, env: HOST)",
    )

    sub.add_parser("install", help="Install the Claude Code hook into ~/.claude/settings.json")
    sub.add_parser("uninstall", help="Remove the Claude Code hook from ~/.claude/settings.json")

    args = parser.parse_args()

    if args.command == "start":
        _cmd_start(args.host, args.port)
    elif args.command == "install":
        _cmd_install()
    elif args.command == "uninstall":
        _cmd_uninstall()
    else:
        parser.print_help()
