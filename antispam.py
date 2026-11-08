"""Антиспам / флуд-контроль, автобан за рекламу и приветствие для новых участников."""

from __future__ import annotations

import re
import time
from collections import defaultdict

from aiogram import Bot, Router, types
from aiogram.filters import Command

import settings
from config import config
from moderation import ban_user, mute_user

router = Router(name="antispam")

# user_id -> list[timestamps]
_flood: dict[int, list[float]] = defaultdict(list)

# Проверка на рекламу: сторонние каналы/чаты, биржи ссылок, рекламные маркеры.
# Используется только при включённом автобане (настройка чата /adban on).
_AD_LINK = re.compile(
    r"(?:t(?:elegram)?\.me/|t(?:elegram)?\.org/|vk\.com/|"
    r"https?://|www\.)(?!/bot|/addstickers)[\w\-./?=%@+&#]+",
    re.I,
)
_AD_TOKEN = re.compile(
    r"(?:канал|чат|группа|подпишись|подписка|зайди|заходи|перейди|"
    r"переходи|лотерея|розыгрыш|бесплатно|бонус|заработ(?:ок|ай)|"
    r"раскрут|реклам|баннер|спам)\b[\w\s\-@.]{0,40}",
    re.I,
)


def is_advertising(text: str) -> bool:
    """Эвристика: считаем сообщение рекламным."""
    if not text:
        return False
    low = text.lower()
    # ссылки на сторонние telegram-каналы/чаты и приглашения — обычно реклама
    if re.search(
        r"t(?:elegram)?\.(?:me|org)/(?!bot|addstickers)(?:\+[\w-]+|[\w@]{3,}(?:/joinchat)?)",
        low,
    ):
        return True
    # сторонняя ссылка + рекламный маркер рядом
    if _AD_LINK.search(text) and _AD_TOKEN.search(low):
        return True
    return False


def _is_flood(user_id: int) -> bool:
    now = time.time()
    stamps = [t for t in _flood[user_id] if now - t < config.flood_window_sec]
    stamps.append(now)
    _flood[user_id] = stamps
    return len(stamps) > config.flood_trigger


@router.message(Command("floodtest"))
async def _flood_test(message: types.Message):
    # служебная: посмотреть настройки флуд-защиты
    state = "ВКЛЮЧЕНА" if config.flood_enabled else "ВЫКЛЮЧЕНА (бот не удаляет и не мутит)"
    await message.reply(
        f"Флуд-защита: {state}\n"
        f"окно={config.flood_window_sec} сек, порог={config.flood_trigger}, "
        f"мут={config.flood_mute_min} мин"
    )


@router.message()
async def flood_check(message: types.Message):
    # Флуд-защита отключена по умолчанию (FLOOD_ENABLED=0).
    # Она НЕ должна удалять сообщения и мутить участников "просто так".
    if not config.flood_enabled:
        return
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
    if not new:
        return

    # Антирейд: если включён, баним всех новых (кроме админов и ботов-админов).
    if settings.antiraid_enabled(message.chat.id):
        for member in new:
            if member.is_bot:
                continue
            try:
                me = await message.bot.get_me()
                if member.id == me.id:
                    continue
                mem = await message.bot.get_chat_member(message.chat.id, member.id)
                if mem.status in ("administrator", "creator"):
                    continue
            except Exception:  # noqa: BLE001
                pass
            try:
                await ban_user(message.bot, message.chat.id, member.id)
                if int(member.id) != me.id:
                    await message.answer(
                        f"Система: антирейд. Новичок {member.full_name} забанен.",
                        disable_web_page_preview=True,
                    )
                import actionlog

                await actionlog.log_action(
                    message.bot,
                    message.chat.id,
                    "АНТИРЕЙД",
                    f"{member.full_name or '?'} [id:{member.id}] · авто-бан новичка",
                )
            except Exception as e:  # noqa: BLE001
                try:
                    await message.answer(
                        f"Система: антирейд не смог забанить {member.full_name}: {e}",
                        disable_web_page_preview=True,
                    )
                except Exception:  # noqa: BLE001
                    pass
        return

    if not config.welcome_enabled:
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


@router.message()
async def auto_ban_advertising(message: types.Message):
    """Автобан за рекламу: работает только в группах, где включён /adban on."""
    chat = message.chat
    if chat.type in ("private", "channel"):
        return
    if not settings.adban_enabled(chat.id):
        return
    user = message.from_user
    if not user or user.is_bot:
        return
    text = message.text or message.caption or ""
    if not is_advertising(text):
        return
    # админов не баним
    try:
        member = await message.bot.get_chat_member(chat.id, user.id)
        if member.status in ("administrator", "creator"):
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        await ban_user(message.bot, chat.id, user.id)
        details = f"{user.full_name or '?'}"
        if user.username:
            details += f" (@{user.username})"
        details += f" [id:{user.id}]"
        await message.answer(
            f"Система: реклама запрещена. {details} забанен автоматически.",
            disable_web_page_preview=True,
        )
        import actionlog

        await actionlog.log_action(
            message.bot, chat.id, "АВТОБАН", f"{details} · источник: авто"
        )
    except Exception:  # noqa: BLE001
        pass