import asyncio
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

import requests
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultPhoto,
    Update,
)
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# =========================
# CONFIG
# =========================

TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

try:
    PORT = int(os.getenv("PORT", "8443"))
except ValueError:
    logger.warning("Invalid PORT value, defaulting to 8443")
    PORT = 8443

WEBHOOK_SECRET = os.getenv(
    "WEBHOOK_SECRET",
    TOKEN.replace(":", "")[:20] if TOKEN else None,
)

if not TOKEN:
    raise ValueError("Configure TELEGRAM_TOKEN")

# =========================
# HTTP / CACHE
# =========================

session = requests.Session()
session.headers.update(
    {
        "User-Agent": "PidroFmBot/1.0",
        "Accept": "application/json",
    }
)

_executor = ThreadPoolExecutor(max_workers=4)

CACHE_MAX_SIZE = 500
CACHE_TTL_SECONDS = 600

cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}

# =========================
# HELPERS
# =========================

def escape_markdown(text: Any) -> str:
    return re.sub(r"([_*`\[])", r"\\\1", str(text))


def normalize_query(query: str) -> str:
    query = re.sub(r"[-_]+", " ", query)
    query = re.sub(r"\s+", " ", query).strip()
    return query


def get_cache(cache_key: str) -> Optional[List[Dict[str, Any]]]:
    item = cache.get(cache_key)
    if not item:
        return None

    created_at, value = item
    if time.time() - created_at > CACHE_TTL_SECONDS:
        cache.pop(cache_key, None)
        return None

    return value


def set_cache(cache_key: str, value: List[Dict[str, Any]]) -> None:
    if len(cache) >= CACHE_MAX_SIZE:
        cache.pop(next(iter(cache)))
    cache[cache_key] = (time.time(), value)


def score_track(track: Dict[str, Any], query: str) -> int:
    try:
        title = track["title"].lower()
        artist = track["artist"]["name"].lower()
        q = query.lower()

        score = 0
        if q in f"{title} {artist}":
            score += 100
        if q in title:
            score += 60
        if q in artist:
            score += 40
        if title.startswith(q):
            score += 30

        return score
    except Exception:
        return 0


def build_track_keyboard(tracks: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    keyboard = []

    for i, track in enumerate(tracks[:10]):
        keyboard.append([
            InlineKeyboardButton(
                f"{track.get('title')} — {track.get('artist', {}).get('name')}",
                callback_data=f"track_{i}",
            )
        ])

    keyboard.append([InlineKeyboardButton("Load more", callback_data="more")])
    return InlineKeyboardMarkup(keyboard)


def build_caption(user_name: str, title: str, album: str, artist: str) -> str:
    return f"♫ {user_name} is listening to...\n\n♬ *{title}* - _{album}_ — _{artist}_"


def build_photo_caption(user_name: str, title: str, album: str, artist: str) -> str:
    return f"♫ {user_name} is listening to...\n\n♬ *{title}* - _{album} — {artist}_"


# =========================
# DEEZER SEARCH
# =========================

def _search_deezer_sync(query: str, index: int = 0) -> List[Dict[str, Any]]:
    query = normalize_query(query)
    cache_key = f"{query}_{index}"

    cached = get_cache(cache_key)
    if cached:
        return cached

    try:
        r = session.get(
            "https://api.deezer.com/search",
            params={"q": query, "index": index},
            timeout=5,
        )

        if r.status_code != 200:
            return []

        tracks = r.json().get("data", [])
        tracks = sorted(tracks, key=lambda t: score_track(t, query), reverse=True)

        set_cache(cache_key, tracks)
        return tracks

    except requests.RequestException:
        return []


async def search_deezer(query: str, index: int = 0) -> List[Dict[str, Any]]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _search_deezer_sync, query, index)


# =========================
# INLINE
# =========================

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.inline_query:
        return

    query = update.inline_query.query
    if not query:
        return

    tracks = await search_deezer(query)
    user_name = escape_markdown(update.inline_query.from_user.first_name)

    results = []
    for i, track in enumerate(tracks[:10]):
        try:
            results.append(
                InlineQueryResultPhoto(
                    id=str(i),
                    photo_url=track["album"]["cover_big"],
                    thumbnail_url=track["album"]["cover_big"],
                    title=f"{track['title']} — {track['artist']['name']}",
                    description="♪ Share this song",
                    caption=build_caption(
                        user_name,
                        escape_markdown(track["title"]),
                        escape_markdown(track["album"]["title"]),
                        escape_markdown(track["artist"]["name"]),
                    ),
                    parse_mode="Markdown",
                )
            )
        except Exception:
            continue

    await update.inline_query.answer(results, cache_time=5)


# =========================
# CHAT
# =========================

async def search_music(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    query = update.message.text.strip()
    context.user_data["query"] = query
    context.user_data["offset"] = 0

    await send_results(update, context)


async def send_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = context.user_data.get("query")
    offset = context.user_data.get("offset", 0)

    tracks = await search_deezer(query, offset)

    if not tracks:
        await update.message.reply_text("No results found.")
        return

    context.user_data["tracks"] = tracks

    await update.message.reply_text(
        "♪ Search song...",
        reply_markup=build_track_keyboard(tracks),
    )


async def more_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["offset"] += 10
    tracks = await search_deezer(
        context.user_data["query"],
        context.user_data["offset"],
    )

    context.user_data["tracks"] = tracks

    await query.message.reply_text(
        "♪ Search song...",
        reply_markup=build_track_keyboard(tracks),
    )


async def select_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    index = int(query.data.split("_")[1])
    track = context.user_data["tracks"][index]

    await query.message.reply_photo(
        photo=track["album"]["cover_big"],
        caption=build_photo_caption(
            escape_markdown(query.from_user.first_name),
            escape_markdown(track["title"]),
            escape_markdown(track["album"]["title"]),
            escape_markdown(track["artist"]["name"]),
        ),
        parse_mode="Markdown",
    )


# =========================
# MAIN
# =========================

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_music))
    app.add_handler(CallbackQueryHandler(more_results, pattern="^more$"))
    app.add_handler(CallbackQueryHandler(select_track, pattern=r"^track_\d+$"))

    if WEBHOOK_URL:
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TOKEN}",
            secret_token=WEBHOOK_SECRET,
        )
    else:
        app.run_polling()


if __name__ == "__main__":
    main()
