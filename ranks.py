"""Система рангов (как в Iris): младший модер → старший админ + владелец.

Ранги хранятся per-chat в ranks.json рядом с кодом.
Уровни:
  1 — Младший модер
  2 — Старший модер
  3 — Младший админ
  4 — Старший админ
  5 — Владелец (наследуется: username владельца, ADMIN_IDS, создатель чата)
"""

from __future__ import annotations

import storage

# Порядок от младшего к старшему
RANKS: dict[str, dict] = {
    "junior_moderator": {"level": 1, "title": "Младший модер"},
    "senior_moderator": {"level": 2, "title": "Старший модер"},
    "junior_admin": {"level": 3, "title": "Младший админ"},
    "senior_admin": {"level": 4, "title": "Старший админ"},
}
OWNER_LEVEL = 5
OWNER_TITLE = "Владелец"

# Алиасы ввода (рус/англ)
ALIASES: dict[str, str] = {
    "модер": "junior_moderator",
    "младший модер": "junior_moderator",
    "млмодер": "junior_moderator",
    "мл.модер": "junior_moderator",
    "младший модератор": "junior_moderator",
    "junior_moderator": "junior_moderator",
    "junior_mod": "junior_moderator",
    "jmod": "junior_moderator",
    "старший модер": "senior_moderator",
    "стмодер": "senior_moderator",
    "ст.модер": "senior_moderator",
    "старший модератор": "senior_moderator",
    "senior_moderator": "senior_moderator",
    "senior_mod": "senior_moderator",
    "smod": "senior_moderator",
    "младший админ": "junior_admin",
    "млдадмин": "junior_admin",
    "мл.админ": "junior_admin",
    "админ": "junior_admin",
    "junior_admin": "junior_admin",
    "jadmin": "junior_admin",
    "старший админ": "senior_admin",
    "стадмин": "senior_admin",
    "ст.админ": "senior_admin",
    "senior_admin": "senior_admin",
    "sadmin": "senior_admin",
    "владелец": "owner",
    "owner": "owner",
}

# Минимальный уровень для действия
PERM_LEVELS: dict[str, int] = {
    "delete": 1,
    "mute": 1,
    "unmute": 2,
    "ban": 2,
    "unban": 2,
    "kick": 2,
    "pin": 2,
    "assign_moderator": 3,
    "assign_junior_admin": 4,
    "assign_senior_admin": 5,
}

_FILE = "ranks.json"


def _load() -> dict:
    return storage.load(_FILE, {})


def _save(data: dict) -> None:
    storage.save(_FILE, data)


def normalize_rank(name: str) -> str | None:
    """Превращает ввод пользователя ('стмодер') в ключ ранга ('senior_moderator')."""
    key = " ".join(name.strip().lower().lstrip("@").split())
    return ALIASES.get(key)


def rank_title(rank: str | None) -> str:
    if not rank:
        return "—"
    if rank == "owner":
        return OWNER_TITLE
    return RANKS.get(rank, {}).get("title", rank)


def rank_level(rank: str | None) -> int:
    if not rank:
        return 0
    if rank == "owner":
        return OWNER_LEVEL
    return RANKS.get(rank, {}).get("level", 0)


def get_rank(chat_id: int, user_id: int) -> str | None:
    return _load().get(str(chat_id), {}).get(str(user_id))


def set_rank(chat_id: int, user_id: int, rank: str | None) -> None:
    data = _load()
    chat = data.setdefault(str(chat_id), {})
    if rank is None:
        chat.pop(str(user_id), None)
    else:
        chat[str(user_id)] = rank
    _save(data)


def chat_ranks(chat_id: int) -> dict[int, str]:
    raw = _load().get(str(chat_id), {})
    result: dict[int, str] = {}
    for uid, rank in raw.items():
        try:
            result[int(uid)] = rank
        except (TypeError, ValueError):
            continue
    return result


def assign_perm_for(rank: str) -> str | None:
    """Какое право нужно, чтобы назначить этот ранг."""
    if rank in ("junior_moderator", "senior_moderator"):
        return "assign_moderator"
    if rank == "junior_admin":
        return "assign_junior_admin"
    if rank == "senior_admin":
        return "assign_senior_admin"
    return None


def hierarchy_text() -> str:
    lines = [
        f"{OWNER_LEVEL}. {OWNER_TITLE} — всё, выдача любых рангов",
        "4. Старший админ — бан/кик/мут/удаление/закреп + выдача младших админов",
        "3. Младший админ — бан/кик/мут/удаление/закреп + выдача модеров",
        "2. Старший модер — бан/кик/мут/удаление/закреп",
        "1. Младший модер — мут и удаление сообщений",
    ]
    return "\n".join(lines)