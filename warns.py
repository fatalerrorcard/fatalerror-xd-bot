"""Система предупреждений (варнов).

- У каждого предупреждения есть срок действия; истёкшие автоматически не считаются.
- Когда активных предупреждений достигает лимита, пользователь банится.
- Срок по умолчанию и лимит задаются в config (WARN_LIMIT, WARN_TTL_DAYS)
  либо сроками при выдаче: варн 30м · варн 1ч · варн 7д · варн 1мес.
"""

from __future__ import annotations

import re
import time

import storage

_FILE = "warns.json"

_UNITS: dict[str, int] = {
    # минуты
    "м": 60, "мин": 60, "минута": 60, "минуты": 60, "минут": 60,
    "m": 60, "min": 60,
    # часы
    "ч": 3600, "час": 3600, "часа": 3600, "часов": 3600,
    "h": 3600, "hour": 3600, "hours": 3600,
    # дни
    "д": 86400, "день": 86400, "дня": 86400, "дней": 86400,
    "d": 86400, "day": 86400, "days": 86400,
    # недели
    "нед": 604800, "неделя": 604800, "недели": 604800, "недель": 604800,
    "w": 604800, "week": 604800, "weeks": 604800,
    # месяцы (условные, 30 дней)
    "мес": 2592000, "месяц": 2592000, "месяца": 2592000, "месяцев": 2592000,
    "mo": 2592000, "month": 2592000, "months": 2592000,
}


def _load() -> dict:
    return storage.load(_FILE, {})


def _save(data: dict) -> None:
    storage.save(_FILE, data)


def parse_duration(text: str) -> int | None:
    """'30м' -> 1800, '1ч' -> 3600, '7д' -> 604800, '1мес' -> 2592000.

    Без единицы число считается днями.
    """
    raw = (text or "").strip().lower().replace(" ", "")
    m = re.match(r"^(\d+)([а-яёa-z]*)$", raw)
    if not m:
        return None
    amount = int(m.group(1))
    unit = m.group(2) or "д"
    mult = _UNITS.get(unit)
    if mult is None:
        return None
    return amount * mult


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def format_duration(seconds: int) -> str:
    """Человеко-читаемый срок."""
    seconds = max(int(seconds), 0)
    if seconds < 60:
        n = max(seconds, 1)
        return f"{n} {_plural(n, 'секунда', 'секунды', 'секунд')}"
    if seconds < 3600:
        n = seconds // 60
        return f"{n} {_plural(n, 'минута', 'минуты', 'минут')}"
    if seconds < 86400:
        n = seconds // 3600
        return f"{n} {_plural(n, 'час', 'часа', 'часов')}"
    if seconds < 604800:
        n = seconds // 86400
        return f"{n} {_plural(n, 'день', 'дня', 'дней')}"
    if seconds < 2592000:
        n = seconds // 604800
        return f"{n} {_plural(n, 'неделя', 'недели', 'недель')}"
    n = seconds // 2592000
    return f"{n} {_plural(n, 'месяц', 'месяца', 'месяцев')}"


def _chat(data: dict, chat_id: int) -> dict:
    return data.setdefault(str(chat_id), {})


def _active_list(rec: dict, now: float | None = None) -> list[float]:
    now = time.time() if now is None else now
    expires = rec.get("expire") or []
    return sorted(float(x) for x in expires if float(x) > now)


def active_count(chat_id: int, user_id: int) -> int:
    data = _load()
    rec = _chat(data, chat_id).get(str(user_id)) or {}
    return len(_active_list(rec))


def add_warn(
    chat_id: int, user_id: int, ttl_seconds: int, name: str = ""
) -> tuple[int, float]:
    """Добавить предупреждение. Возвращает (активных, когда истекает последнее)."""
    data = _load()
    chat = _chat(data, chat_id)
    rec = chat.setdefault(str(user_id), {"name": "", "expire": []})
    if name:
        rec["name"] = name
    expires = _active_list(rec)
    expires.append(time.time() + ttl_seconds)
    rec["expire"] = expires
    _save(data)
    return len(expires), expires[-1]


def list_warns(chat_id: int, user_id: int) -> list[float]:
    """Активные предупреждения (unix-время истечения)."""
    data = _load()
    rec = _chat(data, chat_id).get(str(user_id)) or {}
    return _active_list(rec)


def remove_last(chat_id: int, user_id: int) -> bool:
    """Снять последнее активное предупреждение."""
    data = _load()
    rec = _chat(data, chat_id).get(str(user_id)) or {}
    expires = _active_list(rec)
    if not expires:
        return False
    expires.pop()
    rec["expire"] = expires
    _save(data)
    return True


def clear(chat_id: int, user_id: int) -> None:
    data = _load()
    chat = _chat(data, chat_id)
    chat.pop(str(user_id), None)
    _save(data)


def get_default_ttl(chat_id: int, fallback: int) -> int:
    """Срок по умолчанию для чата (секунды)."""
    data = _load()
    val = _chat(data, chat_id).get("_default_ttl")
    try:
        return int(val) if val else fallback
    except (TypeError, ValueError):
        return fallback


def set_default_ttl(chat_id: int, seconds: int) -> None:
    data = _load()
    _chat(data, chat_id)["_default_ttl"] = int(seconds)
    _save(data)