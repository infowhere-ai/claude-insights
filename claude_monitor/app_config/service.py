"""Config persistence — extra roots (monitor-roots.json)."""

import json
from pathlib import Path

from claude_monitor import config, state


def _config_file() -> Path:
    return config.PROJECTS_ROOT / ".claude" / "monitor-roots.json"


def load_roots_config() -> None:
    try:
        cfg = _config_file()
        if cfg.exists():
            data = json.loads(cfg.read_text(encoding="utf-8"))
            state._extra_roots = [Path(p) for p in data.get("extra_roots", []) if Path(p).is_dir()]
    except Exception:
        state._extra_roots = []


def save_roots_config() -> None:
    try:
        cfg = _config_file()
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(
            json.dumps({"extra_roots": [str(p) for p in state._extra_roots]}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass
