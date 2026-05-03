"""
Acceptance tests — Context Inspector (CA-06).

Dado que o projecto tem CLAUDE.md e ficheiros lidos na sessão
Quando uma sessão está activa e o context inspector é observado
Então as regras e ficheiros aparecem com tamanhos correctos
"""

import time

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import ServerContext

TIMEOUT = 5000


def _open_with_session(page: Page, server: ServerContext, project: str) -> None:
    """Navigate, select project, and wait for session to auto-select."""
    page.goto(f"{server.url}/insights")
    page.locator("#project-select").select_option(label=project)
    # Wait for session to be listed and auto-selected (triggers loadContextInspect)
    time.sleep(2)


def test_claude_md_listed_with_size(page: Page, server: ServerContext, project: str) -> None:
    """
    Dado que   o projecto tem CLAUDE.md com 400 bytes de conteúdo e uma sessão JSONL
    Quando     o dashboard carrega com a sessão activa
    Então      o CLAUDE.md aparece no context inspector
    """
    project_path = server.projects_root / project
    (project_path / "CLAUDE.md").write_text("A" * 400, encoding="utf-8")

    # JSONL is required so currentSessionId gets set → loadContextInspect fires
    server.write_jsonl(project, [server.assistant_entry(tool="Read")])

    _open_with_session(page, server, project)

    expect(page.locator("#ctx-inspect-body")).to_contain_text("CLAUDE", timeout=TIMEOUT)


def test_files_read_in_session_appear(page: Page, server: ServerContext, project: str) -> None:
    """
    Dado que   a sessão JSONL tem tool_use Read + result para um ficheiro
    Quando     o context inspector é observado
    Então      o ficheiro lido aparece na lista de reads
    """
    ts_use = "2026-01-01T10:00:00Z"
    ts_result = "2026-01-01T10:00:01Z"
    server.write_jsonl(project, [
        {
            "type": "assistant",
            "timestamp": ts_use,
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {"input_tokens": 10, "output_tokens": 5,
                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                "content": [{
                    "type": "tool_use", "id": "t1", "name": "Read",
                    "input": {"file_path": "/tmp/important_file.py"},
                }],
            },
        },
        {
            "type": "user",
            "timestamp": ts_result,
            "message": {
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": "def main(): pass\n" * 10,
                    "is_error": False,
                }]
            },
        },
    ])

    _open_with_session(page, server, project)

    expect(page.locator("#ctx-inspect-body")).to_contain_text(
        "important_file.py", timeout=TIMEOUT
    )
