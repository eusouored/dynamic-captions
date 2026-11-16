"""Pastas do usuário: %LOCALAPPDATA%\\DynamicCaptions no Windows, ~/.local/share/DynamicCaptions fora dele."""

from __future__ import annotations

import os
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("SMARTCAPTION_HOME") or os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    path = Path(base) if os.environ.get("SMARTCAPTION_HOME") else Path(base) / "DynamicCaptions"
    path.mkdir(parents=True, exist_ok=True)
    return path
