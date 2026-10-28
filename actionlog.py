"""Журнал действий модерации: отправка событий в привязанный чат/канал."""

from __future__ import annotations

from datetime import datetime

from aiogram import Bot

import settings


def _short(user) -> str:
    if user is None:
        return "?"
    name = user.full_name or "—"
    if user.username:
        return f"{name} (@{user.username})"
    return name


async def log_action(bot: Bot, source_chat_id: int, action: str, text: str) -> None:
    """Отправить запись в журнал, привязанный к чату-источнику."""
    target = settings.log_chat(source_chat_id)
    if not target:
        return
    stamp = datetime.now().strftime("%d.%m %H:%M")
    line = f"{stamp} · {action} · {text}"
    try:
        await bot.send_message(target, line, disable_web_page_preview=True)
    except Exception:  # noqa: BLE001
        pass


def actor(user) -> str:
    """Подпись модератора для журнала."""
    return _short(user)


def target(user) -> str:
    """Подпись нарушителя для журнала."""
    return _short(user)