"""Модуль модерации чатов.

Права администратора группы обязательны для бана/мута/удаления/закрепления.
Бот не может удалять/банить сообщения выше своих прав.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from aiogram import Bot, types
from aiogram.exceptions import TelegramBadRequest

from config import config


# --- Права проверить заранее не выходит, поэтому просто обработчик ошибок ---


async def is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Возвращает True, если бот является администратором чата."""
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id, me.id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


async def ban_user(bot: Bot, chat_id: int, user_id: int) -> str:
    await bot.ban_chat_member(chat_id, user_id)
    return "Пользователь заблокирован (бан)."


async def unban_user(bot: Bot, chat_id: int, user_id: int) -> str:
    await bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
    return "Бан снят."


async def kick_user(bot: Bot, chat_id: int, user_id: int) -> str:
    await bot.ban_chat_member(chat_id, user_id)
    await bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
    return "Пользователь исключён (кик)."


async def mute_user(bot: Bot, chat_id: int, user_id: int, minutes: int = 0) -> str:
    """Мут на minutes минут; 0 - навсегда (до размута)."""
    if minutes > 0:
        until = datetime.now() + timedelta(minutes=minutes)
    else:
        until = datetime.now() + timedelta(days=366)
    await bot.restrict_chat_member(
        chat_id,
        user_id,
        types.ChatPermissions(can_send_messages=False),
        until_date=until,
    )
    return f"Пользователь в муте на {minutes if minutes else 'неопределённый'} мин."


async def unmute_user(bot: Bot, chat_id: int, user_id: int) -> str:
    await bot.restrict_chat_member(
        chat_id,
        user_id,
        types.ChatPermissions(
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
        ),
    )
    return "Мут снят."


async def clear_reply(bot: Bot, chat_id: int, reply: types.Message) -> str:
    """Удаляет сообщение, на которое ответили, и сообщение с командой."""
    await reply.delete()
    return "Сообщение удалено."


async def pin_message(bot: Bot, msg: types.Message) -> str:
    await bot.pin_chat_message(msg.chat.id, msg.reply_to_message.message_id)
    return "Сообщение закреплено."


async def unpin_message(bot: Bot, msg: types.Message) -> str:
    await bot.unpin_chat_message(msg.chat.id, msg.reply_to_message.message_id)
    return "Закрепление снято."