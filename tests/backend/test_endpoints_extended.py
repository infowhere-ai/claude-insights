"""Extended endpoint tests covering subprocess-backed and data-rich endpoints.

Covers /api/diff, /api/pending, /api/sessions, /api/session-detail,
/api/insights-stats, /api/usage-window, /api/weekly-stats, /api/account,
and error/timeout branches.
"""

import json
import sys
import time
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_completed(stdout: str = "", returncode: int = 0) -> CompletedProcess:
    cp = MagicMock(spec=CompletedProcess)
    cp.stdout = stdout
    cp.stderr = ""
    cp.returncode = returncode
    return cp


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")


def _assistant_entry(tool: str = "Read", input_tokens: int = 100,
                     output_tokens: int = 50) -> dict:
    return {
        "type": "assistant",
        "timestamp": "2026-01-01T10:00:00Z",
        "message": {
            "model": "claude-sonnet-4-6",
            "content": [{"type": "tool_use", "id": "t1", "name": tool, "input": {}}],
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": 20,
                "cache_creation_input_tokens": 0,
            },
        },
    }


# ── /api/diff ──────────────────────────────────────────────────────────────────

class TestDiffEndpoint:
    def _create_file(self, project: Path, name: str, content: str = "x = 1\n") -> Path:
        f = project / name
        f.write_text(content)
        return f

    def test_diff_returns_modified_file(self, app_client, tmp_project):
        self._create_file(tmp_project, "app.py")
        diff_output = "diff --git a/app.py b/app.py\n-old\n+new\n"
        with patch("app.subprocess.run", return_value=_make_completed(stdout=diff_output)):
            r = app_client.get("/api/diff?project=my-project&file=app.py")
        assert r.status_code == 200
        assert r.json()["diff"] == diff_output.strip()
        assert r.json()["is_new"] is False

    def test_diff_falls_back_to_staged(self, app_client, tmp_project):
        self._create_file(tmp_project, "new.py")
        staged_diff = "diff --git a/new.py b/new.py\n+added\n"
        calls = [_make_completed(stdout=""), _make_completed(stdout=staged_diff)]
        with patch("app.subprocess.run", side_effect=calls):
            r = app_client.get("/api/diff?project=my-project&file=new.py")
        assert r.status_code == 200
        assert r.json()["diff"] == staged_diff.strip()

    def test_diff_untracked_file(self, app_client, tmp_project):
        self._create_file(tmp_project, "untracked.py")
        new_file_diff = "+++ b/untracked.py\n+new content\n"
        ls_result = _make_completed(stdout="", returncode=1)
        untracked_diff = _make_completed(stdout=new_file_diff)
        calls = [_make_completed(stdout=""), _make_completed(stdout=""), ls_result, untracked_diff]
        with patch("app.subprocess.run", side_effect=calls):
            r = app_client.get("/api/diff?project=my-project&file=untracked.py")
        assert r.status_code == 200
        assert r.json()["is_new"] is True

    def test_diff_timeout_returns_504(self, app_client, tmp_project):
        import subprocess
        self._create_file(tmp_project, "app.py")
        with patch("app.subprocess.run", side_effect=subprocess.TimeoutExpired(["git"], 10)):
            r = app_client.get("/api/diff?project=my-project&file=app.py")
        assert r.status_code == 504

    def test_diff_exception_returns_500(self, app_client, tmp_project):
        self._create_file(tmp_project, "app.py")
        with patch("app.subprocess.run", side_effect=RuntimeError("git not found")):
            r = app_client.get("/api/diff?project=my-project&file=app.py")
        assert r.status_code == 500


# ── /api/pending ──────────────────────────────────────────────────────────────

