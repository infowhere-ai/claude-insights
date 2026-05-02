"""Unit tests for roots config persistence."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from claude_monitor.app_config import service as config_service
from claude_monitor import state


class TestRootsConfig:
    def test_save_and_load_roundtrip(self, tmp_path):
        extra_dir = tmp_path / "extra"
        extra_dir.mkdir()
        original_extra = list(state._extra_roots)
        state._extra_roots[:] = [extra_dir]
        try:
            with patch.object(config_service, "_config_file", return_value=tmp_path / "roots.json"):
                config_service.save_roots_config()
            assert (tmp_path / "roots.json").exists()
            state._extra_roots.clear()
            with patch.object(config_service, "_config_file", return_value=tmp_path / "roots.json"):
                config_service.load_roots_config()
            assert extra_dir in state._extra_roots
        finally:
            state._extra_roots[:] = original_extra

    def test_load_ignores_missing_file(self, tmp_path):
        with patch.object(config_service, "_config_file", return_value=tmp_path / "nonexistent.json"):
            config_service.load_roots_config()
        assert isinstance(state._extra_roots, list)

    def test_load_filters_nonexistent_dirs(self, tmp_path):
        config_file = tmp_path / "roots.json"
        config_file.write_text(json.dumps({"extra_roots": ["/nonexistent/path/xyz"]}))
        original_extra = list(state._extra_roots)
        try:
            with patch.object(config_service, "_config_file", return_value=config_file):
                config_service.load_roots_config()
            assert Path("/nonexistent/path/xyz") not in state._extra_roots
        finally:
            state._extra_roots[:] = original_extra

    def test_save_handles_oserror_gracefully(self, tmp_path):
        readonly = tmp_path / "readonly"
        readonly.mkdir()
        readonly.chmod(0o444)
        config_file = readonly / "sub" / "roots.json"
        original_extra = list(state._extra_roots)
        state._extra_roots.clear()
        try:
            with patch.object(config_service, "_config_file", return_value=config_file):
                config_service.save_roots_config()  # should not raise
        finally:
            state._extra_roots[:] = original_extra
            readonly.chmod(0o755)

    def test_load_handles_invalid_json(self, tmp_path):
        config_file = tmp_path / "bad.json"
        config_file.write_text("not json")
        original_extra = list(state._extra_roots)
        try:
            with patch.object(config_service, "_config_file", return_value=config_file):
                config_service.load_roots_config()
            assert state._extra_roots == []
        finally:
            state._extra_roots[:] = original_extra
