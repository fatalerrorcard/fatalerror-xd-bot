"""Новогодние мини-игры: крестики-нолики, угадай число, викторина.

Состояние игр хранится в памяти (per-chat, per-user). Для "угадай число"
и викторины достаточно команд/кнопок; крестики-нолики - через инлайн.
"""

from __future__ import annotations

import random
import time

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

router = Router(name="games")

# ---------- Крестики-нолики ----------
# _ttt[chat_id][user_id] = {"board": [0..8], "turn": "user"/"bot", "score_user": n, "score_bot": n}
_ttt: dict[int, dict[int, dict]] = {}


def _ttt_wins(board) -> bool:
    win = [
        (0, 1, 2), (3, 4, 5), (6, 7, 8),
        (0, 3, 6), (1, 4, 7), (2, 5, 8),
        (0, 4, 8), (2, 4, 6),
    ]
    for a, b, c in win:
        if board[a] != 0 and board[a] == board[b] == board[c]:
            return True
    return False


def _ttt_free(board) -> list[int]:
    return [i for i, v in enumerate(board) if v == 0]


def _ttt_bot_move(board):
    # стратегия: центр, затем случайный свободный
    free = _ttt_free(board)
    if not free:
        return None
    if 4 in free:
        return 4
    return random.choice(free)


def _ttt_board_text(board) -> str:
    sym = {0: "·", 1: "X", 2: "O"}
    rows = []
    for r in range(3):
        rows.append(" ".join(sym[board[r * 3 + c]] for c in range(3)))
    lines = ["```", "\n".join(rows), "```"]
    return "\n".join(lines)


