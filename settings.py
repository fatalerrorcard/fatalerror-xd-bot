"""Настройки чата: журнал действий, правила и прочее."""

from __future__ import annotations

import storage

_FILE = "settings.json"


def _all() -> dict:
    return storage.load(_FILE, {})


def get(section: str, chat_id: int, default=None):
    return _all().get(section, {}).get(str(chat_id), default)


def set_value(section: str, chat_id: int, value) -> None:
    data = _all()
    data.setdefault(section, {})[str(chat_id)] = value
    storage.save(_FILE, data)


def unset(section: str, chat_id: int) -> None:
    data = _all()
    bucket = data.get(section, {})
    bucket.pop(str(chat_id), None)
    data[section] = bucket
    storage.save(_FILE, data)


def log_chat(chat_id: int):
    return get("logs", chat_id)


def set_log_chat(chat_id: int, target) -> None:
    set_value("logs", chat_id, target)


def rules(chat_id: int) -> str | None:
    return get("rules", chat_id)


def set_rules(chat_id: int, text: str) -> None:
    set_value("rules", chat_id, text)