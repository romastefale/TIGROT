import os
import re
import time
import asyncio
import logging
import requests
import telegram.error

from collections import OrderedDict
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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", TOKEN.replace(":", "")[:20] if TOKEN else None)

ADMIN_ID_RAW = os.getenv("ADMIN_ID")
try:
    ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW else None
except ValueError:
    logger.warning("Invalid ADMIN_ID value")
    ADMIN_ID = None

PORT = int(os.getenv("PORT", 8443))

if not TOKEN:
    raise ValueError("Configure TELEGRAM_TOKEN")

session = requests.Session()
cache = OrderedDict()
CACHE_MAX_SIZE = 500
_executor = ThreadPoolExecutor(max_workers=4)

# =========================
# UTIL
# =========================

def sanitize_text(text):
    if not text:
        return text

    text = str(text)

    forbidden_pattern = re.compile(
        r'[\u0600-\u06FF\u0400-\u04FF\u4E00-\u9FFF]'
    )

    if not forbidden_pattern.search(text):
        return text

    sanitized = forbidden_pattern.sub("", text)
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()

    return sanitized if sanitized else "Unknown"


def escape_markdown(text):
    return re.sub(r"([_*`\[])", r"\\\1", str(text))


def evict_cache():
    while len(cache) > CACHE_MAX_SIZE:
        cache.popitem(last=False)


def is_admin(user_id):
    return ADMIN_ID is not None and user_id == ADMIN_ID


# =========================
# LOG
# =========================

async def send_log_prompt(update, context):
    await update.effective_message.reply_text(
        "📝Qual texto de <i>Update</i> você deseja enviar?",
        parse_mode=ParseMode.HTML
    )


async def start_log(update, context):
    user_id = update.effective_user.id if update.effective_user else None

    if not is_admin(user_id):
        await update.effective_message.reply_text("Sem permissão.")
        return

    context.user_data["awaiting_log"] = time.time()
    await send_log_prompt(update, context)


async def handle_log_input(update, context):
    ts = context.user_data.get("awaiting_log")

    if not ts or time.time() - ts > 60:
        context.user_data.pop("awaiting_log", None)
        return

    user_id = update.effective_user.id if update.effective_user else None
    if not is_admin(user_id):
        return

    msg = update.effective_message
    if not msg:
        return

    try:
        await context.bot.copy_message(
            chat_id=msg.chat_id,
            from_chat_id=msg.chat_id,
            message_id=msg.message_id
        )
    except Exception:
        logger.exception("Erro no /log")
        await msg.reply_text("Falha ao reproduzir a mensagem.")
        context.user_data.pop("awaiting_log", None)
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🆗Correto?", callback_data="log_ok"),
            InlineKeyboardButton("✏️Editar...", callback_data="log_edit")
        ]
    ])

    await msg.reply_text("🆗Correto?", reply_markup=keyboard)
    context.user_data.pop("awaiting_log", None)


async def handle_log_callback(update, context):
    cb_query = update.callback_query
    await cb_query.answer()

    user_id = cb_query.from_user.id if cb_query.from_user else None
    if not is_admin(user_id):
        return

    if cb_query.data == "log_edit":
        context.user_data["awaiting_log"] = time.time()
        await cb_query.message.reply_text(
            "📝Qual texto de <i>Update</i> você deseja enviar?",
            parse_mode=ParseMode.HTML
        )


# =========================
# SEARCH
# =========================

def score_track(track, query):
    try:
        title = track["title"].lower()
        artist = track["artist"]["name"].lower()
        q = query.lower()

        score = 0
        if q in f"{title} {artist}": score += 100
        if q in title: score += 60
        if q in artist: score += 40
        if title.startswith(q): score += 30

        return score
    except:
        return 0


def _search_deezer_sync(query, index=0):
    query = re.sub(r"[-_]+", " ", query)
    query = re.sub(r"\s+", " ", query).strip()

    cache_key = f"{query}_{index}"

    if cache_key in cache:
        cache.move_to_end(cache_key)
        return cache[cache_key]

    for _ in range(3):
        try:
            r = session.get(
                "https://api.deezer.com/search",
                params={"q": query, "index": index},
                timeout=5
            )

            if r.status_code != 200:
                return []

            tracks = r.json().get("data", [])
            tracks = sorted(tracks, key=lambda t: score_track(t, query), reverse=True)

            cache[cache_key] = tracks
            evict_cache()

            return tracks
        except Exception:
            time.sleep(1)

    return []


