"""
Loading the settings files (services/app_config.py)

The app reads two YAML files, in this order:

  1. the main settings file: `CONFIG_PATH`, or `config.yaml` in the repo root.
     It holds shared defaults that suit anyone (no names, no home folders).
  2. your own settings, laid on top: `LOCAL_CONFIG_PATH`, or `config.local.yaml`
     next to the main file. Git ignores it. Put your people, folders and server
     address there. `config.local.example.yaml` shows what goes in it.

"Laid on top" means: a section in your own file (say `torrent:`) changes only the
keys you write in it; the rest of that section keeps the main file's values. A
list (say `media_folders`) is replaced as a whole.

Every script that needs settings should call `load_config()` so they all see the
same values.
"""

from __future__ import annotations

import copy
import os
from typing import Any, Dict, Optional

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_NAME = "config.local.yaml"


def config_path() -> str:
    """The main settings file the app uses."""
    return os.getenv("CONFIG_PATH") or os.path.join(ROOT, "config.yaml")


def local_config_path(main_path: Optional[str] = None) -> str:
    """Your own settings file: `LOCAL_CONFIG_PATH`, else `config.local.yaml` next to the main file."""
    return os.getenv("LOCAL_CONFIG_PATH") or os.path.join(
        os.path.dirname(os.path.abspath(main_path or config_path())), LOCAL_NAME
    )


def merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    """`extra` laid over `base`: mappings merge key by key, anything else is replaced."""
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _read(path: str) -> Dict[str, Any]:
    if not path or not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"[config] {path} must be a YAML mapping (key: value lines).")
    return data


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """The main settings file with your own settings laid on top. Missing files count as empty."""
    main = path or config_path()
    return merge(_read(main), _read(local_config_path(main)))


def setting(config: Dict[str, Any], dotted: str, default: Any = None) -> Any:
    """`setting(cfg, "recommender.engine")`: a value by its dotted name, `default` when missing."""
    node: Any = config
    for part in dotted.split("."):
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return default
    return node
