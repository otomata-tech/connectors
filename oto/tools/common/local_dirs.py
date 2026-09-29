"""Local directories and config file used by connectors that keep local state.

Caches (Drive, Notion, Figma, Silae tokens), browser session files and the
optional `~/.otomata/config.yaml` blocks (e.g. `field_filters`) live here.
This module never resolves secrets: credentials are always passed in by the
consumer (see `oto.tools.common.credentials`).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict


def get_config_dir() -> Path:
    """Local config directory (`~/.otomata/`), created on demand."""
    config_dir = Path.home() / ".otomata"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_cache_dir() -> Path:
    """Local cache directory (`~/.cache/otomata/`), created on demand."""
    cache_dir = Path.home() / ".cache" / "otomata"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_sessions_dir() -> Path:
    """Browser sessions directory (`~/.otomata/sessions/`), created on demand."""
    sessions_dir = get_config_dir() / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return sessions_dir


@lru_cache(maxsize=1)
def _read_config() -> Dict[str, Any]:
    config_file = Path.home() / ".otomata" / "config.yaml"
    if not config_file.exists():
        return {}
    import yaml
    with open(config_file) as f:
        return yaml.safe_load(f) or {}


def get_config_section(key: str, default: Any = None) -> Any:
    """Read a named block of `~/.otomata/config.yaml` (`default` when absent)."""
    return _read_config().get(key, default)
