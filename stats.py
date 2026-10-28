"""Статистика сообщений и топы (всё время / неделя / день).

Данные хранятся per-chat в stats.json рядом с кодом.
Счётчик работает через middleware: считает каждое сообщение в группах.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from aiogram import BaseMiddleware, Router, types
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name="stats")

_STORE = Path(__file__).with_name("stats.json")
_PRUNE_DAYS = 40      # сколько дней хранить дневные счётчики
_SAVE_EVERY = 20      # сохранять на диск раз в N сообщений

_data: dict[str, Any] | None = None
_dirty = 0


def _load() -> dict:
    try:
        with open(_STORE, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save(data: dict) -> None:
    try:
        with open(_STORE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    except Exception:  # noqa: BLE001
        pass


def _get_data() -> dict:
    global _data
    if _data is None:
        _data = _load()
    return _data


def _prune(data: dict) -> None:
    cutoff = (date.today() - timedelta(days=_PRUNE_DAYS)).isoformat()
    for chat in data.values():
        if not isinstance(chat, dict):
            continue
        for rec in chat.values():
            if not isinstance(rec, dict):
                continue
            days = rec.get("days")
            if not isinstance(days, dict):
                continue
            for key in [k for k in days if k < cutoff]:
                days.pop(key, None)


def flush() -> None:
    """Сбросить накопленный счётчик на диск."""
    global _dirty
    if _data is not None and _dirty:
        _prune(_data)
        _save(_data)
        _dirty = 0


def record(message: Message) -> None:
    """Учесть одно сообщение в группе."""
    global _dirty
    if message.chat.type not in ("group", "supergroup"):
        return
    user = message.from_user
    if not user or user.is_bot:
        return

    data = _get_data()
    chat = data.setdefault(str(message.chat.id), {})
    rec = chat.setdefault(
        str(user.id),
        {"name": "", "username": "", "total": 0, "days": {}},
    )
    if user.full_name:
        rec["name"] = user.full_name
    if user.username:
        rec["username"] = user.username
    rec["total"] = int(rec.get("total", 0)) + 1

    today = date.today().isoformat()
    days = rec.setdefault("days", {})
    days[today] = int(days.get(today, 0)) + 1

    _dirty += 1
    if _dirty >= _SAVE_EVERY:
        flush()


class StatsMiddleware(BaseMiddleware):
    """Считает сообщения до обработки хендлерами."""

    async def __call__(self, handler, event: Message, data: dict):
        try:
            record(event)
        except Exception:  # noqa: BLE001
            pass
        return await handler(event, data)


def _period_keys(period: str) -> set[str] | None:
    today = date.today()
    if period == "day":
        return {today.isoformat()}
    if period == "week":
        return {(today - timedelta(days=i)).isoformat() for i in range(7)}
    return None  # всё время


def top(chat_id: int, period: str = "all", limit: int = 10) -> list[dict]:
    chat = _get_data().get(str(chat_id), {})
    keys = _period_keys(period)
    rows: list[dict] = []
    for uid, rec in chat.items():
        if not isinstance(rec, dict):
            continue
        if keys is None:
            count = int(rec.get("total", 0))
        else:
            days = rec.get("days", {})
            count = sum(int(days.get(k, 0)) for k in keys)
        if count <= 0:
            continue
        rows.append(
            {
                "id": uid,
                "count": count,
                "username": rec.get("username") or "",
                "name": rec.get("name") or "",
            }
        )
    rows.sort(key=lambda r: (-r["count"], r["name"].lower()))
    return rows[:limit]


def _display(row: dict) -> str:
    if row["username"]:
        who = f"@{row['username']}"
    elif row["name"]:
        who = row["name"]
    else:
        who = row["id"]
    return who


_PERIOD_ALIASES = {
    "": "all",
    "все": "all",
    "всё": "all",
    "всего": "all",
    "all": "all",
    "день": "day",
    "дня": "day",
    "дней": "day",
    "дню": "day",
    "day": "day",
    "today": "day",
    "неделя": "week",
    "неделю": "week",
    "недели": "week",
    "week": "week",
}

_PERIOD_TITLE = {
    "all": "за всё время",
    "week": "за неделю",
    "day": "за день",
}


@router.message(Command("top", "топ"))
async def cmd_top(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        return await message.reply("Команда работает только в групповом чате.")

    parts = (message.text or "").split(maxsplit=1)
    arg = parts[1].strip().lower() if len(parts) > 1 else ""
    period = _PERIOD_ALIASES.get(arg)
    if period is None:
        return await message.reply(
            "Формат: /топ · /топ день · /топ неделя"
        )

    rows = top(message.chat.id, period, limit=10)
    if not rows:
        return await message.reply(
            f"Пока нет статистики {_PERIOD_TITLE[period]}."
        )

    lines = [f"Топ {_PERIOD_TITLE[period]}:"]
    for i, row in enumerate(rows, start=1):
        lines.append(f"{i}. {_display(row)} — {row['count']}")
    await message.reply("\n".join(lines))


@router.message(Command("mystats", "я"))
async def cmd_mystats(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        return await message.reply("Команда работает только в групповом чате.")
    user = message.from_user
    if not user:
        return

    chat = _get_data().get(str(message.chat.id), {})
    rec = chat.get(str(user.id), {})
    total = int(rec.get("total", 0))
    days = rec.get("days", {}) if isinstance(rec, dict) else {}
    today = date.today()
    day_count = int(days.get(today.isoformat(), 0))
    week_count = sum(
        int(days.get((today - timedelta(days=i)).isoformat(), 0))
        for i in range(7)
    )
    await message.reply(
        f"Ваша статистика:\n"
        f"за день: {day_count}\n"
        f"за неделю: {week_count}\n"
        f"всего: {total}"
    )


@router.message(Command("topreset"))
async def cmd_topreset(message: Message):
    # сброс статистики чата — только владелец/сеньор-админ проверяет main
    from main import has_perm  # локальный импорт, чтобы не было цикла

    if not await has_perm(message, "assign_junior_admin"):
        return await message.reply("Нет прав: нужен ранг не ниже старшего админа.")
    data = _get_data()
    data.pop(str(message.chat.id), None)
    flush()
    await message.reply("Статистика чата сброшена.")