class TestPendingEndpoint:
    def test_pending_returns_modified_files(self, app_client, tmp_project):
        porcelain = " M app.py\n?? untracked.py\n"
        with patch("app.subprocess.run", return_value=_make_completed(stdout=porcelain)):
            r = app_client.get("/api/pending?project=my-project")
        assert r.status_code == 200
        files = r.json()["files"]
        labels = [f["label"] for f in files]
        assert "modified" in labels
        assert "untracked" in labels

    def test_pending_renamed_file(self, app_client, tmp_project):
        porcelain = "R  old.py -> new.py\n"
        with patch("app.subprocess.run", return_value=_make_completed(stdout=porcelain)):
            r = app_client.get("/api/pending?project=my-project")
        assert r.status_code == 200
        files = r.json()["files"]
        assert any("new.py" in f["rel_path"] for f in files)

    def test_pending_git_error_returns_empty(self, app_client, tmp_project):
        with patch("app.subprocess.run", return_value=_make_completed(stdout="", returncode=128)):
            r = app_client.get("/api/pending?project=my-project")
        assert r.status_code == 200
        assert r.json()["files"] == []

    def test_pending_timeout_returns_504(self, app_client, tmp_project):
        import subprocess
        with patch("app.subprocess.run", side_effect=subprocess.TimeoutExpired(["git"], 10)):
            r = app_client.get("/api/pending?project=my-project")
        assert r.status_code == 504

    def test_pending_exception_returns_500(self, app_client, tmp_project):
        with patch("app.subprocess.run", side_effect=RuntimeError("unexpected")):
            r = app_client.get("/api/pending?project=my-project")
        assert r.status_code == 500

    def test_pending_empty_porcelain_returns_empty(self, app_client, tmp_project):
        with patch("app.subprocess.run", return_value=_make_completed(stdout="")):
            r = app_client.get("/api/pending?project=my-project")
        assert r.status_code == 200
        assert r.json()["files"] == []


# ── /api/sessions ─────────────────────────────────────────────────────────────

class TestSessionsEndpoint:
    def test_sessions_known_project_returns_list(self, app_client, tmp_project):
        with patch("app._list_sessions", return_value=[
            {"session_id": "abc123", "is_active": True, "started_at": "2026-01-01T10:00:00Z"}
        ]):
            r = app_client.get("/api/sessions?project=my-project")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert data[0]["session_id"] == "abc123"


# ── /api/session-detail ───────────────────────────────────────────────────────

class TestSessionDetailEndpoint:
    def test_session_detail_with_valid_jsonl(self, app_client, tmp_project, tmp_path):
        import app as app_module
        project_path = tmp_project
        encoded = str(project_path).replace("/", "-")
        jsonl_dir = tmp_path / "claude_proj" / encoded
        jsonl_dir.mkdir(parents=True)
        jsonl_file = jsonl_dir / "test_sess.jsonl"
        _write_jsonl(jsonl_file, [_assistant_entry("Read")])

        with patch.object(app_module, "CLAUDE_PROJECTS_DIR", tmp_path / "claude_proj"):
            r = app_client.get("/api/session-detail?project=my-project&session_id=test_sess")
        assert r.status_code == 200
        body = r.json()
        assert "tools" in body
        assert "thinking" in body
        assert "stats" in body

    def test_session_detail_missing_jsonl_returns_404(self, app_client, tmp_project, tmp_path):
        import app as app_module
        with patch.object(app_module, "CLAUDE_PROJECTS_DIR", tmp_path / "nonexistent"):
            r = app_client.get("/api/session-detail?project=my-project&session_id=ghost")
        assert r.status_code == 404


# ── /api/insights-stats ───────────────────────────────────────────────────────

