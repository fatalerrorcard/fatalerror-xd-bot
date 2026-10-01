"""Телеграм-бот управления чатами: модерация + автосохранение контента.

Запуск:  python main.py
"""

from __future__ import annotations

import asyncio
import logging
import time
from functools import wraps

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import Command
from aiogram.types import Message

import antispam
from config import config
from moderation import (
    ban_user,
    clear_reply,
    is_admin,
    kick_user,
    mute_user,
    pin_message,
    unmute_user,
    unban_user,
    unpin_message,
)
from saver import save_external, watchlist

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("main")

bot = Bot(token=config.bot_token)
dp = Dispatcher()
router = Router()

OWNERS = set(config.admin_ids)  # глобальные админы из .env
SELF = config.admin_ids[0] if config.admin_ids else None

# user_id владельца: запоминается при контакте владельца с ботом
_owner_user_id: int | None = None

# Кэш владельцев групп: chat_id -> (owner_id, ts). 10 минут.
_owner_cache: dict[int, tuple[int, float]] = {}
_OWNER_TTL = 600


async def get_chat_owner(bot: Bot, chat_id: int) -> int | None:
    """Возвращает ID создателя (владельца) группы/канала.

    Требует, чтобы бот был участником чата. Прав администратора
    для определения владельца НЕ требуется.
    """
    now = time.time()
    cached = _owner_cache.get(chat_id)
    if cached and now - cached[1] < _OWNER_TTL:
        return cached[0]

    owner_id = None
    try:
        admins = await bot.get_chat_administrators(chat_id)
        for a in admins:
            if a.status == "creator":
                owner_id = a.user.id
                break
    except Exception:  # noqa: BLE001
        # бот не участник / нет доступа - просто вернём None
        pass

    _owner_cache[chat_id] = (owner_id, now)
    return owner_id


async def _is_owner(message: Message) -> bool:
    """Команду может выполнять владелец (по username), создатель группы или админ из ADMIN_IDS."""
    user = message.from_user
    if not user:
        return False
    # 1) Username владельца (работает в любом чате, даже без админки)
    if user.username and config.owner_username and user.username.lower() == config.owner_username:
        global _owner_user_id
        _owner_user_id = user.id  # запоминаем user_id владельца
        return True
    # 2) Глобальный админ из ADMIN_IDS проходит везде
    if user.id in OWNERS:
        return True
    # 3) Создатель (creator) группы
    if message.chat.type in ("group", "supergroup"):
        owner = await get_chat_owner(message.bot, message.chat.id)
        if owner is not None and user.id == owner:
            return True
    return False


def owner_only(func):
    """Декоратор: пускает только владельца группы / глобального админа."""

    @wraps(func)
    async def wrapper(message: Message, *args, **kwargs):
        if not await _is_owner(message):
            await message.reply("Нет прав: команда доступна владельцу группы.")
            return None
        return await func(message, *args, **kwargs)

    return wrapper


async def _ensure_admin(message: Message) -> tuple[bool, str]:
    """Проверяет, что сообщение из группового чата, где бот - админ."""
    if message.chat.type not in ("group", "supergroup"):
        return False, "Команда работает только в групповом чате."
    if not await is_admin(message.bot, message.chat.id, (await message.bot.get_me()).id):
        return False, "Бот должен быть администратором чата."
    return True, ""


@router.message(Command("start"))
async def cmd_start(message: Message):
    # Запоминаем user_id владельца при первом контакте
    user = message.from_user
    if user and user.username and config.owner_username and user.username.lower() == config.owner_username:
        global _owner_user_id
        _owner_user_id = user.id
        log.info("Запомнен user_id владельца: %s", user.id)
    txt = (
        "Бот управления чатами.\n"
        "Групповые команды (бой должен быть админом):\n"
        "  /ban [ответ] — бан · /unban — снять бан\n"
        "  /kick [ответ] — исключить · /mute 30 — мут 30 мин\n"
        "  /unmute — снять мут · /clear, /delete — удалить ответное сообщение\n"
        "  /pin — закрепить (ответ) · /unpin — открепить\n"
        "Сохранение контента:\n"
        "  /watch — добавить чат в автосохранение\n"
        "  /unwatch — убрать чат\n"
        "  /watchlist — список отслеживаемых чатов\n"
        "  /id — показать ваш ID и ID чата\n"
        "Личка: /id, /watchlist, /watch <чат>"
    )
    await message.answer(txt, disable_web_page_preview=True)


