"""Телеграм-бот управления чатами: модерация + автосохранение контента.

Запуск:  python main.py
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime
from functools import wraps

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import Command
from aiogram.types import BotCommand, Message

import actionlog
import antispam
import ranks
import reputation
import settings
import stats
import warns
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


# ---------- Ранги: уровни и права ----------

# Какой ранг нужен для действия (для текста отказа)
_NEED_TITLE = {
    "delete": "младшего модера",
    "mute": "младшего модера",
    "unmute": "старшего модера",
    "ban": "старшего модера",
    "unban": "старшего модера",
    "kick": "старшего модера",
    "pin": "старшего модера",
    "assign_moderator": "младшего админа",
    "assign_junior_admin": "старшего админа",
    "assign_senior_admin": "владельца",
}


async def level_for(chat, user, bot) -> int:
    """Уровень прав пользователя в чате (0–5). Работает и для кнопок."""
    if user is None:
        return 0
    if (
        user.username
        and config.owner_username
        and user.username.lower() == config.owner_username
    ):
        return ranks.OWNER_LEVEL
    if user.id in OWNERS:
        return ranks.OWNER_LEVEL
    if chat.type in ("group", "supergroup"):
        owner = await get_chat_owner(bot, chat.id)
        if owner is not None and user.id == owner:
            return ranks.OWNER_LEVEL
        return ranks.rank_level(ranks.get_rank(chat.id, user.id))
    return 0


async def effective_level(message: Message) -> int:
    """Уровень прав отправителя сообщения."""
    return await level_for(message.chat, message.from_user, message.bot)


async def has_perm(message: Message, perm: str) -> bool:
    need = ranks.PERM_LEVELS.get(perm, 99)
    return await effective_level(message) >= need


async def _require(message: Message, perm: str) -> bool:
    if await has_perm(message, perm):
        return True
    await message.reply(
        f"Нет прав: нужен ранг не ниже {_NEED_TITLE.get(perm, 'старшего модера')}."
    )
    return False


def _resolve_target(message: Message, parts: list[str], index: int) -> int | None:
    """Кому назначаем: ответ на сообщение или числовой ID."""
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
    if len(parts) > index and parts[index].isdigit():
        return int(parts[index])
    return None


def _reason(message: Message, skip: int = 1) -> str:
    """Причина наказания: всё после первых `skip` слов команды."""
    parts = (message.text or "").split(maxsplit=skip)
    if len(parts) > skip:
        return parts[skip].strip()
    return ""


def _parse_mute(message: Message) -> tuple[int, str]:
    """'/mute 30 флуд' -> (30, 'флуд'); '/mute флуд' -> (10, 'флуд')."""
    parts = (message.text or "").split(maxsplit=1)
    rest = parts[1].strip() if len(parts) > 1 else ""
    if not rest:
        return 10, ""
    m = re.match(r"^(\d+)\s*(.*)$", rest)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return 10, rest


def _parse_warn(message: Message) -> tuple[int, str]:
    """'/warn 1ч мат' -> (3600, 'мат'); без срока — стандартный."""
    parts = (message.text or "").split(maxsplit=1)
    rest = parts[1].strip() if len(parts) > 1 else ""
    if not rest:
        return 0, ""
    first, _, tail = rest.partition(" ")
    ttl = warns.parse_duration(first)
    if ttl is not None:
        return ttl, tail.strip()
    return 0, rest


async def _log(message: Message, action: str, text: str) -> None:
    try:
        await actionlog.log_action(message.bot, message.chat.id, action, text)
    except Exception:  # noqa: BLE001
        pass


async def _ensure_admin(message: Message) -> tuple[bool, str]:
    """Проверяет, что сообщение из группового чата, где бот - админ."""
    if message.chat.type not in ("group", "supergroup"):
        return False, "Команда работает только в групповом чате."
    if not await is_admin(message.bot, message.chat.id, (await message.bot.get_me()).id):
        return False, "Бот должен быть администратором чата."
    return True, ""


def help_text() -> str:
    """Полный список всех команд бота."""
    return (
        "БОТ УПРАВЛЕНИЯ ЧАТАМИ\n"
        "\n"
        "Команды можно писать и со слэшем (/ban), и просто словом (бан).\n"
        "\n"
        "МОДЕРАЦИЯ (ответом на сообщение, бот должен быть админом):\n"
        "  бан / бан спам — забанить, можно с причиной\n"
        "  разбан — снять бан\n"
        "  кик / кик флуд — исключить, с причиной\n"
        "  мут / мут 30 / мут 30 флуд — мут на N минут с причиной\n"
        "  размут — снять мут\n"
        "  удалить / удали — удалить сообщение\n"
        "  закрепить / открепить — закреп и снятие закрепа\n"
        "\n"
        "РАНГИ:\n"
        "  мой ранг — ваш ранг\n"
        "  ранги — иерархия рангов\n"
        "  выдать <ранг> — выдать ранг (ответом на сообщение)\n"
        "      ранги: модер, стмодер, младший админ, старший админ\n"
        "  снять ранг — забрать ранг (ответом)\n"
        "  ранги чата — кто какой ранг имеет\n"
        "\n"
        "ПРЕДУПРЕЖДЕНИЯ (варны):\n"
        "  варн / варн 1ч / варн 7д / варн 1мес — выдать (ответом)\n"
        "      срок: 30м, 1ч, 7д, 2нед, 1мес (без срока — стандартный)\n"
        "  варны — показать предупреждения (ответом или /warns)\n"
        "  снять варн — снять последнее (ответом)\n"
        "  срок варнов 7д — срок по умолчанию для чата\n"
        f"  {config.warn_limit} предупреждения = бан\n"
        "\n"
        "СТАТИСТИКА:\n"
        "  топ — топ по сообщениям за всё время\n"
        "  топ день — топ за сегодня\n"
        "  топ неделя — топ за 7 дней\n"
        "  моя статистика — ваша личная статистика\n"
        "  /topreset — сброс статистики чата (старший админ)\n"
        "\n"
        "РЕПУТАЦИЯ:\n"
        "  + / - (ответом) — плюс или минус участнику\n"
        "  реп — репутация участника (ответом) или своя\n"
        "  топ репутации — рейтинг чата\n"
        f"      лимит оценок: {config.rep_daily_limit} в сутки\n"
        "\n"
        "ЖАЛОБЫ:\n"
        "  жалоба (ответом) — карточка с кнопками для админов\n"
        "  жалоба спам — жалоба с причиной\n"
        "\n"
        "СОХРАНЕНИЕ КОНТЕНТА:\n"
        "  /watch — добавить чат в автосохранение\n"
        "  /unwatch — убрать чат\n"
        "  /watchlist — список отслеживаемых чатов\n"
        "\n"
        "ПРОЧЕЕ:\n"
        "  инфо — карточка участника (ответом или ID)\n"
        "  правила — показать правила чата\n"
        "  правила установить <текст> — задать правила (младший админ)\n"
        "  журнал — куда уходят действия модерации\n"
        "  журнал @канал — привязать журнал (младший админ)\n"
        "  /id — ваш ID и ID чата\n"
        "  помощь / команды — эта справка\n"
    )


@router.message(Command("start", "help"))
async def cmd_start(message: Message):
    # Запоминаем user_id владельца при первом контакте
    user = message.from_user
    if user and user.username and config.owner_username and user.username.lower() == config.owner_username:
        global _owner_user_id
        _owner_user_id = user.id
        log.info("Запомнен user_id владельца: %s", user.id)
    await message.answer(help_text(), disable_web_page_preview=True)


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
    if not await _require(message, "ban"):
        return
    ok, err = await _ensure_admin(message)
    if not ok:
        return await message.reply(err)
    if not message.reply_to_message:
        return await message.reply("Ответьте на сообщение пользователя.")
    victim = message.reply_to_message.from_user
    reason = _reason(message)
    try:
        res = await ban_user(message.bot, message.chat.id, victim.id)
        note = f"\nПричина: {reason}" if reason else ""
        await message.reply(res + note)
        details = actionlog.target(victim)
        if reason:
            details += f" · причина: {reason}"
        details += f" · модератор: {actionlog.actor(message.from_user)}"
        await _log(message, "БАН", details)
    except Exception as e:  # noqa: BLE001
        await message.reply(f"Ошибка: {e}")


@router.message(Command("unban"))
async def cmd_unban(message: Message):
    if not await _require(message, "unban"):
        return
    ok, err = await _ensure_admin(message)
    if not ok:
        return await message.reply(err)
    if not message.reply_to_message:
        return await message.reply("Ответьте на сообщение пользователя.")
    uid = message.reply_to_message.from_user.id
    victim = message.reply_to_message.from_user
    try:
        await unban_user(message.bot, message.chat.id, uid)
        await message.reply("Бан снят.")
        await _log(
            message,
            "РАЗБАН",
            f"{actionlog.target(victim)} · модератор: {actionlog.actor(message.from_user)}",
        )
    except Exception as e:  # noqa: BLE001
        await message.reply(f"Ошибка: {e}")


@router.message(Command("kick"))
async def cmd_kick(message: Message):
    if not await _require(message, "kick"):
        return
    ok, err = await _ensure_admin(message)
    if not ok:
        return await message.reply(err)
    if not message.reply_to_message:
        return await message.reply("Ответьте на сообщение пользователя.")
    victim = message.reply_to_message.from_user
    reason = _reason(message)
    try:
        res = await kick_user(message.bot, message.chat.id, victim.id)
        note = f"\nПричина: {reason}" if reason else ""
        await message.reply(res + note)
        details = actionlog.target(victim)
        if reason:
            details += f" · причина: {reason}"
        details += f" · модератор: {actionlog.actor(message.from_user)}"
        await _log(message, "КИК", details)
    except Exception as e:  # noqa: BLE001
        await message.reply(f"Ошибка: {e}")


@router.message(Command("mute"))
async def cmd_mute(message: Message):
    if not await _require(message, "mute"):
        return
    ok, err = await _ensure_admin(message)
    if not ok:
        return await message.reply(err)
    if not message.reply_to_message:
        return await message.reply("Ответьте на сообщение пользователя.")
    victim = message.reply_to_message.from_user
    mins, reason = _parse_mute(message)
    try:
        res = await mute_user(message.bot, message.chat.id, victim.id, mins)
        note = f"\nПричина: {reason}" if reason else ""
        await message.reply(res + note)
        details = actionlog.target(victim) + f" · {mins} мин"
        if reason:
            details += f" · причина: {reason}"
        details += f" · модератор: {actionlog.actor(message.from_user)}"
        await _log(message, "МУТ", details)
    except Exception as e:  # noqa: BLE001
        await message.reply(f"Ошибка: {e}")


@router.message(Command("unmute"))
async def cmd_unmute(message: Message):
    if not await _require(message, "unmute"):
        return
    ok, err = await _ensure_admin(message)
    if not ok:
        return await message.reply(err)
    if not message.reply_to_message:
        return await message.reply("Ответьте на сообщение пользователя.")
    victim = message.reply_to_message.from_user
    try:
        res = await unmute_user(message.bot, message.chat.id, victim.id)
        await message.reply(res)
        await _log(
            message,
            "РАЗМУТ",
            f"{actionlog.target(victim)} · модератор: {actionlog.actor(message.from_user)}",
        )
    except Exception as e:  # noqa: BLE001
        await message.reply(f"Ошибка: {e}")


@router.message(Command("clear", "delete"))
async def cmd_clear(message: Message):
    if not await _require(message, "delete"):
        return
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
    if not await _require(message, "pin"):
        return
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
    if not await _require(message, "pin"):
        return
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


# ---------- Ранги ----------

@router.message(Command("ranks"))
async def cmd_ranks(message: Message):
    await message.reply(
        "Иерархия рангов (от высшего к низшему):\n" + ranks.hierarchy_text()
    )


@router.message(Command("rank"))
async def cmd_rank(message: Message):
    user = message.from_user
    if not user:
        return
    lvl = await effective_level(message)
    if lvl == ranks.OWNER_LEVEL:
        title = ranks.OWNER_TITLE
    elif message.chat.type in ("group", "supergroup"):
        title = ranks.rank_title(ranks.get_rank(message.chat.id, user.id))
    else:
        title = "—"
    await message.reply(f"Ваш ранг: {title} (уровень {lvl})")


@router.message(Command("setrank"))
async def cmd_setrank(message: Message):
    parts = (message.text or "").split()
    reply = message.reply_to_message
    if reply and reply.from_user:
        if len(parts) < 2:
            return await message.reply(
                "Укажите ранг ответом на сообщение: /setrank модер"
            )
        rank_raw = " ".join(parts[1:])
        target = reply.from_user.id
    else:
        if len(parts) < 3 or not parts[1].isdigit():
            return await message.reply(
                "Формат: /setrank <ID> <ранг> или ответом: /setrank <ранг>\n"
                "Ранги: модер, стмодер, младший админ, старший админ"
            )
        target = int(parts[1])
        rank_raw = " ".join(parts[2:])

    rank = ranks.normalize_rank(rank_raw)
    if not rank or rank == "owner":
        return await message.reply(
            "Неизвестный ранг. Доступно: модер, стмодер, младший админ, старший админ."
        )

    perm = ranks.assign_perm_for(rank)
    if not perm or not await has_perm(message, perm):
        return await message.reply("Нет прав на выдачу этого ранга.")

    my_level = await effective_level(message)
    target_level = ranks.rank_level(ranks.get_rank(message.chat.id, target))
    new_level = ranks.rank_level(rank)
    if my_level < ranks.OWNER_LEVEL:
        if target_level >= my_level:
            return await message.reply("Нельзя менять ранг равному или старшему.")
        if new_level >= my_level:
            return await message.reply("Нельзя выдать ранг не ниже своего.")

    ranks.set_rank(message.chat.id, target, rank)
    await message.reply(
        f"Пользователю {target} выдан ранг «{ranks.rank_title(rank)}»."
    )
    await _log(
        message,
        "РАНГ",
        f"{target} · «{ranks.rank_title(rank)}» · модератор: {actionlog.actor(message.from_user)}",
    )


@router.message(Command("unrank"))
async def cmd_unrank(message: Message):
    parts = (message.text or "").split()
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user.id
    elif len(parts) > 1 and parts[1].isdigit():
        target = int(parts[1])
    else:
        return await message.reply(
            "Ответьте на сообщение пользователя или укажите ID: /unrank <ID>"
        )

    my_level = await effective_level(message)
    if my_level < ranks.PERM_LEVELS["assign_moderator"]:
        return await message.reply("Нет прав: нужен ранг не ниже младшего админа.")

    target_level = ranks.rank_level(ranks.get_rank(message.chat.id, target))
    if my_level < ranks.OWNER_LEVEL and target_level >= my_level:
        return await message.reply("Нельзя снять ранг равному или старшему.")

    ranks.set_rank(message.chat.id, target, None)
    await message.reply("Ранг снят.")
    await _log(
        message,
        "РАНГ СНЯТ",
        f"{target} · модератор: {actionlog.actor(message.from_user)}",
    )


@router.message(Command("ranklist", "rlist"))
async def cmd_ranklist(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        return await message.reply("Команда работает только в группе.")
    data = ranks.chat_ranks(message.chat.id)
    if not data:
        return await message.reply("В этом чате ранги никому не выданы.")
    ordered = sorted(data.items(), key=lambda kv: -ranks.rank_level(kv[1]))
    lines = []
    for uid, rank in ordered:
        name = str(uid)
        try:
            member = await message.bot.get_chat_member(message.chat.id, uid)
            name = member.user.full_name or member.user.username or str(uid)
        except Exception:  # noqa: BLE001
            pass
        lines.append(f"• {name} — {ranks.rank_title(rank)}")
    await message.reply("Ранги в чате:\n" + "\n".join(lines))


# ---------- Предупреждения (варны) ----------

def _warn_target(
    message: Message, parts: list[str]
) -> tuple[int | None, str, str | None]:
    """Определяет цель: ответ на сообщение или числовой ID.

    Возвращает (user_id, имя, сырой срок).
    """
    reply = message.reply_to_message
    if reply and reply.from_user:
        name = reply.from_user.full_name or str(reply.from_user.id)
        ttl_raw = parts[1] if len(parts) > 1 else None
        return reply.from_user.id, name, ttl_raw
    if len(parts) > 1 and parts[1].isdigit():
        ttl_raw = parts[2] if len(parts) > 2 else None
        return int(parts[1]), parts[1], ttl_raw
    return None, "", None


@router.message(Command("warn"))
async def cmd_warn(message: Message):
    if not await _require(message, "mute"):
        return
    ok, err = await _ensure_admin(message)
    if not ok:
        return await message.reply(err)

    parts = (message.text or "").split()
    reply = message.reply_to_message
    reason = ""
    if reply and reply.from_user:
        target = reply.from_user.id
        name = reply.from_user.full_name or str(target)
        ttl, reason = _parse_warn(message)
    elif len(parts) > 1 and parts[1].isdigit():
        target = int(parts[1])
        name = parts[1]
        rest = parts[2:]
        ttl = 0
        if rest:
            parsed = warns.parse_duration(rest[0])
            if parsed is not None:
                ttl = parsed
                reason = " ".join(rest[1:])
            else:
                reason = " ".join(rest)
    else:
        return await message.reply(
            "Ответьте на сообщение или укажите ID: варн 1ч (срок: 30м, 1ч, 7д, 1мес)"
        )

    if not ttl:
        ttl = warns.get_default_ttl(
            message.chat.id, config.warn_ttl_days * 86400
        )

    count, _until = warns.add_warn(message.chat.id, target, ttl, name)

    if count >= config.warn_limit:
        try:
            await ban_user(message.bot, message.chat.id, target)
            warns.clear(message.chat.id, target)
            await message.reply(
                f"{name}: {count} предупреждения — бан."
            )
            details = f"{name} · {count} предупреждения"
            if reason:
                details += f" · причина: {reason}"
            details += f" · модератор: {actionlog.actor(message.from_user)}"
            await _log(message, "БАН (варны)", details)
        except Exception as e:  # noqa: BLE001
            await message.reply(
                f"Предупреждений {count}, но забанить не удалось: {e}"
            )
        return

    await message.reply(
        f"Предупреждение {count}/{config.warn_limit} для {name}. "
        f"Снимется через {warns.format_duration(ttl)}."
    )
    details = f"{name} · {count}/{config.warn_limit}"
    if reason:
        details += f" · причина: {reason}"
    details += f" · модератор: {actionlog.actor(message.from_user)}"
    await _log(message, "ВАРН", details)


@router.message(Command("warns"))
async def cmd_warns(message: Message):
    parts = (message.text or "").split()
    target, name, _ = _warn_target(message, parts)
    if not target:
        target = message.from_user.id if message.from_user else None
        name = "вы"
    if not target:
        return

    active = warns.list_warns(message.chat.id, target)
    if not active:
        return await message.reply(f"У {name} нет предупреждений.")

    import time as _time

    now = _time.time()
    lines = [f"Предупреждения ({name}): {len(active)}/{config.warn_limit}"]
    for i, until in enumerate(active, start=1):
        left = int(until - now)
        lines.append(f"{i}. осталось {warns.format_duration(left)}")
    await message.reply("\n".join(lines))


@router.message(Command("unwarn"))
async def cmd_unwarn(message: Message):
    if not await _require(message, "mute"):
        return
    parts = (message.text or "").split()
    target, name, _ = _warn_target(message, parts)
    if not target:
        return await message.reply("Ответьте на сообщение или укажите ID.")

    if warns.remove_last(message.chat.id, target):
        await message.reply(f"Снято последнее предупреждение с {name}.")
        await _log(
            message,
            "СНЯТ ВАРН",
            f"{name} · модератор: {actionlog.actor(message.from_user)}",
        )
    else:
        await message.reply(f"У {name} нет активных предупреждений.")


@router.message(Command("warnset"))
async def cmd_warnset(message: Message):
    if not await _require(message, "assign_moderator"):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        cur = warns.get_default_ttl(message.chat.id, config.warn_ttl_days * 86400)
        return await message.reply(
            f"Срок варна по умолчанию: {warns.format_duration(cur)}.\n"
            "Изменить: /warnset 7д (30м, 1ч, 7д, 2нед, 1мес)"
        )
    ttl = warns.parse_duration(parts[1])
    if ttl is None:
        return await message.reply("Не понял срок. Примеры: 30м, 1ч, 7д, 1мес.")
    warns.set_default_ttl(message.chat.id, ttl)
    await message.reply(
        f"Срок варна по умолчанию: {warns.format_duration(ttl)}."
    )


# ---------- Информация, правила, журнал ----------

@router.message(Command("info"))
async def cmd_info(message: Message):
    reply = message.reply_to_message
    user = reply.from_user if reply and reply.from_user else None
    parts = (message.text or "").split()
    if user is None and len(parts) > 1 and parts[1].lstrip("-").isdigit():
        try:
            member = await message.bot.get_chat_member(
                message.chat.id, int(parts[1])
            )
            user = member.user
        except Exception:  # noqa: BLE001
            return await message.reply("Не нашёл такого участника в чате.")
    if user is None:
        user = message.from_user
    if user is None:
        return

    rank = ranks.get_rank(message.chat.id, user.id)
    if (
        (user.username and config.owner_username
         and user.username.lower() == config.owner_username)
        or user.id in OWNERS
    ):
        rank = "owner"
    else:
        owner_id = await get_chat_owner(message.bot, message.chat.id)
        if owner_id == user.id:
            rank = "owner"

    summary = stats.user_summary(message.chat.id, user.id)
    active = warns.list_warns(message.chat.id, user.id)
    next_left = ""
    if active:
        left = max(int(active[0] - time.time()), 0)
        next_left = f" · ближайший через {warns.format_duration(left)}"
    first = ""
    if summary["first"]:
        first = datetime.fromtimestamp(summary["first"]).strftime("%d.%m.%Y")

    rep_value = reputation.score(message.chat.id, user.id)
    rep_place = reputation.places(message.chat.id).get(str(user.id))
    rep_line = f"Репутация: {rep_value:+d}"
    if rep_place:
        rep_line += f" · место {rep_place}"

    lines = [
        user.full_name or "—",
        f"@{user.username}" if user.username else "без username",
        f"ID: {user.id}",
        "",
        f"Ранг: {ranks.rank_title(rank) if rank else '—'}",
        f"Варны: {len(active)}/{config.warn_limit}{next_left}",
        f"Сообщений: {summary['total']} (день {summary['day']}, неделя {summary['week']})",
        rep_line,
    ]
    if first:
        lines.append(f"В чате с: {first}")
    await message.reply("\n".join(lines))


@router.message(Command("rules"))
async def cmd_rules(message: Message):
    text = settings.rules(message.chat.id)
    if not text:
        return await message.reply(
            "Правила не заданы.\nУстановить: правила установить <текст>"
        )
    await message.reply("ПРАВИЛА ЧАТА\n\n" + text)


@router.message(Command("setrules"))
async def cmd_setrules(message: Message):
    if not await _require(message, "assign_moderator"):
        return
    raw = (message.text or "").strip()
    low = raw.lower()
    text = ""
    for prefix in (
        "/setrules",
        "правила установить",
        "установить правила",
        "задать правила",
        "правила задать",
    ):
        if low.startswith(prefix):
            text = raw[len(prefix):].strip()
            break
    if not text:
        return await message.reply(
            "Напишите текст: правила установить <текст>"
        )
    settings.set_rules(message.chat.id, text)
    await message.reply("Правила обновлены.")


@router.message(Command("setlog"))
async def cmd_setlog(message: Message):
    if not await _require(message, "assign_moderator"):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        cur = settings.log_chat(message.chat.id)
        return await message.reply(
            f"Журнал: {cur if cur else 'не задан'}\n"
            "Привязать: журнал @канал или журнал -1001234567890\n"
            "Отключить: журнал выкл"
        )
    raw = parts[1].strip()
    if raw.lower() in ("off", "выкл", "нет", "0"):
        settings.unset("logs", message.chat.id)
        return await message.reply("Журнал отключён.")
    target = int(raw) if raw.lstrip("-").isdigit() else raw
    settings.set_log_chat(message.chat.id, target)
    await message.reply(f"Журнал действий привязан к {target}.")


# ---------- Репутация ----------

def _member_name(user) -> str:
    if user is None:
        return "—"
    if user.username:
        return f"@{user.username}"
    return user.full_name or str(user.id)


async def _give_rep(message: Message, delta: int) -> None:
    if message.chat.type not in ("group", "supergroup"):
        return
    reply = message.reply_to_message
    if not reply or not reply.from_user:
        return await message.reply("Ответьте на сообщение участника.")
    target = reply.from_user
    if target.is_bot:
        return await message.reply("Ботам репутацию не ставят.")
    if message.from_user is None:
        return

    ok, err, value = reputation.give(
        message.chat.id,
        message.from_user.id,
        target.id,
        delta,
        config.rep_daily_limit,
        target.full_name or "",
        target.username or "",
    )
    if not ok:
        return await message.reply(err)

    sign = "+" if delta > 0 else ""
    await message.reply(
        f"{sign}{delta} → {_member_name(target)} · репутация {value:+d}"
    )


@router.message(Command("plus", "плюс"))
async def cmd_plus(message: Message):
    await _give_rep(message, 1)


@router.message(Command("minus", "минус"))
async def cmd_minus(message: Message):
    await _give_rep(message, -1)


@router.message(Command("rep", "reputation", "реп", "репутация"))
async def cmd_rep(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        return await message.reply("Команда работает только в группе.")
    reply = message.reply_to_message
    user = reply.from_user if reply and reply.from_user else message.from_user
    if user is None:
        return

    value = reputation.score(message.chat.id, user.id)
    place = reputation.places(message.chat.id).get(str(user.id))
    line = f"Репутация {_member_name(user)}: {value:+d}"
    if place:
        line += f" · место {place}"
    if message.from_user:
        left = max(
            config.rep_daily_limit
            - reputation.given_today(message.chat.id, message.from_user.id),
            0,
        )
        line += f"\nОсталось оценок сегодня: {left}/{config.rep_daily_limit}"
    await message.reply(line)


@router.message(Command("reptop", "топрепутации"))
async def cmd_reptop(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        return await message.reply("Команда работает только в группе.")
    rows = reputation.top(message.chat.id, 10)
    if not rows:
        return await message.reply("Репутация пока никому не выдавалась.")

    lines = ["ТОП РЕПУТАЦИИ"]
    for i, row in enumerate(rows, start=1):
        who = (
            f"@{row['username']}"
            if row["username"]
            else (row["name"] or row["id"])
        )
        lines.append(f"{i}. {who} — {row['score']:+d}")
    await message.reply("\n".join(lines))


# ---------- Жалобы ----------

_recent_reports: dict[tuple[int, int], float] = {}
_REPORT_COOLDOWN = 300


@router.message(Command("report"))
async def cmd_report(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        return await message.reply("Жалоба работает только в группе.")
    reply = message.reply_to_message
    if not reply or not reply.from_user:
        return await message.reply(
            "Ответьте на сообщение нарушителя: жалоба спам"
        )
    target = reply.from_user
    if target.is_bot:
        return await message.reply("На бота жаловаться не нужно.")
    if message.from_user is None:
        return
    if target.id == message.from_user.id:
        return await message.reply("Нельзя жаловаться на себя.")

    key = (message.from_user.id, target.id)
    now = time.time()
    if now - _recent_reports.get(key, 0) < _REPORT_COOLDOWN:
        return await message.reply(
            "Жалоба на этого участника уже отправлена. Подождите."
        )
    _recent_reports[key] = now

    reason = _reason(message)
    lines = [
        "ЖАЛОБА",
        f"Нарушитель: {actionlog.target(target)}",
        f"От: {actionlog.actor(message.from_user)}",
    ]
    if reason:
        lines.append(f"Причина: {reason}")
    lines.append("")
    lines.append("Решение принимают администраторы.")

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Забанить",
                    callback_data=f"rep:ban:{target.id}:{reply.message_id}",
                ),
                types.InlineKeyboardButton(
                    text="Мут 1ч",
                    callback_data=f"rep:mute:{target.id}:{reply.message_id}",
                ),
                types.InlineKeyboardButton(
                    text="Отклонить",
                    callback_data=f"rep:skip:{target.id}:{reply.message_id}",
                ),
            ]
        ]
    )
    await message.reply("\n".join(lines), reply_markup=kb)


@router.callback_query(F.data.startswith("rep:"))
async def on_report_action(cb: types.CallbackQuery):
    parts = (cb.data or "").split(":")
    if len(parts) < 4:
        return await cb.answer("Некорректная жалоба", show_alert=True)
    _, action, target_raw, _src = parts
    try:
        target_id = int(target_raw)
    except ValueError:
        return await cb.answer("Некорректная жалоба", show_alert=True)

    chat = cb.message.chat
    actor_user = cb.from_user
    level = await level_for(chat, actor_user, cb.bot)
    need = {"ban": "ban", "mute": "mute", "skip": "mute"}.get(action)
    if need is None:
        return await cb.answer("Неизвестное действие", show_alert=True)
    if level < ranks.PERM_LEVELS[need]:
        return await cb.answer("Нет прав на это действие", show_alert=True)

    try:
        if action == "ban":
            await ban_user(cb.bot, chat.id, target_id)
            verdict = f"Забанен · {actionlog.actor(actor_user)}"
            log_kind = "БАН (жалоба)"
        elif action == "mute":
            await mute_user(cb.bot, chat.id, target_id, 60)
            verdict = f"Мут 60 мин · {actionlog.actor(actor_user)}"
            log_kind = "МУТ (жалоба)"
        else:
            verdict = f"Отклонено · {actionlog.actor(actor_user)}"
            log_kind = "ЖАЛОБА ОТКЛОНЕНА"
    except Exception as e:  # noqa: BLE001
        return await cb.answer(f"Не удалось: {e}", show_alert=True)

    base = cb.message.text or "ЖАЛОБА"
    try:
        await cb.message.edit_text(f"{base}\n\n→ {verdict}", reply_markup=None)
    except Exception:  # noqa: BLE001
        pass
    await cb.answer("Готово")

    try:
        await actionlog.log_action(
            cb.bot,
            chat.id,
            log_kind,
            f"{target_id} · модератор: {actionlog.actor(actor_user)}",
        )
    except Exception:  # noqa: BLE001
        pass


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


# ---------- Команды без слэша, по-русски ----------
# Работают как обычный текст: "бан", "мут 30", "топ неделя" и т.д.
# Требуют, чтобы у бота был ВЫКЛЮЧЕН режим приватности в @BotFather.

_RU_COMMANDS: list[tuple[re.Pattern[str], object]] = [
    (re.compile(r"^(бан|забанить)$", re.I), cmd_ban),
    (re.compile(r"^(разбан|унбан|разбанить)$", re.I), cmd_unban),
    (re.compile(r"^(кик|выгнать|исключить)$", re.I), cmd_kick),
    (re.compile(r"^мут(\s+\d+)?$", re.I), cmd_mute),
    (re.compile(r"^(размут|унмут|снять мут)$", re.I), cmd_unmute),
    (re.compile(r"^(удалить|удали|удаление|чистить|очистить)$", re.I), cmd_clear),
    (re.compile(r"^(закрепить|закреп)$", re.I), cmd_pin),
    (re.compile(r"^(открепить|откреп|снять закреп)$", re.I), cmd_unpin),
    (
        re.compile(
            r"^топ(\s+(за\s+)?(день|дня|неделя|неделю|недели|все|всё|всего))?$",
            re.I,
        ),
        stats.cmd_top,
    ),
    (
        re.compile(r"^(моя статистика|мои сообщения|стата|моя стата)$", re.I),
        stats.cmd_mystats,
    ),
    (re.compile(r"^(мой ранг|ранг)$", re.I), cmd_rank),
    (re.compile(r"^(ранги|иерархия|список рангов)$", re.I), cmd_ranks),
    (re.compile(r"^выдать\s+\S+$", re.I), cmd_setrank),
    (re.compile(r"^(снять ранг|забрать ранг)$", re.I), cmd_unrank),
    (re.compile(r"^(ранги чата|кто тут кто)$", re.I), cmd_ranklist),
    (
        re.compile(r"^(варн|предупреждение|warn)(\s+.+)?$", re.I),
        cmd_warn,
    ),
    (re.compile(r"^(варны|предупреждения|warns)$", re.I), cmd_warns),
    (
        re.compile(r"^(снять варн|унварн|unwarn)(\s+.+)?$", re.I),
        cmd_unwarn,
    ),
    (
        re.compile(r"^(срок варнов|варн срок|warnset)(\s+.+)?$", re.I),
        cmd_warnset,
    ),
    (
        re.compile(r"^(топ репутации|рейтинг репутации|reptop)$", re.I),
        cmd_reptop,
    ),
    (re.compile(r"^(\+|плюс|plus)$", re.I), cmd_plus),
    (re.compile(r"^(-|минус|minus)$", re.I), cmd_minus),
    (re.compile(r"^(реп|репутация|rep)$", re.I), cmd_rep),
    (
        re.compile(r"^(жалоба|репорт|report)(\s+.+)?$", re.I),
        cmd_report,
    ),
    (
        re.compile(r"^(инфо|кто это|профиль)(\s+.+)?$", re.I),
        cmd_info,
    ),
    (re.compile(r"^(правила|rules)$", re.I), cmd_rules),
    (
        re.compile(
            r"^(правила установить|установить правила|задать правила|правила задать)(\s+.+)?$",
            re.I,
        ),
        cmd_setrules,
    ),
    (re.compile(r"^(журнал|лог|setlog)(\s+.+)?$", re.I), cmd_setlog),
    (re.compile(r"^(помощь|команды|справка|help)$", re.I), cmd_start),
]


@router.message(F.text)
async def ru_command(message: Message):
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return
    for pattern, handler in _RU_COMMANDS:
        if pattern.match(text):
            await handler(message)
            return


# Меню команд в Telegram (кнопка "/")
BOT_COMMANDS = [
    BotCommand(command="start", description="Все команды бота"),
    BotCommand(command="help", description="Список команд"),
    BotCommand(command="top", description="Топ по сообщениям: всё / день / неделя"),
    BotCommand(command="mystats", description="Моя статистика"),
    BotCommand(command="rank", description="Мой ранг"),
    BotCommand(command="ranks", description="Иерархия рангов"),
    BotCommand(command="setrank", description="Выдать ранг (ответом)"),
    BotCommand(command="unrank", description="Снять ранг"),
    BotCommand(command="ranklist", description="Ранги в чате"),
    BotCommand(command="warn", description="Предупреждение: /warn 1ч (ответом)"),
    BotCommand(command="warns", description="Предупреждения пользователя"),
    BotCommand(command="unwarn", description="Снять предупреждение"),
    BotCommand(command="warnset", description="Срок варна по умолчанию"),
    BotCommand(command="ban", description="Забанить (ответом)"),
    BotCommand(command="unban", description="Снять бан"),
    BotCommand(command="kick", description="Исключить (ответом)"),
    BotCommand(command="mute", description="Мут: /mute 30"),
    BotCommand(command="unmute", description="Снять мут"),
    BotCommand(command="delete", description="Удалить сообщение (ответом)"),
    BotCommand(command="pin", description="Закрепить (ответом)"),
    BotCommand(command="unpin", description="Открепить"),
    BotCommand(command="watch", description="Добавить чат в автосохранение"),
    BotCommand(command="unwatch", description="Убрать чат из автосохранения"),
    BotCommand(command="watchlist", description="Чаты в автосохранении"),
    BotCommand(command="info", description="Карточка участника"),
    BotCommand(command="rep", description="Репутация участника"),
    BotCommand(command="reptop", description="Топ репутации"),
    BotCommand(command="plus", description="Плюс участнику (ответом)"),
    BotCommand(command="minus", description="Минус участнику (ответом)"),
    BotCommand(command="report", description="Жалоба на сообщение (ответом)"),
    BotCommand(command="инфо", description="Карточка участника"),
    BotCommand(command="реп", description="Репутация участника"),
    BotCommand(command="правила", description="Правила чата"),
    BotCommand(command="топ", description="Топ по сообщениям"),
    BotCommand(command="жалоба", description="Жалоба на сообщение"),
    BotCommand(command="rules", description="Правила чата"),
    BotCommand(command="setrules", description="Задать правила (админ)"),
    BotCommand(command="setlog", description="Привязать журнал действий"),
    BotCommand(command="id", description="Мой ID и ID чата"),
]


async def main():
    # Счётчик сообщений работает до хендлеров
    dp.message.outer_middleware(stats.StatsMiddleware())

    dp.include_router(stats.router)
    dp.include_router(router)
    dp.include_router(antispam.router)
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await bot.set_my_commands(BOT_COMMANDS)
        log.info("Меню команд обновлено.")
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось обновить меню команд: %s", e)
    log.info("Запуск бота...")
    try:
        await dp.start_polling(bot)
    finally:
        stats.flush()
        log.info("Статистика сохранена, бот остановлен.")


if __name__ == "__main__":
    asyncio.run(main())