class TestInsightsStatsEndpoint:
    def _setup_jsonl_dir(self, tmp_path, project_path: Path) -> Path:
        encoded = str(project_path).replace("/", "-")
        jsonl_dir = tmp_path / "claude_p" / encoded
        jsonl_dir.mkdir(parents=True)
        return jsonl_dir

    def test_insights_stats_with_data(self, app_client, tmp_project, tmp_path):
        import app as app_module
        jsonl_dir = self._setup_jsonl_dir(tmp_path, tmp_project)
        f = jsonl_dir / "sess.jsonl"
        _write_jsonl(f, [
            _assistant_entry("Read", input_tokens=200, output_tokens=100),
            _assistant_entry("Write", input_tokens=150, output_tokens=80),
        ])

        with patch.object(app_module, "CLAUDE_PROJECTS_DIR", tmp_path / "claude_p"):
            r = app_client.get("/api/insights-stats?project=my-project")
        assert r.status_code == 200
        body = r.json()
        assert body["sessions_count"] >= 1
        assert body["total_tokens"] > 0

    def test_insights_stats_no_jsonl_dir(self, app_client, tmp_project, tmp_path):
        import app as app_module
        with patch.object(app_module, "CLAUDE_PROJECTS_DIR", tmp_path / "nonexistent"):
            r = app_client.get("/api/insights-stats?project=my-project")
        assert r.status_code == 200
        body = r.json()
        assert body["sessions_count"] == 0
        assert body["total_tokens"] == 0

    def test_insights_stats_calculates_top_tool(self, app_client, tmp_project, tmp_path):
        import app as app_module
        jsonl_dir = self._setup_jsonl_dir(tmp_path, tmp_project)
        # 3 Read calls, 1 Write
        entries = [_assistant_entry("Read")] * 3 + [_assistant_entry("Write")]
        _write_jsonl(jsonl_dir / "sess.jsonl", entries)

        with patch.object(app_module, "CLAUDE_PROJECTS_DIR", tmp_path / "claude_p"):
            r = app_client.get("/api/insights-stats?project=my-project")
        assert r.json()["top_tool"] == "Read"
        assert r.json()["top_tool_count"] == 3


# ── /api/usage-window ─────────────────────────────────────────────────────────

class TestUsageWindowEndpoint:
    def test_usage_window_with_data(self, app_client, tmp_project, tmp_path):
        import app as app_module
        encoded = str(tmp_project).replace("/", "-")
        jsonl_dir = tmp_path / "claude_uw" / encoded
        jsonl_dir.mkdir(parents=True)
        f = jsonl_dir / "sess.jsonl"
        _write_jsonl(f, [_assistant_entry("Read", input_tokens=500, output_tokens=200)])

        with patch.object(app_module, "CLAUDE_PROJECTS_DIR", tmp_path / "claude_uw"):
            r = app_client.get("/api/usage-window?project=my-project")
        assert r.status_code == 200
        body = r.json()
        assert body["window_tokens"] > 0
        assert body["sessions_in_window"] == 1
        assert "remaining_secs" in body
        assert "elapsed_pct" in body

    def test_usage_window_no_jsonl_dir(self, app_client, tmp_project, tmp_path):
        import app as app_module
        with patch.object(app_module, "CLAUDE_PROJECTS_DIR", tmp_path / "nonexistent"):
            r = app_client.get("/api/usage-window?project=my-project")
        assert r.status_code == 200
        body = r.json()
        assert body["window_tokens"] == 0
        assert body["sessions_in_window"] == 0

    def test_usage_window_ignores_old_sessions(self, app_client, tmp_project, tmp_path):
        import app as app_module
        encoded = str(tmp_project).replace("/", "-")
        jsonl_dir = tmp_path / "claude_uw2" / encoded
        jsonl_dir.mkdir(parents=True)
        f = jsonl_dir / "old.jsonl"
        _write_jsonl(f, [_assistant_entry("Read", input_tokens=999)])
        # Set mtime to 6 hours ago (beyond 5-hour window)
        old_time = time.time() - 6 * 3600
        import os
        os.utime(f, (old_time, old_time))

        with patch.object(app_module, "CLAUDE_PROJECTS_DIR", tmp_path / "claude_uw2"):
            r = app_client.get("/api/usage-window?project=my-project")
        assert r.json()["sessions_in_window"] == 0


# ── /api/weekly-stats ─────────────────────────────────────────────────────────

class TestWeeklyStatsEndpoint:
    def test_weekly_stats_with_data(self, app_client, tmp_project):
        import app as app_module
        weekly_data = {"total_input": 1000, "total_output": 500}
        weekly_file = tmp_project / ".claude" / "weekly_tokens.json"
        weekly_file.write_text(json.dumps(weekly_data))
        app_module._status_paths["my-project"] = tmp_project / ".claude" / "status.json"

        r = app_client.get("/api/weekly-stats")
        assert r.status_code == 200
        assert "weekly" in r.json()


