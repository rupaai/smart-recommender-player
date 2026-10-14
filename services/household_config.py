"""Load household-specific profile data separately from general app settings."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml

from services.nl_people import DEFAULT_GROUP_LABEL


def load_household(path: str | None = None) -> Dict[str, Any]:
    """Read HOUSEHOLD_CONFIG_PATH (or household.yaml) if present."""
    path = path or os.getenv("HOUSEHOLD_CONFIG_PATH")
    if not path:
        config_path = os.getenv("CONFIG_PATH", "config.yaml")
        path = str(Path(config_path).resolve().parent / "household.yaml")
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}
    household = data.get("household", {})
    if not isinstance(household, dict):
        raise ValueError("household.yaml: household must be a mapping")
    profiles = household.get("profiles", [])
    if not isinstance(profiles, list):
        raise ValueError("household.yaml: household.profiles must be a list")
    seen_ids, seen_keys = set(), set()
    for i, profile in enumerate(profiles, 1):
        if not isinstance(profile, dict):
            raise ValueError(f"household.yaml: profile #{i} must be a mapping")
        try:
            profile_id = int(profile["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"household.yaml: profile #{i} needs a numeric id") from exc
        key, name, user = (str(profile.get(k, "")).strip() for k in ("key", "name", "jellyfin_user_id"))
        if profile_id < 1 or not key or not name or not user:
            raise ValueError(f"household.yaml: profile #{i} needs a positive id, key, name and Jellyfin user")
        if profile_id in seen_ids or key.lower() in seen_keys:
            raise ValueError(f"household.yaml: profile #{i} duplicates an id or key")
        seen_ids.add(profile_id)
        seen_keys.add(key.lower())
    group = household.get("group") or {}
    if not isinstance(group, dict):
        raise ValueError("household.yaml: household.group must be a mapping")
    group_ids = group.get("profile_ids", [])
    try:
        valid_group_ids = isinstance(group_ids, list) and all(int(i) in seen_ids for i in group_ids)
    except (TypeError, ValueError):
        valid_group_ids = False
    if not valid_group_ids:
        raise ValueError("household.yaml: group.profile_ids must refer to configured profiles")
    return household


def merge_household(config: Dict[str, Any], household: Dict[str, Any]) -> Dict[str, Any]:
    """Expose configured profiles through the existing Northern Lights people adapter."""
    if not household:
        return config
    config["household"] = household
    nl = dict(config.get("northern_lights") or {})
    group = household.get("group") or {}
    nl["group_label"] = str(group.get("name") or DEFAULT_GROUP_LABEL).strip()[:40] or DEFAULT_GROUP_LABEL
    nl["group_profile_ids"] = [int(i) for i in group.get("profile_ids", [])]
    nl["people"] = [
        {
            "id": p["key"], "name": p["name"],
            "jellyfin_user_id": p["jellyfin_user_id"], "profile_id": int(p["id"]),
        }
        for p in household.get("profiles", [])
    ]
    config["northern_lights"] = nl
    return config


def load_machine_config(config: Dict[str, Any], path: str | None = None) -> Dict[str, Any]:
    """Overlay local filesystem paths from machine.yaml, when present."""
    path = path or os.getenv("MACHINE_CONFIG_PATH")
    if not path:
        config_path = os.getenv("CONFIG_PATH", "config.yaml")
        path = str(Path(config_path).resolve().parent / "machine.yaml")
    if not os.path.isfile(path):
        return config
    with open(path, "r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}
    machine = data.get("machine", {})
    if not isinstance(machine, dict):
        raise ValueError("machine.yaml: machine must be a mapping")
    for key in ("media_folders", "jellyfin_library"):
        if key in machine:
            config[key] = machine[key]
    if "torrent" in machine:
        if not isinstance(machine["torrent"], dict):
            raise ValueError("machine.yaml: machine.torrent must be a mapping")
        torrent = dict(config.get("torrent") or {})
        torrent.update(machine["torrent"])
        config["torrent"] = torrent
    return config
