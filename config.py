import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # Токен бота (@BotFather -> /newbot).
    # Значение вшито по умолчанию, чтобы бот работал без переменных окружения
    # (Bothost, Render и т.п.). Переменная окружения BOT_TOKEN имеет приоритет.
    bot_token: str = os.getenv(
        "BOT_TOKEN",
        "8941070557:AAGnNKTwiDS-n0AvxnJ0r_wxT5ubuuOTkuQ",
    )

    # ID администраторов (цифровые, можно несколько через запятую).
    # Вшит ID владельца по умолчанию; ADMIN_IDS из окружения переопределяет.
    admin_ids: list[int] = field(
        default_factory=lambda: (
            [
                int(x.strip())
                for x in os.getenv("ADMIN_IDS", "").split(",")
                if x.strip().isdigit()
            ]
            or [1087968824]
        )
    )

    # Username владельца (без @): получит доступ ко всем командам
    # в любом чате, даже если не админ этого чата.
    owner_username: str = field(
        default_factory=lambda: (
            os.getenv("OWNER_USERNAME", "").strip().lstrip("@").lower()
            or "fatalerror333"
        )
    )

    # Канал, куда бот пересылает сохранённый контент
    # @username канала или числовой chat_id
    archive_chat: str = os.getenv("ARCHIVE_CHAT", "")

    # Что сохранять: photo | video | document | audio | voice | animation | link | text
    save_types: list[str] = field(
        default_factory=lambda: [
            x.strip()
            for x in os.getenv("SAVE_TYPES", "photo,video,document,audio,voice,animation").split(",")
            if x.strip()
        ]
    )

    # Пересылать сообщение с подписью (ссылка на источник)
    with_caption: bool = os.getenv("WITH_CAPTION", "1") == "1"

    # Максимальная длина подписи в архиве
    caption_max_len: int = int(os.getenv("CAPTION_MAX_LEN", "500"))

    # ===== Модерация =====
    flood_trigger: int = int(os.getenv("FLOOD_TRIGGER", "4"))       # сообщений за окно
    flood_window_sec: int = int(os.getenv("FLOOD_WINDOW_SEC", "6")) # окно, сек
    flood_mute_min: int = int(os.getenv("FLOOD_MUTE_MIN", "10"))    # мут за флуд, мин
    welcome_enabled: bool = os.getenv("WELCOME_ENABLED", "1") == "1"
    welcome_text: str = os.getenv(
        "WELCOME_TEXT",
        "Добро пожаловать, {name}! Читай правила и будь активным.",
    )

    # Директория для сохранения файлов на диск (копия вместо пересылки)
    save_to_disk: str = os.getenv("SAVE_TO_DISK", "")


config = Config()

if not config.bot_token:
    raise SystemExit("ERROR: Переменная BOT_TOKEN не задана. Заполните .env")
if not config.admin_ids:
    raise SystemExit("ERROR: Укажите ADMIN_IDS (числовые ID админов) в .env")