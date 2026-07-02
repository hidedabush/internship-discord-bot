"""Load and save local configuration for the internship bot."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_DIR / "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "discord_channel_id": "",
    "scan_interval_minutes": 240,
    "auto_scan_enabled": True,
    "auto_scan_on_start": True,
    "max_posts_per_scan": 20,
    "include_keywords": [
        "software",
        "swe",
        "intern",
        "internship",
        "co-op",
        "data",
        "ai",
        "machine learning",
        "backend",
        "frontend",
        "cloud",
        "quant",
        "gpu",
        "cuda",
        "hardware",
    ],
    "exclude_keywords": [
        "senior",
        "staff",
        "principal",
        "full-time",
        "new grad",
    ],
}


def load_config() -> Dict[str, Any]:
    """Load config.json and .env, then return one merged config dictionary."""
    load_dotenv(ROOT_DIR / ".env")

    config = DEFAULT_CONFIG.copy()
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            user_config = json.load(f)
            config.update(user_config)

    # Environment variables win over config.json when present.
    if os.getenv("DISCORD_CHANNEL_ID"):
        config["discord_channel_id"] = os.getenv("DISCORD_CHANNEL_ID")
    if os.getenv("SCAN_INTERVAL_MINUTES"):
        config["scan_interval_minutes"] = int(os.getenv("SCAN_INTERVAL_MINUTES", "240"))
    if os.getenv("AUTO_SCAN_ENABLED"):
        config["auto_scan_enabled"] = os.getenv("AUTO_SCAN_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    if os.getenv("AUTO_SCAN_ON_START"):
        config["auto_scan_on_start"] = os.getenv("AUTO_SCAN_ON_START", "").strip().lower() in {"1", "true", "yes", "on"}
    if os.getenv("MAX_POSTS_PER_SCAN"):
        config["max_posts_per_scan"] = int(os.getenv("MAX_POSTS_PER_SCAN", "20"))

    config["discord_token"] = os.getenv("DISCORD_TOKEN", "")
    config["discord_guild_id"] = os.getenv("DISCORD_GUILD_ID", "")
    return config


def save_config(config: Dict[str, Any]) -> None:
    """Save non-secret config values back to config.json."""
    safe_config = {k: v for k, v in config.items() if k not in {"discord_token", "discord_guild_id"}}
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(safe_config, f, indent=2)
