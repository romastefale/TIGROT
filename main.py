import os
import re
import sys
import time
import asyncio
import logging
import requests
import telegram.error
from threading import Lock

from concurrent.futures import ThreadPoolExecutor
from telegram import (
    Update,
    InlineQueryResultPhoto,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    InlineQueryHandler,
    MessageHandler,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    filters
)

# =========================
# CONFIGURAÇÃO DE LOGS
# =========================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# =========================
# VARIÁVEIS DE AMBIENTE (RAILWAY)
# =========================
TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", TOKEN.replace(":", "")[:20] if TOKEN else None)

ADMIN_ID_RAW = os.getenv("ADMIN_ID")
try:
    ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW else None
except ValueError:
    logger.warning("Invalid ADMIN_ID value, bot will run without admin restriction.")
    ADMIN_ID = None

try:
    PORT = int(os.getenv("PORT", 8080))
except ValueError:
    logger.warning("Invalid PORT value, defaulting to 8080")
    PORT = 8080

if not TOKEN:
    raise ValueError("Configure TELEGRAM_TOKEN nas variáveis do Railway")

# =========================
# VARIÁVEIS GLOBAIS
# =========================
session = requests.Session()
cache = {}
cache_lock = Lock()
CACHE_MAX_SIZE = 500
_executor = ThreadPoolExecutor(max_workers=4)

# =========================
# PADRÃO DE CARACTERES PROIBIDOS
# =========================
FORBIDDEN_PATTERN = re.compile(
    r'['
    r'\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF'  # Árabe
    r'\u0400-\u04FF\u0500-\u052F\u2DE0-\u2DFF\uA640-\uA69F'               # Cirílico
    r'\u4E00-\u9FFF\u3400-\u4DBF'                                         # Chinês
    r'\u0900-\u097F'                                                      # Hindi (Devanagari)
    r'\u0980-\u09FF'                                                      # Bengali
    r']'
)

# =========================
# FUNÇÕES DE HIGIENIZAÇÃO E UTILITÁRIOS
# =========================
async def sanitize_text(text):
    if not text:
        return text

    text = str(text)

    if not FORBIDDEN_PATTERN.search(text):
        return text

    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": "auto",
            "tl": "en",
            "dt": "t",
            "q": text
        }
        response = await asyncio.to_thread(session.get, url, params=params, timeout=3)
        if response.status_code == 200:
            data = response.json()
            translated_text = "".join([sentence[0] for sentence in data[0]])

            if not FORBIDDEN_PATTERN.search(translated_text):
                return translated_text
    except Exception as e:
        logger.warning(f"Falha na tradução automática: {e}")

    sanitized = FORBIDDEN_PATTERN.sub("", text)
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()

    return sanitized if sanitized else "Unknown"


def escape_markdown(text):
    return re.sub(r"([_*`\[])", r"\\\1", str(text))


def evict_cache():
    with cache_lock:
        if len(cache) >= CACHE_MAX_SIZE:
            oldest_keys = list(cache.keys())[:100]
            for k in oldest_keys:
                del cache[k]


def is_admin(user_id: int | None) -> bool:
    return ADMIN_ID is not None