def _ttt_keyboard(board, chat_id, user_id):
    rows = []
    for r in range(3):
        row = []
        for c in range(3):
            v = board[r * 3 + c]
            if v == 0:
                label = f"[{r * 3 + c + 1}]"
                data = f"ttt:{chat_id}:{user_id}:{r * 3 + c}"
            else:
                label = "X" if v == 1 else "O"
                data = f"ttt:no:{user_id}:{r * 3 + c}"
            row.append(types.InlineKeyboardButton(text=label, callback_data=data))
        rows.append(row)
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("ttt", "крестики"))
async def cmd_ttt(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        return await message.reply("Играем в группе.")
    uid = message.from_user.id
    chat_id = message.chat.id
    _ttt.setdefault(chat_id, {})[uid] = {
        "board": [0] * 9,
        "turn": "user",
        "score_user": 0,
        "score_bot": 0,
    }
    st = _ttt[chat_id][uid]
    await message.reply(
        "Крестики-нолики. Ты играешь за X.\n"
        "Нажимай свободные клетки на поле.\n"
        + _ttt_board_text(st["board"]),
        reply_markup=_ttt_keyboard(st["board"], chat_id, uid),
    )


@router.callback_query(F.data.startswith("ttt:"))
async def on_ttt(cb: CallbackQuery):
    try:
        _, chat_raw, user_raw, cell_raw = cb.data.split(":")
        chat_id = int(chat_raw)
        user_id = int(user_raw)
        cell = int(cell_raw)
    except (ValueError, IndexError):
        return await cb.answer("Старая сессия — начните /крестики заново", show_alert=True)

    if cb.from_user.id != user_id:
        return await cb.answer("Не твоя игра", show_alert=True)

    st = _ttt.get(chat_id, {}).get(user_id)
    if not st:
        return await cb.answer("Игра не найдена — начните /крестики", show_alert=True)

    board = st["board"]
    if st["turn"] != "user":
        return await cb.answer("Сейчас ходит бот", show_alert=True)
    if board[cell] != 0:
        return await cb.answer("Клетка занята", show_alert=True)

    board[cell] = 1  # X пользователя
    if _ttt_wins(board):
        st["score_user"] += 1
        msg = f"Ты выиграл! 🎄 Счёт: ты {st['score_user']} — бот {st['score_bot']}"
        await cb.message.edit_text(
            msg + "\n" + _ttt_board_text(board)
        )
        # новая партия
        st["board"] = [0] * 9
        st["turn"] = "user"
        return await cb.message.reply(
            "Новая партия. Ты за X.\n" + _ttt_board_text(st["board"]),
            reply_markup=_ttt_keyboard(st["board"], chat_id, user_id),
        )

    if not _ttt_free(board):
        msg = "Ничья 😌"
        st["board"] = [0] * 9
        st["turn"] = "user"
        return await cb.message.edit_text(msg)

    # ход бота
    st["turn"] = "bot"
    mv = _ttt_bot_move(board)
    if mv is not None:
        board[mv] = 2
    st["turn"] = "user"

    if _ttt_wins(board):
        st["score_bot"] += 1
        msg = f"Бот выиграл. Счёт: ты {st['score_user']} — бот {st['score_bot']}"
        await cb.message.edit_text(
            msg + "\n" + _ttt_board_text(board)
        )
        st["board"] = [0] * 9
        return await cb.message.reply(
            "Новая партия. Ты за X.\n" + _ttt_board_text(st["board"]),
            reply_markup=_ttt_keyboard(st["board"], chat_id, user_id),
        )

    if not _ttt_free(board):
        st["board"] = [0] * 9
        return await cb.message.edit_text("Ничья 😌")

    await cb.message.edit_text(
        "Твой ход (за X):\n" + _ttt_board_text(board),
        reply_markup=_ttt_keyboard(board, chat_id, user_id),
    )
    await cb.answer()


# ---------- Угадай число ----------
_guess: dict[tuple[int, int], dict] = {}


@router.message(Command("guess", "число"))
async def cmd_guess(message: Message):
    if not message.from_user:
        return
    key = (message.chat.id, message.from_user.id)
    _guess[key] = {
        "secret": random.randint(1, 100),
        "tries": 0,
        "started": time.time(),
    }
    await message.reply(
        "Я загадал число от 1 до 100." "\n"
        "Пиши «б» <число> или просто число, чтобы угадать.\n"
        "Начать заново: /число"
    )


@router.message(Command("bet", "б"))
async def cmd_bet(message: Message):
    if not message.from_user:
        return
    key = (message.chat.id, message.from_user.id)
    st = _guess.get(key)
    if not st:
        return await message.reply("Сначала /число — я загадаю.")
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        return await message.reply("Пришли число."
        " Например: б 42")

    n = int(parts[1])
    st["tries"] += 1
    if n < st["secret"]:
        return await message.reply("Больше.")
    if n > st["secret"]:
        return await message.reply("Меньше.")
    tried = st["tries"]
    del _guess[key]
    await message.reply(
        f"Правильно! Число было {st['secret']}. "
        f"Угадал за {tried} попыток. 🎄"
    )


# ---------- Новогодняя викторина ----------
QUIZ = [
    {
        "q": "Кто традиционно приносит подарки под ёлку в России?",
        "options": ["Дед Мороз", "Санта-Клаус", "Зубная фея", "Баба Яга"],
        "answer": 0,
    },
    {
        "q": "Что обязательно наряжают к Новому году?",
        "options": ["Ёлку", "Кактус", "Берёзу", "Пальму"],
        "answer": 0,
    },
    {
        "q": "Классический новогодний салат — это…",
        "options": ["Оливье", "Цезарь", "Винегрет", "Мимоза"],
        "answer": 0,
    },
    {
        "q": "В какой стране Новый год встречают раньше всего?",
        "options": ["Новая Зеландия", "Россия", "США", "Бразилия"],
        "answer": 0,
    },
    {
        "q": "Сколько ударов отбивают куранты в новогоднюю ночь?",
        "options": ["12", "10", "24", "5"],
        "answer": 0,
    },
]

_quiz: dict[tuple[int, int], dict] = {}


def _quiz_keyboard(chat_id, user_id):
    st = _quiz[(chat_id, user_id)]
    q = QUIZ[st["idx"]]
    rows = []
    for i, opt in enumerate(q["options"]):
        rows.append([
            types.InlineKeyboardButton(
                text=opt,
                callback_data=f"quiz:{chat_id}:{user_id}:{i}",
            )
        ])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("quiz", "викторина"))
