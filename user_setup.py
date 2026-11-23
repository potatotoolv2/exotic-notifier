"""Saved bot settings on this PC (token + CSV)."""
from __future__ import annotations

import json
import os

from licensing import data_dir


def config_path() -> str:
    return os.path.join(data_dir(), "user_config.json")


def load_config() -> dict:
    path = config_path()
    if not os.path.isfile(path):
        return {}
    try:
        return json.loads(open(path, "r", encoding="utf-8").read())
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    path = config_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def run_wizard(force: bool = False) -> dict:
    """Console fallback. The GUI launcher is the normal setup path."""
    cfg = load_config()
    if not force:
        return cfg
    token = (os.getenv("DISCORD_TOKEN") or cfg.get("discord_token") or "").strip()
    csv_path = (os.getenv("DATABASE_FILE") or cfg.get("database_file") or "").strip()
    return {
        "discord_token": token,
        "database_file": csv_path,
        "license_key": cfg.get("license_key") or "",
    }
