"""claude-insights — real-time dashboard for Claude Code sessions."""

try:
    from importlib.metadata import version, PackageNotFoundError

    __version__ = version("claude-insights")
except PackageNotFoundError:
    __version__ = "dev"
