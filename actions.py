"""Команды-действия: обнять, поцеловать, погладить, ударить, выебать, убить, воскресить.

Как в Ирис: «обнять» (или /hug) ответом на сообщение либо с @упоминанием —
бот отвечает случайной фразой про автора и цель. Без цели — действие
направляется на бота или случайное обращение.
"""

from __future__ import annotations

import random
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name="actions")

ACTIONS = {
    "hug": {
        "names": ("обнять", "обнимаю", "обнять бы"),
        "templates": [
            "{actor} крепко обнимает {target} 🤗",
            "{actor} нежно обнимает {target} и шепчет: всё будет хорошо 💚",
            "{actor} обнимает {target} так, что трещат косточки 🫂",
            "{actor} подбегает к {target} и обнимает со всей души 🥰",
            "{actor} зарывается в объятия {target} и не хочет отпускать ✨",
            "{actor} осторожно обнимает {target}, чтобы не сломать 😌",
        ],
    },
    "kiss": {
        "names": ("поцеловать", "целую", "поцелуй"),
        "templates": [
            "{actor} целует {target} в щёчку 😘",
            "{actor} чмокает {target} в лоб 💋",
            "{actor} по-настоящему целует {target} 🌹",
            "{actor} оставляет нежный поцелуй на щеке {target} 😚",
            "{actor} целует {target} и смущается 💕",
            "{actor} хватает {target} и целует, пока никто не видит 🤭",
        ],
    },
    "pet": {
        "names": ("погладить", "глажу", "поглажу"),
        "templates": [
            "{actor} гладит {target} по голове 🐾",
            "{actor} ласково треплет {target} по волосам ✋",
            "{actor} гладит {target}, как котёнка, по спинке 😸",
            "{actor} чешет {target} за ушком 🐱",
            "{actor} успокаивающе поглаживает {target} 🌿",
            "{actor} гладит {target} и говорит, что тот молодец 🌟",
        ],
    },
    "hit": {
        "names": ("ударить", "ударю", "бью"),
        "templates": [
            "{actor} даёт {target} подзатыльник 👊",
            "{actor} ударяет {target} подушкой (по-доброму) 🛏",
            "{actor} бьёт {target} по руке и убегает 🏃",
            "{actor} шлёпает {target} газетой по попе 📰",
            "{actor} даёт {target} щелбан 💥",
            "{actor} легонько ударяет {target} в плечо: за дело 😤",
        ],
    },
    "fuck": {
        "names": ("выебать", "выябать"),
        "templates": [
            "{actor} выебывает {target} на радость всем вокруг 🔞",
            "{actor} выебывает {target} так, что весь чат в шоке 😈",
            "{actor} делает с {target} такие вещи, о которых лучше молчать 😏",
            "{actor} выебывает {target} и доволен собой 💪",
            "{actor} устраивает {target} незабываемую ночь 🌙",
            "{actor} выебывает {target} — теперь тот не сядет. Шутка. Или нет 😏",
        ],
    },
    "kill": {
        "names": ("убить", "убиваю", "уничтожить"),
        "templates": [
            "{actor} убивает {target} (в игровом смысле) 💀",
            "{actor} устраняет {target} из чата навсегда ⚰️",
            "{actor} делает {target} рука-лицо, и {target} падает без чувств 🤕",
            "{actor} прибивает {target} и вешает табличку «не воскрешать» 🪦",
            "{actor} убивает {target}. Бедный. Или нет? 🤔",
            "{actor} одним ударом отправляет {target} в некст лайф ☠️",
        ],
    },
    "revive": {
        "names": ("воскресить", "воскрешаю", "оживить"),
        "templates": [
            "{actor} возвращает {target} из мёртвых ⚡",
            "{actor} воскрешает {target} — сегодня повезло ✨",
            "{actor} откачивает {target} и ставит на ноги 💚",
            "{actor} оживляет {target} под аплодисменты чата 👏",
            "{actor} воскрешает {target} со второй попытки 🙏",
            "{actor} рождает {target} заново из пепла 🐦‍🔥",
        ],
    },
}

# Дополнительные обращения, когда цели не указано
EXTRA = (
    "сладкий(-ая)",
    "дорогой(-ая)",
    "милаш",
    "зайка",
    "солнышко",
    "красавчик",
    "босс",
    "мемный красавчик",
    "милашка",
    "забудька",
)


def _target_name(message: Message) -> str | None:
    """Цель действия: ответ на сообщение или @упоминание в тексте."""
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        return u.full_name or (f"@{u.username}" if u.username else "пользователь")
    text = (message.text or "") + " " + (message.caption or "")
    for word in text.split():
        if word.startswith("@"):
            return word
    return None


def _render(message: Message, action: str, /) -> str:
    actor = message.from_user.full_name if message.from_user else "Кто-то"
    data = ACTIONS[action]
    target = _target_name(message)
    if not target:
        if random.random() < 0.3:
            target = "тебя"
        else:
            target = random.choice(EXTRA)
    return random.choice(data["templates"]).format(actor=actor, target=target)


def _make_handler(action: str):
    async def handler(message: Message):
        await message.answer(_render(message, action), disable_web_page_preview=True)

    return handler


# Команды со слэшем: /hug /kiss /pet /hit /fuck /kill /revive
for _action in ACTIONS:
    router.message.register(_make_handler(_action), Command(_action, ignore_case=True))

# Русские алиасы без слэша: «обнять», «поцеловать», «выебать» и т.д.
for _action, _data in ACTIONS.items():
    _pattern = r"^(" + "|".join(re.escape(n) for n in _data["names"]) + r")$"
    router.message.register(
        _make_handler(_action),
        F.text.regexp(_pattern, flags=re.IGNORECASE),
    )