async def cmd_quiz(message: Message):
    if not message.from_user:
        return
    key = (message.chat.id, message.from_user.id)
    _quiz[key] = {"idx": 0, "score": 0}
    st = _quiz[key]
    q = QUIZ[0]
    await message.reply(
        f"Новогодняя викторина. Вопрос 1/5:\n{q['q']}",
        reply_markup=_quiz_keyboard(message.chat.id, message.from_user.id),
    )


@router.callback_query(F.data.startswith("quiz:"))
async def on_quiz(cb: CallbackQuery):
    try:
        _, chat_raw, user_raw, opt_raw = cb.data.split(":")
        chat_id = int(chat_raw)
        user_id = int(user_raw)
        opt = int(opt_raw)
    except (ValueError, IndexError):
        return await cb.answer("Старая викторина — начните /викторина", show_alert=True)

    if cb.from_user.id != user_id:
        return await cb.answer("Это не твоя викторина", show_alert=True)

    st = _quiz.get((chat_id, user_id))
    if not st:
        return await cb.answer("Нет активной викторины", show_alert=True)

    q = QUIZ[st["idx"]]
    correct = opt == q["answer"]
    if correct:
        st["score"] += 1

    st["idx"] += 1
    if st["idx"] >= len(QUIZ):
        score = st["score"]
        del _quiz[(chat_id, user_id)]
        verdict = "отлично! 🎄" if score >= 4 else "неплохо 😄" if score >= 2 else "повезёт в следующем году 🙂"
        return await cb.message.edit_text(
            f"Викторина окончена! Твой результат: {score}/5 — {verdict}"
        )

    nq = QUIZ[st["idx"]]
    await cb.message.edit_text(
        f"{'Верно! ✅' if correct else 'Мимо ❌'} "
        f"Счёт: {st['score']}\n\n"
        f"Вопрос {st['idx'] + 1}/5:\n{nq['q']}",
        reply_markup=_quiz_keyboard(chat_id, user_id),
    )
    await cb.answer()


# ---------- Предсказания на новый год ----------
PREDICTIONS = [
    "В январе ты находишь под ёлкой то, что искал весь год.",
    "Февраль принесёт неожиданные сообщения. Отвечай подумав.",
    "В марте заработает то, что давно не работало.",
    "Апрель — удачный для новых знакомств прямо в этом чате.",
    "В мае тебя ждёт маленькая, но приятная победа.",
    "Июнь подарит хорошие новости от старых друзей.",
    "В июле кто-то вспомнит о тебе добрым словом.",
    "Август — отличный месяц, чтобы наконец что-то начать.",
    "В сентябре случится то, что изменит твои планы в лучшую сторону.",
    "Октябрь проверит твою смелость. Не спрячешься — выиграешь.",
    "Ноябрь — месяц тихих побед.",
    "В декабре сбудутся слова, сказанные в этом году.",
    "Скоро тебе улыбнётся удача — буквально в этом чате.",
    "Твоё следующее сообщение кому-то очень поможет.",
    "Не трать судьбу на мелочи: жди главный подарок под ёлкой.",
    "В новом году тебя назовут «самым активным». И это будут говорить всерьёз.",
]


def _forecast_keyboard():
    return types.InlineKeyboardMarkup(
        inline_keyboard=[[
            types.InlineKeyboardButton(
                text="🎄 Ещё предсказание",
                callback_data="forecast:again",
            )
        ]]
    )


@router.message(Command("forecast", "предсказание", "гадание"))
async def cmd_forecast(message: Message):
    if not message.from_user:
        return
    text = random.choice(PREDICTIONS)
    await message.reply(
        f"🔮 Предсказание на новый год для {message.from_user.full_name or 'тебя'}:\n\n{text}",
        reply_markup=_forecast_keyboard(),
    )


@router.callback_query(F.data.startswith("forecast:"))
async def on_forecast(cb: CallbackQuery):
    if cb.data.split(":", 1)[1] != "again":
        return await cb.answer()
    text = random.choice(PREDICTIONS)
    # заменяем текст на новое предсказание, кнопка остаётся
    await cb.message.edit_text(
        f"🔮 Ещё предсказание:\n\n{text}",
        reply_markup=_forecast_keyboard(),
    )
    await cb.answer()