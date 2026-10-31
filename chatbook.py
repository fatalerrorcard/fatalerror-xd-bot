"""Учёт известных чатов бота (группы и каналы).

Bot API не отдаёт список чатов, где состоит бот, поэтому бот сам
запоминает каждый чат, в котором видит апдейты (сообщения, события
участников), и хранит его в chatbook.json.
"""

from __future__ import annotations

import time

import storage

_FILE = "chatbook.json"


def _load() -> dict:
    return storage.load(_FILE, {})


def _save(data: dict) -> None:
    storage.save(_FILE, data)


def remember(chat_id: int, chat_type: str, title: str = "") -> bool:
    """Записать чат в список известных. Возвращает True, если чат новый."""
    data = _load()
    key = str(chat_id)
    is_new = key not in data
    data[key] = {
        "id": chat_id,
        "type": chat_type,  # group / supergroup / channel
        "title": title or "",
        "first_seen": int(time.time()),
    }
    _save(data)
    return is_new


def known_chats() -> dict:
    """Все известные чаты: {chat_id: record}."""
    return _load()


def announce_targets() -> list[dict]:
    """Чаты, куда можно рассылать объявления: группы, супергруппы, каналы."""
    result = []
    for key, rec in _load().items():
        if not isinstance(rec, dict):
            continue
        ctype = rec.get("type")
        if ctype not in ("group", "supergroup", "channel"):
            continue
        result.append(
            {
                "id": rec.get("id", int(key) if key.lstrip("-").isdigit() else key),
                "type": ctype,
                "title": rec.get("title", ""),
            }
        )
    return result