# ── /api/account ──────────────────────────────────────────────────────────────

class TestAccountEndpoint:
    def test_account_returns_expected_structure(self, app_client):
        mock_data = {
            "model": "claude-sonnet-4-6",
            "enabled_plugins": [],
            "daily_activity": [],
            "tokens_week": {"input": 5000, "output": 2000, "cache_creation": 100, "cache_read": 800},
            "service_tier": "standard",
        }
        with patch("app._get_account_sync", return_value=mock_data):
            r = app_client.get("/api/account")
        assert r.status_code == 200
        body = r.json()
        assert body["model"] == "claude-sonnet-4-6"
        assert "tokens_week" in body
        assert body["tokens_week"]["input"] == 5000

    def test_account_sync_reads_settings(self, tmp_path):
        import app as app_module
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir()
        (settings_dir / "settings.json").write_text(
            json.dumps({"model": "claude-opus-4-7", "enabledPlugins": {"mcp-tool": True}}),
        )
        with patch("app.Path.home", return_value=tmp_path):
            result = app_module._get_account_sync()
        assert result["model"] == "claude-opus-4-7"
        assert "mcp-tool" in result["enabled_plugins"]

    def test_account_sync_handles_missing_settings(self, tmp_path):
        import app as app_module
        with patch("app.Path.home", return_value=tmp_path):
            result = app_module._get_account_sync()
        assert result["model"] == "unknown"
        assert result["tokens_week"]["input"] == 0

    def test_account_sync_aggregates_tokens(self, tmp_path):
        import app as app_module
        projects_dir = tmp_path / ".claude" / "projects" / "my-proj"
        projects_dir.mkdir(parents=True)
        jsonl_file = projects_dir / "sess.jsonl"
        _write_jsonl(jsonl_file, [{
            "type": "assistant",
            "message": {
                "usage": {
                    "input_tokens": 300,
                    "output_tokens": 150,
                    "cache_creation_input_tokens": 10,
                    "cache_read_input_tokens": 50,
                    "service_tier": "priority",
                }
            }
        }])
        with patch("app.Path.home", return_value=tmp_path):
            result = app_module._get_account_sync()
        assert result["tokens_week"]["input"] == 300
        assert result["tokens_week"]["output"] == 150
        assert result["service_tier"] == "priority"

    def test_account_sync_reads_stats_cache(self, tmp_path):
        import app as app_module
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "stats-cache.json").write_text(
            json.dumps({"dailyActivity": [{"date": "2026-01-01", "messages": 10}]})
        )
        with patch("app.Path.home", return_value=tmp_path):
            result = app_module._get_account_sync()
        assert len(result["daily_activity"]) == 1
        assert result["daily_activity"][0]["date"] == "2026-01-01"

    def test_account_sync_skips_old_jsonl(self, tmp_path):
        import app as app_module
        import os
        projects_dir = tmp_path / ".claude" / "projects" / "old-proj"
        projects_dir.mkdir(parents=True)
        old_f = projects_dir / "old.jsonl"
        _write_jsonl(old_f, [{"type": "assistant", "message": {"usage": {"input_tokens": 999}}}])
        old_time = time.time() - 8 * 24 * 3600
        os.utime(old_f, (old_time, old_time))

        with patch("app.Path.home", return_value=tmp_path):
            result = app_module._get_account_sync()
        assert result["tokens_week"]["input"] == 0


# ── /api/skills ───────────────────────────────────────────────────────────────

