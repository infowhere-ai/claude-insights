"""Tests for context inspector endpoint."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")


def test_context_inspect_unknown_project_returns_404(app_client):
    r = app_client.get("/api/context-inspect?project=does-not-exist")
    assert r.status_code == 404


def test_context_inspect_known_project(app_client, tmp_project, tmp_path):
    from claude_monitor import config as config_module
    (tmp_project / "CLAUDE.md").write_text("# Project\n\nInstructions here.")
    rules_dir = tmp_project / ".claude" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    rule_file = rules_dir / "custom.md"
    rule_file.write_text("# Rule\n\nDo this.")

    encoded = str(tmp_project).replace("/", "-")
    jsonl_dir = tmp_path / "ci_proj" / encoded
    jsonl_dir.mkdir(parents=True)
    _write_jsonl(jsonl_dir / "sess.jsonl", [
        {
            "type": "assistant",
            "timestamp": "2026-01-01T10:00:00Z",
            "message": {
                "content": [{"type": "tool_use", "id": "t1", "name": "Read",
                             "input": {"file_path": str(tmp_project / "CLAUDE.md")}}],
                "usage": {"input_tokens": 10, "output_tokens": 5,
                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            },
        },
    ])

    with patch.object(config_module, "CLAUDE_PROJECTS_DIR", tmp_path / "ci_proj"):
        r = app_client.get("/api/context-inspect?project=my-project")
    assert r.status_code == 200
    body = r.json()
    assert "rules" in body
    assert "reads" in body
