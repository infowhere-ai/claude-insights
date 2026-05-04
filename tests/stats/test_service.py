"""Unit tests for stats service helpers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from claude_monitor.stats import service as stats_service


class TestParseTokenEntry:
    def test_extracts_token_counts_from_assistant_entry(self):
        entry = {
            "type": "assistant",
            "message": {
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 20,
                    "cache_creation_input_tokens": 5,
                }
            },
        }
        result = stats_service._parse_token_entry(entry)
        assert result["input"] == 100
        assert result["output"] == 50
        assert result["cache_read"] == 20
        assert result["ctx"] == 125  # 100 + 20 + 5

    def test_returns_zeros_for_non_assistant_entry(self):
        entry = {"type": "user", "message": {}}
        result = stats_service._parse_token_entry(entry)
        assert result is None

    def test_returns_zeros_for_missing_usage(self):
        entry = {"type": "assistant", "message": {}}
        result = stats_service._parse_token_entry(entry)
        assert result["input"] == 0
        assert result["output"] == 0
        assert result["cache_read"] == 0
        assert result["ctx"] == 0

    def test_extracts_model_from_entry(self):
        entry = {
            "type": "assistant",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {},
            },
        }
        result = stats_service._parse_token_entry(entry)
        assert result["model"] == "claude-sonnet-4-6"

    def test_returns_empty_model_when_not_present(self):
        entry = {"type": "assistant", "message": {"usage": {}}}
        result = stats_service._parse_token_entry(entry)
        assert result["model"] == ""
