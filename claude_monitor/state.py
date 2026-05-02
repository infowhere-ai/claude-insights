"""In-memory application state — single source of truth for all mutable globals."""
import asyncio
from pathlib import Path

projects: dict[str, dict] = {}
_status_paths: dict[str, Path] = {}
_mtimes: dict[str, float] = {}
_sse_clients: list[asyncio.Queue] = []
_pending_projects: list[str] = []

_project_events: dict[str, list] = {}

_jsonl_mtimes: dict[str, float] = {}
_project_stats_cache: dict[str, dict] = {}

_jsonl_cache: dict[str, dict] = {}
_agents_dir_mtimes: dict[str, float] = {}
_persisted_agent_ids: dict[str, set] = {}
_thinking_cache: dict[str, dict] = {}

_extra_roots: list[Path] = []