# ---------- Команды групповой модерации ----------

@router.message(Command("id"))
async def cmd_id(message: Message):
    user = message.from_user
    reply = message.reply_to_message
    chat = message.chat
    info = (
        f"Ваш ID: {user.id}\n"
        f"Чат: {chat.id}\n"
        f"Тип чата: {chat.type}"
    )
    if reply and reply.from_user:
        info += f"\n\nЦелевой пользователь: {reply.from_user.id}"
    await message.reply(info)


@router.message(Command("ban"))
async def cmd_ban(message: Message):
    if not await _is_owner(message):
        return await message.reply("Нет прав.")
    ok, err = await _ensure_admin(message)
    if not ok:
        return await message.reply(err)
    if not message.reply_to_message:
        return await message.reply("Ответьте на сообщение пользователя.")
    try:
        res = await ban_user(
            message.bot,
            message.chat.id,
            message.reply_to_message.from_user.id,
        )
        await message.reply(res + " Управляется админами.")
    except Exception as e:  # noqa: BLE001
        await message.reply(f"Ошибка: {e}")


@router.message(Command("unban"))
async def cmd_unban(message: Message):
    if not await _is_owner(message):
        return await message.reply("Нет прав.")
    ok, err = await _ensure_admin(message)
    if not ok:
        return await message.reply(err)
    if not message.reply_to_message:
        return await message.reply("Ответьте на сообщение пользователя.")
    uid = message.reply_to_message.from_user.id
    try:
        await unban_user(message.bot, message.chat.id, uid)
        await message.reply("Бан снят.")
    except Exception as e:  # noqa: BLE001
        await message.reply(f"Ошибка: {e}")


@router.message(Command("kick"))
async def cmd_kick(message: Message):
    if not await _is_owner(message):
        return await message.reply("Нет прав.")
    ok, err = await _ensure_admin(message)
    if not ok:
        return await message.reply(err)
    if not message.reply_to_message:
        return await message.reply("Ответьте на сообщение пользователя.")
    try:
        res = await kick_user(
            message.bot, message.chat.id, message.reply_to_message.from_user.id
        )
        await message.reply(res)
    except Exception as e:  # noqa: BLE001
        await message.reply(f"Ошибка: {e}")


@router.message(Command("mute"))
async def cmd_mute(message: Message):
    if not await _is_owner(message):
        return await message.reply("Нет прав.")
    ok, err = await _ensure_admin(message)
    if not ok:
        return await message.reply(err)
    if not message.reply_to_message:
        return await message.reply("Ответьте на сообщение пользователя.")
    args = message.text.split()
    mins = int(args[1]) if len(args) > 1 and args[1].isdigit() else 10
    try:
        res = await mute_user(
            message.bot, message.chat.id, message.reply_to_message.from_user.id, mins
        )
        await message.reply(res)
    except Exception as e:  # noqa: BLE001
        await message.reply(f"Ошибка: {e}")


@router.message(Command("unmute"))
async def cmd_unmute(message: Message):
    if not await _is_owner(message):
        return await message.reply("Нет прав.")
    ok, err = await _ensure_admin(message)
    if not ok:
        return await message.reply(err)
    if not message.reply_to_message:
        return await message.reply("Ответьте на сообщение пользователя.")
    try:
        res = await unmute_user(
            message.bot, message.chat.id, message.reply_to_message.from_user.id
        )
        await message.reply(res)
    except Exception as e:  # noqa: BLE001
        await message.reply(f"Ошибка: {e}")


@router.message(Command("clear", "delete"))
async def cmd_clear(message: Message):
    if not await _is_owner(message):
        return await message.reply("Нет прав.")
    ok, err = await _ensure_admin(message)
    if not ok:
        return await message.reply(err)
    if not message.reply_to_message:
        return await message.reply("Ответьте на сообщение пользователя.")
    try:
        res = await clear_reply(message.bot, message.chat.id, message.reply_to_message)
        await message.reply(res)
    except Exception as e:  # noqa: BLE001
        await message.reply(f"Ошибка: {e}")


@router.message(Command("pin"))
async def cmd_pin(message: Message):
    if not await _is_owner(message):
        return await message.reply("Нет прав.")
    ok, err = await _ensure_admin(message)
    if not ok:
        return await message.reply(err)
    if not message.reply_to_message:
        return await message.reply("Ответьте на сообщение для закрепления.")
    try:
        res = await pin_message(message.bot, message)
        await message.reply(res)
    except Exception as e:  # noqa: BLE001
        await message.reply(f"Ошибка: {e}")