async def search_deezer(query, index=0):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _search_deezer_sync, query, index)


# =========================
# INLINE
# =========================

async def inline_query(update, context):
    query = update.inline_query.query
    if not query:
        return

    tracks = await search_deezer(query)
    user = update.inline_query.from_user
    user_name = escape_markdown(sanitize_text(user.first_name if user else "Someone"))

    results = []

    for i, track in enumerate(tracks[:10]):
        try:
            title = escape_markdown(sanitize_text(track["title"]))
            artist = escape_markdown(sanitize_text(track["artist"]["name"]))
            album = escape_markdown(sanitize_text(track["album"]["title"]))
            cover = track["album"]["cover_big"]

            results.append(
                InlineQueryResultPhoto(
                    id=str(i),
                    photo_url=cover,
                    thumbnail_url=cover,
                    title=f"{track['title']} — {track['artist']['name']}",
                    description="♪ Share this song",
                    caption=(
                        f"♫ {user_name} is listening to...\n\n"
                        f"♬ *{title}* - _{album}_ — _{artist}_"
                    ),
                    parse_mode="Markdown"
                )
            )
        except:
            continue

    try:
        await update.inline_query.answer(results, cache_time=5)
    except Exception:
        logger.exception("Erro inline")


# =========================
# CHAT SEARCH
# =========================

async def search_music(update, context):
    if context.user_data.get("awaiting_log"):
        return

    context.user_data["query"] = update.message.text
    context.user_data["offset"] = 0

    await send_results(update, context)


async def send_results(update, context):
    query = context.user_data.get("query")
    offset = context.user_data.get("offset", 0)

    tracks = await search_deezer(query, offset)

    if not tracks:
        await update.message.reply_text("No results found.")
        return

    context.user_data["tracks"] = tracks

    keyboard = []

    for i, track in enumerate(tracks[:10]):
        title = sanitize_text(track["title"])
        artist = sanitize_text(track["artist"]["name"])

        keyboard.append([
            InlineKeyboardButton(f"{title} — {artist}", callback_data=f"track_{i}")
        ])

    keyboard.append([
        InlineKeyboardButton("Load more", callback_data="more")
    ])

    await update.message.reply_text(
        "♪ Search song...",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def more_results(update, context):
    cb_query = update.callback_query
    await cb_query.answer()

    context.user_data["offset"] += 10
    await send_results(cb_query.message, context)


async def select_track(update, context):
    cb_query = update.callback_query
    await cb_query.answer()

    index = int(cb_query.data.split("_")[1])
    tracks = context.user_data.get("tracks")

    if not tracks or index >= len(tracks):
        await cb_query.answer("Resultado expirado.", show_alert=True)
        return

    track = tracks[index]

    title = escape_markdown(sanitize_text(track["title"]))
    artist = escape_markdown(sanitize_text(track["artist"]["name"]))
    album = escape_markdown(sanitize_text(track["album"]["title"]))
    cover = track["album"]["cover_big"]

    user_name = escape_markdown(sanitize_text(cb_query.from_user.first_name))

    await cb_query.message.reply_photo(
        photo=cover,
        caption=(
            f"♫ {user_name} is listening to...\n\n"
            f"♬ *{title}* - _{album} — {artist}_"
        ),
        parse_mode="Markdown"
    )


# =========================
# MAIN
# =========================

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("log", start_log))
    app.add_handler(InlineQueryHandler(inline_query))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_music))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_log_input), group=1)

    app.add_handler(CallbackQueryHandler(handle_log_callback, pattern=r"^log_(ok|edit)$"))
    app.add_handler(CallbackQueryHandler(more_results, pattern="^more$"))
    app.add_handler(CallbackQueryHandler(select_track, pattern=r"^track_\d+$"))

    if WEBHOOK_URL:
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TOKEN}",
            secret_token=WEBHOOK_SECRET,
            drop_pending_updates=True,
        )
    else:
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
