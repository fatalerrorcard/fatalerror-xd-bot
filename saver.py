"""Модуль автосохранения контента (RipSave-функционал).

Бот сохраняет входящие медиа из чатов, на которые он подписан
(https://t.me/username 또는 числовой chat_id в CHAT_WATCHLIST).

Куда сохраняем:
  1) В архивный канал (ARCHIVE_CHAT) - через копирование файла + подпись.
  2) На диск (SAVE_TO_DISK) - качает файл в папку по датам.

ВАЖНО: бот сохраняет ТОЛЬКО медиа, отправленные в чаты, где он присутствует,
и только тот контент, на котором у него есть права. Массовый дамп чужих
закрытых чатов/каналов (без подписки/прав) не поддерживается и не подразумевается.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from aiogram import Bot, types

from config import config

# Список досыренных чатов (username без @ или числовой chat_id).
# Можно заполнить через /watch в личке.
watchlist: set[int | str] = set()


def _target_is_user_chat(message: types.Message) -> bool:
    """Пропускаем сообщения в личке и от системных событий."""
    if message.chat.type in ("private",):
        return False
    return True


def _filename_for(sender, message) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{sender.id if sender else 'anon'}"


def _build_caption(src: types.Message) -> str:
    """Подпись для архивного сообщения."""
    caption = ""
    if getattr(src, "caption", None):
        caption = src.caption
    elif src.text:
        caption = src.text
    sender = src.from_user
    who = f"{sender.username or sender.full_name}" if sender else "anon"
    link = (
        f"\n\n— {who} · {src.chat.title or src.chat.username or src.chat.id}"
    )
    result = (caption[: config.caption_max_len] + link)[: config.caption_max_len]
    return result or None


async def save_external(
    bot: Bot,
    msg: types.Message,
) -> tuple[bool, str]:
    """Копирует медиа в архивный канал либо сохраняет на диск.

    Возвращает (сохранено_ли, пояснение).
    """
    media_type = _detect_media(msg)
    if media_type is None or media_type not in config.save_types:
        return False, f"тип '{media_type or 'text'}' не в списке сохранения"

    # 1) На диск
    if config.save_to_disk:
        try:
            await _save_to_disk(bot, msg, media_type)
        except Exception as e:  # noqa: BLE001
            return False, f"ошибка диска: {e}"

    # 2) В архивный канал
    if config.archive_chat:
        ok, err = await _copy_to_channel(bot, msg, media_type)
        return ok, err or f"сохранено: {media_type}"
    return True, f"сохранено на диск: {media_type}"


def _detect_media(msg: types.Message) -> str | None:
    if msg.photo:
        return "photo"
    if msg.video:
        return "video"
    if msg.document:
        return "document"
    if msg.audio:
        return "audio"
    if msg.voice:
        return "voice"
    if msg.animation:
        return "animation"
    if msg.video_note:
        return "video_note"
    if msg.sticker:
        return "sticker"
    if msg.text:
        return "text"
    # ссылки - отдельно не детектируем, text уже поймал
    return None


async def _copy_to_channel(
    bot: Bot, msg: types.Message, media_type: str
) -> tuple[bool, str]:
    target = None
    # username может начинаться с @
    target = config.archive_chat
    if not target:
        return False, "не задан ARCHIVE_CHAT"

    try:
        if media_type == "photo":
            await bot.copy_message(target, msg.chat.id, msg.message_id)
        elif media_type in (
            "video", "document", "audio", "voice", "animation", "video_note", "sticker",
        ):
            await bot.copy_message(target, msg.chat.id, msg.message_id)
        elif media_type == "text":
            await bot.send_message(
                target,
                _build_caption(msg) or msg.text or "",
            )
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, f"copy ошибка: {e}"


async def _save_to_disk(bot: Bot, msg: types.Message, media_type: str):
    """Сохраняет файл на диск в SAVE_TO_DISK/<гг-мм-дд>/."""
    root = Path(config.save_to_disk).expanduser()
    day = datetime.now().strftime("%Y-%m-%d")
    folder = root / day
    folder.mkdir(parents=True, exist_ok=True)

    def _ext(original: str, default: str) -> str:
        import os

        _, ext = os.path.splitext(original or "")
        return ext or default

    sender = msg.from_user
    base = f"{datetime.now().strftime('%H%M%S')}_{sender.id if sender else 'anon'}"

    # Для фото берём наибольший размер
    if media_type == "photo":
        largest = msg.photo[-1]
        fname = f"{base}.jpg"
        await bot.download(
            largest,
            destination=folder / fname,
        )
    elif media_type == "sticker" or media_type == "animation":
        fname = f"{base}.webp" if media_type == "sticker" else f"{base}.mp4"
        await bot.download(
            getattr(msg, media_type),
            destination=folder / fname,
        )
    elif media_type == "video_note":
        fname = f"{base}.mp4"
        await bot.download(
            msg.video_note,
            destination=folder / fname,
        )
    else:
        # document / video / audio / voice
        file_attr = getattr(msg, media_type)
        dflt = {
            "document": ".bin",
            "video": ".mp4",
            "audio": ".mp3",
            "voice": ".ogg",
        }[media_type]
        fname = f"{base}{_ext(file_attr.file_name, dflt)}"
        await bot.download(
            file_attr,
            destination=folder / fname,
        )