@router.message(Command("unpin"))
async def cmd_unpin(message: Message):
    if not await _is_owner(message):
        return await message.reply("Нет прав.")
    ok, err = await _ensure_admin(message)
    if not ok:
        return await message.reply(err)
    if not message.reply_to_message:
        return await message.reply("Ответьте на сообщение для открепления.")
    try:
        res = await unpin_message(message.bot, message)
        await message.reply(res)
    except Exception as e:  # noqa: BLE001
        await message.reply(f"Ошибка: {e}")


# ---------- Работа со списком чатов для сохранения ----------

@router.message(Command("watch"))
async def cmd_watch(message: Message):
    if not await _is_owner(message):
        return await message.reply("Нет прав.")
    args = message.text.split(maxsplit=1)
    # для групы: можно завачить текущий чат
    if len(args) == 1 and message.chat.type in ("group", "supergroup"):
        watchlist.add(message.chat.id)
        await message.reply(f"Чат {message.chat.id} добавлен в автосохранение.")
        return
    if len(args) == 1:
        return await message.reply(
            "Укажите чат: /watch @username или числовой ID (в личке бота)"
        )
    target = args[1].strip()
    if target.startswith("@"):
        target = target[1:]
    watchlist.add(target)
    await message.reply(f"Добавлено отслеживание: {target}")


@router.message(Command("unwatch"))
async def cmd_unwatch(message: Message):
    if not await _is_owner(message):
        return await message.reply("Нет прав.")
    args = message.text.split(maxsplit=1)
    if len(args) == 1 and message.chat.type in ("group", "supergroup"):
        watchlist.discard(message.chat.id)
        await message.reply("Чат убран из автосохранения.")
        return
    if len(args) == 1:
        return await message.reply("Укажите @username для удаления.")
    target = args[1].strip().lstrip("@")
    watchlist.discard(target)
    await message.reply(f"Убрано: {target}")


@router.message(Command("watchlist"))
async def cmd_watchlist(message: Message):
    if not await _is_owner(message):
        return await message.reply("Нет прав.")
    if not watchlist:
        return await message.reply("Список пуст. Добавьте чаты через /watch.")
    items = "\n".join(f" • {str(c)}" for c in watchlist)
    await message.reply(f"Отслеживаемые чаты:\n{items}")


# ---------- Автосохранение контента ----------
from aiogram.filters import BaseFilter


class InWatchlist(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.chat.id in watchlist


@router.message(InWatchlist())
async def auto_save(message: Message):
    result, note = await save_external(message.bot, message)
    if result:
        log.info("Сохранено из %s: %s", message.chat.id, note)


# ---------- Приветствие при добавлении бота в группу ----------

@router.my_chat_member()
async def bot_added_to_chat(event: types.ChatMemberUpdated):
    """Когда бота добавляют в группу, поздороваться, если в чате есть владелец."""
    chat = event.chat
    if chat.type not in ("group", "supergroup"):
        return

    new_status = event.new_chat_member.status
    if new_status not in ("member", "administrator"):
        return

    owner_username = config.owner_username
    if not owner_username:
        return

    owner_id = _owner_user_id
    admin_match = False
    member_match = False

    # 1) Ищем владельца среди админов/создателя группы
    try:
        admins = await event.bot.get_chat_administrators(chat.id)
        admin_match = any(
            a.user.username and a.user.username.lower() == owner_username
            for a in admins
        )
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось получить админов: %s", e)

    # 2) Если знаем user_id владельца - проверяем членство напрямую
    if not admin_match and owner_id:
        try:
            member = await event.bot.get_chat_member(chat.id, owner_id)
            member_match = member.status in ("member", "administrator", "creator")
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось проверить членство: %s", e)

    if not (admin_match or member_match):
        log.info("Владелец %s не найден в группе %s при добавлении", owner_username, chat.id)
        return

    try:
        await event.bot.send_message(
            chat.id,
            f"@{owner_username} Создатель! Привет! как вы?",
        )
        log.info("Приветствие отправлено в чат %s", chat.id)
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось отправить приветствие: %s", e)


async def main():
    dp.include_router(router)
    dp.include_router(antispam.router)
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Запуск бота...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())