"""Антиспам / флуд-контроль и приветствие для новых участников."""

from __future__ import annotations

import time
from collections import defaultdict

from aiogram import Bot, Router, types
from aiogram.filters import Command

from config import config
from moderation import mute_user

router = Router(name="antispam")

# user_id -> list[timestamps]
_flood: dict[int, list[float]] = defaultdict(list)


def _is_flood(user_id: int) -> bool:
    now = time.time()
    stamps = [t for t in _flood[user_id] if now - t < config.flood_window_sec]
    stamps.append(now)
    _flood[user_id] = stamps
    return len(stamps) > config.flood_trigger


@router.message(Command("floodtest"))
async def _flood_test(message: types.Message):
    # служебная: чтобы проверить логику, не вызывая реальный мут
    await message.reply(f"окно={config.flood_window_sec}, порог={config.flood_trigger}")


@router.message()
async def flood_check(message: types.Message):
    chat = message.chat
    if chat.type == "private":
        return
    user = message.from_user
    if not user or user.is_bot:
        return
    if _is_flood(user.id):
        # админов не мутим
        try:
            member = await message.bot.get_chat_member(chat.id, user.id)
            if member.status in ("administrator", "creator"):
                return
        except Exception:
            pass
        try:
            await message.delete()
        except Exception:
            pass
        if config.flood_mute_min:
            try:
                await mute_user(
                    message.bot, chat.id, user.id, config.flood_mute_min
                )
                await message.answer(
                    f"Система: флуд-защита. {user.full_name} в муте "
                    f"на {config.flood_mute_min} мин."
                )
            except Exception as e:  # noqa: BLE001
                await message.answer(f"Не удалось замутить: {e}")


@router.message()
async def welcome_new_member(message: types.Message):
    if message.chat.type == "private":
        return
    new = message.new_chat_members
    if not new or not config.welcome_enabled:
        return
    for member in new:
        if member.is_bot:
            continue
        name = member.full_name or member.username or "гость"
        try:
            await message.answer(
                config.welcome_text.format(name=name), disable_web_page_preview=True
            )
        except Exception:
            text = config.welcome_text.replace("{name}", name)
            await message.answer(text, disable_web_page_preview=True)