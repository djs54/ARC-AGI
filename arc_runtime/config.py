"""ARC-owned configuration loader."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict


def load_config(config_path: str | Path | None = None) -> Dict[str, Any]:
    """Load ARC config from an explicit path or standard local fallbacks."""
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib

    if config_path:
        explicit = Path(config_path)
        if not explicit.exists():
            raise FileNotFoundError(f"config file not found at {explicit}")
        with explicit.open("rb") as handle:
            config = tomllib.load(handle)
        config["_config_path"] = str(explicit)
        return config

    search_paths = [
        Path.cwd() / "campy.toml",
        Path.cwd() / "sidequests.toml",
        Path.home() / ".campy" / "config.toml",
        Path.home() / ".sidequests" / "config.toml",
    ]
    for path in search_paths:
        if path.exists():
            with path.open("rb") as handle:
                config = tomllib.load(handle)
            config["_config_path"] = str(path)
            return config

    raise FileNotFoundError("No ARC/HippoCampy config file found. Expected campy.toml or sidequests.toml")

