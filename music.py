"""Новогодние мелодии: оригинальный синтез (без авторских записей).

Бот сам генерирует короткие праздничные наигрыши в WAV и присылает их
как аудиофайлы. Композиции собственные, записей чужих треков нет.
"""

from __future__ import annotations

import io
import math
import os
import random
import tempfile
import wave

import numpy as np

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

router = Router(name="music")

SR = 44100

# Мелодии: список нот в Гц и длительностей (доля: 1 = четверть; темп = bpm)
# Все три — оригинальные последовательности в «зимней» пентатонике.
MELODIES = {
    "bells": {  # "Снежные колокольчики"
        "tempo": 132,
        "notes": [
            (659.25, 0.5), (783.99, 0.5), (880.00, 1.0),
            (783.99, 0.5), (880.00, 0.5), (1046.50, 1.0),
            (880.00, 0.5), (1046.50, 0.5), (1318.51, 1.0),
            (1046.50, 1.0), (783.99, 1.0),
            (659.25, 0.5), (783.99, 0.5), (880.00, 1.0),
            (1046.50, 0.5), (1174.66, 0.5), (1318.51, 1.0),
            (1174.66, 1.5), (1046.50, 0.5),
        ],
    },
    "waltz": {  # "Зимний вальс"
        "tempo": 150,
        "notes": [
            (392.00, 1.0), (523.25, 1.0), (659.25, 1.0),
            (523.25, 0.5), (659.25, 0.5), (783.99, 1.0),
            (659.25, 1.0), (523.25, 1.0),
            (440.00, 1.0), (493.88, 1.0), (587.33, 1.0),
            (493.88, 0.5), (587.33, 0.5), (659.25, 1.0),
            (587.33, 1.0), (493.88, 1.0),
        ],
    },
    "calm": {  # "Лунный снегопад" (спокойный эмбиент)
        "tempo": 96,
        "notes": [
            (440.00, 2.0), (329.63, 2.0),
            (392.00, 2.0), (293.66, 2.0),
            (349.23, 2.0), (261.63, 2.0),
            (329.63, 2.0), (196.00, 2.0),
        ],
    },
}


def _note_to_samples(freq, dur, sr, bell=False):
    n = int(sr * dur)
    t = np.arange(n) / sr
    attack = min(int(sr * 0.02), n)
    release = min(int(sr * 0.08), n)
    env = np.ones(n)
    env[:attack] = np.linspace(0, 1, attack) if attack else np.array([])
    env[n - release:] = np.linspace(1, 0, release) if release else np.array([])

    if bell:
        # колокольчиковый тембр: основной тон + сильные обертоны
        sig = (
            np.sin(2 * math.pi * freq * t)
            + 0.55 * np.sin(2 * math.pi * freq * 2.01 * t)
            + 0.30 * np.sin(2 * math.pi * freq * 3.02 * t)
            + 0.12 * np.sin(2 * math.pi * freq * 4.05 * t)
        )
        sig /= 1.97
        decay = np.exp(-t * 2.4)
        return (sig * env * decay * 0.5).astype(np.float64)
    return (np.sin(2 * math.pi * freq * t) * env * 0.5).astype(np.float64)


def _synthesize(key: str) -> bytes:
    mel = MELODIES[key]
    tempo = mel["tempo"]
    beat = 60.0 / tempo
    bell = key == "bells"

    total = int(SR * 14.0)
    mix = np.zeros(total, dtype=np.float64)

    t = 0.0
    note_i = 0
    while note_i < len(mel["notes"]) and t < 12.0:
        freq, ln = mel["notes"][note_i]
        dur = ln * beat * 1.25  # небольшая растяжка для воздуха
        seg = _note_to_samples(freq, dur, SR, bell=bell)
        i0 = int(t * SR)
        end = min(i0 + len(seg), total)
        mix[i0:end] += seg[: end - i0]
        t += (ln + 0.10) * beat * 0.92
        note_i += 1

    # лёгкий шум снега
    rng = np.random.default_rng(42)
    noise = rng.normal(0, 1, total) * 0.006
    mix += noise

    fade = int(SR * 1.2)
    mix[: min(fade, total)] *= np.linspace(0, 1, min(fade, total))
    mix[-min(fade, total):] *= np.linspace(1, 0, min(fade, total))

    mix /= (np.max(np.abs(mix)) + 1e-9)

    # переводим в стерео
    l = mix * 0.9
    r = mix * 0.9
    pcm16 = (np.stack([l, r], axis=1) * 32000).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()


_TITLES = {
    "bells": "Снежные колокольчики",
    "waltz": "Зимний вальс",
    "calm": "Лунный снегопад",
}


def _keyboard():
    rows = []
    for key, title in _TITLES.items():
        rows.append([
            types.InlineKeyboardButton(
                text=f"🎄 {title}",
                callback_data=f"music:{key}",
            )
        ])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("music", "музыка"))
async def cmd_music(message: Message):
    await message.reply(
        "Новогодние мелодии (собственный синтез, без чужих записей):\n"
        "выбери трек",
        reply_markup=_keyboard(),
    )


@router.callback_query(F.data.startswith("music:"))
async def on_music(cb: CallbackQuery):
    key = cb.data.split(":", 1)[1]
    if key not in MELODIES:
        return await cb.answer("Неизвестный трек", show_alert=True)
    await cb.answer("Генерирую…", show_alert=False)

    try:
        data = _synthesize(key)
        fname = f"{key}_{random.randint(1000, 9999)}.wav"
        audio = BufferedInputFile(data, filename=fname)
        await cb.message.answer_audio(
            audio,
            caption=f"🎄 {_TITLES[key]} — Новогодний наигрыш от FatalerrorBot",
        )
    except Exception as e:  # noqa: BLE001
        await cb.message.answer(f"Не получилось: {e}")