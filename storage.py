"""Единое хранилище состояния (JSON) с атомарной записью.

Все данные бота (ранги, варны, статистика, настройки) пишутся через этот модуль.
Каталог задаётся переменной окружения STATE_DIR — на хостинге с постоянным
диском укажите туда путь к volume, иначе данные живут рядом с кодом.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from config import config


def _base() -> Path:
    if config.state_dir:
        base = Path(config.state_dir).expanduser()
    else:
        base = Path(__file__).resolve().parent
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return base


def path_for(name: str) -> Path:
    return _base() / name


def load(name: str, default: dict | None = None) -> dict:
    try:
        with open(path_for(name), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return dict(default or {})
    except Exception:  # noqa: BLE001
        return dict(default or {})


def save(name: str, data: dict) -> None:
    """Атомарная запись: сначала во временный файл, затем os.replace."""
    target = path_for(name)
    try:
        fd, tmp = tempfile.mkstemp(
            dir=str(target.parent), prefix=target.name + ".", suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, target)
    except Exception:  # noqa: BLE001
        try:
            with open(target, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        except Exception:  # noqa: BLE001
            pass