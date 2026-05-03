"""E2E test fixtures — starts a real claude_monitor server and provides data helpers."""

import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(url: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            httpx.get(f"{url}/health", timeout=1)
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"Server at {url} did not become ready within {timeout}s")


# ── Server context ────────────────────────────────────────────────────────────

class ServerContext:
    """Provides the server URL and helpers to inject test data."""

    def __init__(self, url: str, projects_root: Path, claude_projects: Path):
        self.url = url
        self.projects_root = projects_root
        self.claude_projects = claude_projects

    # ── Status file helpers ───────────────────────────────────────────────────

    def write_status(self, project: str, status: str,
                     tool: str | None = None, extra: dict | None = None) -> None:
        data: dict = {
            "status": status,
            "state": status,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if tool:
            data["tool"] = tool
            data["current_action"] = {
                "hook": "PreToolUse", "tool": tool, "description": tool,
            }
        if extra:
            data.update(extra)
        path = self.projects_root / project / ".claude" / "status.json"
        path.write_text(json.dumps(data), encoding="utf-8")

    # ── JSONL helpers ─────────────────────────────────────────────────────────

    def jsonl_dir(self, project: str) -> Path:
        project_path = self.projects_root / project
        encoded = str(project_path).replace("/", "-")
        d = self.claude_projects / encoded
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_jsonl(self, project: str, entries: list[dict],
                    filename: str = "session.jsonl",
                    newest: bool = False) -> Path:
        d = self.jsonl_dir(project)
        if newest:
            # Remove all existing JSONL files so this is definitively the only/newest
            for old in d.glob("*.jsonl"):
                old.unlink(missing_ok=True)
        f = d / filename
        f.write_text(
            "\n".join(json.dumps(e) for e in entries), encoding="utf-8"
        )
        return f

    def assistant_entry(
        self,
        tool: str | None = None,
        thinking: str | None = None,
        input_tokens: int = 100,
        output_tokens: int = 50,
        cache_read: int = 0,
        cache_creation: int = 0,
        model: str = "claude-sonnet-4-6",
        ts: str | None = None,
    ) -> dict:
        content: list[dict] = []
        if thinking:
            content.append({"type": "thinking", "thinking": thinking})
        if tool:
            content.append({"type": "tool_use", "id": "t1", "name": tool,
                            "input": {"file_path": "/tmp/test.py"}})
        return {
            "type": "assistant",
            "timestamp": ts or datetime.now(timezone.utc).isoformat(),
            "message": {
                "model": model,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_creation,
                },
                "content": content,
            },
        }

    def user_tool_result_entry(self, tool_id: str = "t1",
                               content: str = "result",
                               is_error: bool = False,
                               ts: str | None = None) -> dict:
        return {
            "type": "user",
            "timestamp": ts or datetime.now(timezone.utc).isoformat(),
            "message": {
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": content,
                    "is_error": is_error,
                }]
            },
        }

    def make_project(self, name: str) -> Path:
        project = self.projects_root / name
        (project / ".claude").mkdir(parents=True, exist_ok=True)
        (project / ".claude" / "status.json").write_text(
            json.dumps({"status": "idle", "state": "idle",
                        "ts": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )
        return project


# ── Session-scoped server fixture ─────────────────────────────────────────────

@pytest.fixture(scope="session")
def server(tmp_path_factory):
    """Start a real claude_monitor server process. Shared across the test session."""
    projects_root = tmp_path_factory.mktemp("e2e_projects")
    claude_projects = tmp_path_factory.mktemp("e2e_claude_projects")
    db_path = tmp_path_factory.mktemp("e2e_db") / "test.db"
    port = _free_port()

    # Pre-create all test projects so they are discovered at server startup
    for name in ("test-project", "git-project"):
        p = projects_root / name
        (p / ".claude").mkdir(parents=True)
        (p / ".claude" / "status.json").write_text(
            json.dumps({"status": "idle", "state": "idle",
                        "ts": "2026-01-01T00:00:00Z"}),
            encoding="utf-8",
        )

    env = {
        **os.environ,
        "PROJECTS_ROOT": str(projects_root),
        "CLAUDE_PROJECTS_DIR": str(claude_projects),
        "CLAUDE_INSIGHTS_DB": str(db_path),
        "POLL_INTERVAL": "0.2",
        "DISCOVERY_INTERVAL": "2.0",
        "JSONL_ACTIVE_SECONDS": "60.0",
    }

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "claude_monitor.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "error"],
        env=env,
    )

    url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_server(url)
    except RuntimeError:
        proc.terminate()
        raise

    ctx = ServerContext(url=url, projects_root=projects_root,
                        claude_projects=claude_projects)
    yield ctx

    proc.terminate()
    proc.wait(timeout=5)


# ── Per-test project fixture ──────────────────────────────────────────────────

@pytest.fixture
def project(server: ServerContext):
    """Reset 'test-project' to idle before each test. Returns the project name."""
    name = "test-project"
    server.write_status(name, "idle")
    time.sleep(0.3)  # let poll_loop pick up the reset
    yield name


