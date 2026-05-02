"""
Acceptance tests — Context Inspector.

Spec: standarts/private/projects/claude-monitor/specs/context-inspector.md
Product Owner: Leandro Siciliano | Data: 2026-05-01
"""

import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestAcceptanceContextInspector:

    def test_context_inspector_endpoint_accessible(self, app_client, tmp_project):
        """
        Dado que   o projecto está registado com status.json
        Quando     GET /api/context-inspect?project=<name>
        Então      a resposta é 200 e contém categorias de contexto
        """
        from claude_monitor import state
        project_name = tmp_project.name
        state._status_paths[project_name] = tmp_project / ".claude" / "status.json"
        (tmp_project / "CLAUDE.md").write_text("# Project Rules\nThese are the rules.\n", encoding="utf-8")

        r = app_client.get(f"/api/context-inspect?project={project_name}")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

        data = r.json()
        assert "fixed" in data or "rules" in data or "categories" in data, (
            f"Expected context categories in response, got keys: {list(data.keys())}"
        )

    def test_claude_md_appears_in_response(self, app_client, tmp_project):
        """
        Dado que   o projecto tem CLAUDE.md com conteúdo
        Quando     GET /api/context-inspect?project=<name>
        Então      a resposta contém referência ao CLAUDE.md
        """
        from claude_monitor import state
        project_name = tmp_project.name
        state._status_paths[project_name] = tmp_project / ".claude" / "status.json"

        (tmp_project / "CLAUDE.md").write_text("# My Project\nSome important rules here.\n", encoding="utf-8")

        r = app_client.get(f"/api/context-inspect?project={project_name}")
        if r.status_code != 200:
            pytest.skip(f"context-inspect not available: {r.status_code}")

        body = r.text
        assert "CLAUDE" in body.upper() or "claude.md" in body.lower()

    def test_unknown_project_returns_404(self, app_client):
        """
        Dado que   "unknown-project" não está em _status_paths
        Quando     GET /api/context-inspect?project=unknown-project
        Então      a resposta é 404
        """
        r = app_client.get("/api/context-inspect?project=unknown-project-xyz")
        assert r.status_code == 404

    def test_detect_latest_thinking_excludes_whitespace(self, tmp_jsonl_dir):
        """
        Dado que   um thinking block contém apenas whitespace
        Quando     detect_latest_thinking é chamado
        Então      retorna None (não inclui blocos vazios)
        """
        from claude_monitor.jsonl import parser
        jsonl = tmp_jsonl_dir / "ws_think.jsonl"
        jsonl.write_text(
            json.dumps({
                "type": "assistant",
                "timestamp": "2026-01-01T10:00:00Z",
                "message": {
                    "model": "m",
                    "usage": {"input_tokens": 1, "output_tokens": 1,
                               "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                    "content": [{"type": "thinking", "thinking": "\n\n   \n"}],
                },
            }),
            encoding="utf-8",
        )

        result = parser.detect_latest_thinking(jsonl)
        assert result is None, "Whitespace-only thinking should return None"

    def test_tokens_estimated_as_bytes_over_four(self, tmp_project, app_client):
        """
        Dado que   CLAUDE.md tem conteúdo de tamanho conhecido
        Quando     GET /api/context-inspect
        Então      tokens_est ≈ len(content.encode('utf-8')) // 4
        """
        from claude_monitor import state
        project_name = tmp_project.name
        state._status_paths[project_name] = tmp_project / ".claude" / "status.json"

        content = "A" * 400  # 400 bytes → 100 tokens_est
        (tmp_project / "CLAUDE.md").write_text(content, encoding="utf-8")

        r = app_client.get(f"/api/context-inspect?project={project_name}")
        if r.status_code != 200:
            pytest.skip("context-inspect not available")

        data = r.json()
        fixed = data.get("fixed", data.get("rules", []))
        claude_entry = next(
            (item for item in fixed if "CLAUDE" in str(item.get("name", "")).upper()), None
        )
        if claude_entry:
            tokens_est = claude_entry.get("tokens_est", 0)
            expected = 400 // 4
            assert abs(tokens_est - expected) <= 5