class TestSkillsEndpoint:
    def test_skills_with_skill_md_file(self, app_client, tmp_path):
        skills_dir = tmp_path / ".claude" / "skills" / "my-skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("---\nname: My Skill\ndescription: Does things\n---\n\nBody text.")

        with patch("pathlib.Path.home", return_value=tmp_path):
            r = app_client.get("/api/skills")
        assert r.status_code == 200
        skills = r.json()["skills"]
        assert any(s["name"] == "my-skill" for s in skills)

    def test_skills_handles_unreadable_file(self, app_client, tmp_path):
        skills_dir = tmp_path / ".claude" / "skills" / "broken-skill"
        skills_dir.mkdir(parents=True)
        skill_file = skills_dir / "SKILL.md"
        skill_file.write_text("content")

        with patch("pathlib.Path.home", return_value=tmp_path):
            with patch("app._parse_skill_md", side_effect=Exception("parse error")):
                r = app_client.get("/api/skills")
        assert r.status_code == 200


# ── /api/browse ───────────────────────────────────────────────────────────────

class TestBrowseEndpoint:
    def test_browse_permission_error(self, app_client, tmp_path):
        import stat
        restricted = tmp_path / "restricted"
        restricted.mkdir()
        restricted.chmod(0o000)
        try:
            r = app_client.get(f"/api/browse?path={restricted}")
            assert r.status_code == 403
        finally:
            restricted.chmod(0o755)


# ── /api/config — edge cases ──────────────────────────────────────────────────

class TestConfigEdgeCases:
    def test_add_primary_root_as_extra_is_rejected(self, app_client, tmp_projects_root):
        r = app_client.post("/api/config/roots", json={"action": "add", "path": str(tmp_projects_root)})
        assert r.status_code == 400
        assert "already the primary" in r.json()["error"]


# ── /api/claude-md — extra roots ─────────────────────────────────────────────

class TestClaudeMdExtraRoots:
    def test_claude_md_found_in_extra_root(self, app_client, tmp_path):
        import app as app_module
        extra_root = tmp_path / "extra"
        project = extra_root / "extra-project"
        project.mkdir(parents=True)
        (project / "CLAUDE.md").write_text("# Extra Project\n\nContent here.")

        original_extras = list(app_module._extra_roots)
        app_module._extra_roots.append(extra_root)
        try:
            r = app_client.get("/api/claude-md?project=extra-project")
            assert r.status_code == 200
            assert "Extra Project" in r.json()["content"]
        finally:
            app_module._extra_roots[:] = original_extras


# ── /api/file DELETE — success path ──────────────────────────────────────────

class TestDeleteFileEndpoint:
    def test_delete_untracked_file_succeeds(self, app_client, tmp_project):
        untracked = tmp_project / "to_delete.txt"
        untracked.write_text("temp content")
        with patch("app.subprocess.run", return_value=_make_completed(stdout="", returncode=1)):
            r = app_client.delete(f"/api/file?project=my-project&path={untracked}")
        assert r.status_code == 200
        assert "deleted" in r.json()
        assert not untracked.exists()

    def test_delete_nonexistent_file_returns_404(self, app_client, tmp_project):
        r = app_client.delete("/api/file?project=my-project&path=nonexistent.txt")
        assert r.status_code == 404

    def test_delete_timeout_returns_504(self, app_client, tmp_project):
        import subprocess
        f = tmp_project / "some.txt"
        f.write_text("x")
        with patch("app.subprocess.run", side_effect=subprocess.TimeoutExpired(["git"], 5)):
            r = app_client.delete(f"/api/file?project=my-project&path={f}")
        assert r.status_code == 504

    def test_delete_path_outside_project_returns_400(self, app_client, tmp_project, tmp_path):
        outside = tmp_path / "outside.txt"
        outside.write_text("x")
        r = app_client.delete(f"/api/file?project=my-project&path={outside}")
        assert r.status_code == 400


# ── /api/context-inspect ─────────────────────────────────────────────────────

class TestContextInspectEndpoint:
    def test_context_inspect_known_project(self, app_client, tmp_project, tmp_path):
        import app as app_module
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

        with patch.object(app_module, "CLAUDE_PROJECTS_DIR", tmp_path / "ci_proj"):
            r = app_client.get("/api/context-inspect?project=my-project")
        assert r.status_code == 200
        body = r.json()
        assert "rules" in body
        assert "reads" in body
