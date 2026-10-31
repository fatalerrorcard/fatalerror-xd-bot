"""Репутация участников: плюсы и минусы за сообщения.

Правила:
- ставить можно только ответом на сообщение;
- себе ставить нельзя;
- суточный лимит на одного дающего (REP_DAILY_LIMIT, по умолчанию 10);
- счёт хранится per-chat.
"""

from __future__ import annotations

from datetime import date

import storage

_FILE = "reputation.json"


def _load() -> dict:
    return storage.load(_FILE, {})


def _save(data: dict) -> None:
    storage.save(_FILE, data)


def _chat(data: dict, chat_id: int) -> dict:
    return data.setdefault(str(chat_id), {})


def _rec(chat: dict, user_id: int) -> dict:
    return chat.setdefault(
        str(user_id), {"score": 0, "given": {}, "name": "", "username": ""}
    )


def score(chat_id: int, user_id: int) -> int:
    rec = _chat(_load(), chat_id).get(str(user_id)) or {}
    try:
        return int(rec.get("score", 0))
    except (TypeError, ValueError):
        return 0


def given_today(chat_id: int, user_id: int) -> int:
    rec = _chat(_load(), chat_id).get(str(user_id)) or {}
    given = rec.get("given") or {}
    try:
        return int(given.get(date.today().isoformat(), 0))
    except (TypeError, ValueError):
        return 0


def give(
    chat_id: int,
    giver_id: int,
    target_id: int,
    delta: int,
    limit: int,
    target_name: str = "",
    target_username: str = "",
) -> tuple[bool, str, int]:
    """Поставить оценку. Возвращает (успех, пояснение, новый счёт цели)."""
    if giver_id == target_id:
        return False, "Себе репутацию ставить нельзя.", 0

    data = _load()
    chat = _chat(data, chat_id)

    giver = _rec(chat, giver_id)
    today = date.today().isoformat()
    given = giver.get("given")
    if not isinstance(given, dict):
        given = {}
        giver["given"] = given
    used = int(given.get(today, 0))
    if used >= limit:
        return (
            False,
            f"Лимит на сегодня исчерпан ({limit} в сутки).",
            score(chat_id, target_id),
        )

    target = _rec(chat, target_id)
    if target_name:
        target["name"] = target_name
    if target_username:
        target["username"] = target_username
    try:
        current = int(target.get("score", 0))
    except (TypeError, ValueError):
        current = 0
    target["score"] = current + delta

    given[today] = used + 1
    giver["given"] = given

    _save(data)
    return True, "", current + delta


def top(chat_id: int, limit: int = 10) -> list[dict]:
    chat = _chat(_load(), chat_id)
    rows: list[dict] = []
    for uid, rec in chat.items():
        if not isinstance(rec, dict):
            continue
        try:
            value = int(rec.get("score", 0))
        except (TypeError, ValueError):
            continue
        if value == 0:
            continue
        rows.append(
            {
                "id": uid,
                "score": value,
                "name": rec.get("name") or "",
                "username": rec.get("username") or "",
            }
        )
    rows.sort(key=lambda r: (-r["score"], r["name"].lower()))
    return rows[:limit]


def places(chat_id: int) -> dict[str, int]:
    """Позиция пользователя в топе (1-based) по всем ненулевым счетам."""
    chat = _chat(_load(), chat_id)
    values: list[tuple[int, str]] = []
    for uid, rec in chat.items():
        if not isinstance(rec, dict):
            continue
        try:
            value = int(rec.get("score", 0))
        except (TypeError, ValueError):
            continue
        if value != 0:
            values.append((value, uid))
    values.sort(key=lambda x: -x[0])
    return {uid: index + 1 for index, (_v, uid) in enumerate(values)}