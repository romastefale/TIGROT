import asyncio
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultPhoto,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
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
GENIUS_ACCESS_TOKEN = os.getenv("GENIUS_ACCESS_TOKEN")

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
LYRICS_SNIPPET_MAX_LENGTH = 280

cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
lyrics_cache: Dict[str, Tuple[float, Optional[str]]] = {}
LYRICS_CACHE_MISS = object()

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


def get_lyrics_cache(cache_key: str) -> object:
    item = lyrics_cache.get(cache_key)
    if not item:
        return LYRICS_CACHE_MISS

    created_at, value = item
    if time.time() - created_at > CACHE_TTL_SECONDS:
        lyrics_cache.pop(cache_key, None)
        return LYRICS_CACHE_MISS

    return value


def set_lyrics_cache(cache_key: str, value: Optional[str]) -> None:
    if len(lyrics_cache) >= CACHE_MAX_SIZE:
        lyrics_cache.pop(next(iter(lyrics_cache)))
    lyrics_cache[cache_key] = (time.time(), value)


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


def build_lyrics_prompt_keyboard(index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Yes", callback_data=f"lyrics_yes_{index}"),
                InlineKeyboardButton("No", callback_data=f"lyrics_no_{index}"),
            ]
        ]
    )


def build_caption(
    user_name: str,
    title: str,
    album: str,
    artist: str,
    lyrics_snippet: Optional[str] = None,
) -> str:
    caption = f"♫ {user_name} is listening to...\n\n♬ *{title}* - _{album}_ — _{artist}_"
    if lyrics_snippet:
        caption += f"\n\n🎤 {lyrics_snippet}"
    return caption


def build_photo_caption(
    user_name: str,
    title: str,
    album: str,
    artist: str,
    lyrics_snippet: Optional[str] = None,
) -> str:
    caption = f"♫ {user_name} is listening to...\n\n♬ *{title}* - _{album} — {artist}_"
    if lyrics_snippet:
        caption += f"\n\n_♪ Lyrics\n\n{lyrics_snippet}_"
    return caption


def clean_lyrics_text(text: str) -> str:
    text = re.sub(r"\b\d+ Contributors?.*?Lyrics", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"You might also like", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bEmbed\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[[^\]]+\]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def split_lyrics_lines(text: str) -> List[str]:
    raw_lines = re.split(r"(?<=[.!?])\s+|\s*\\n\s*", text)
    return [line.strip(" -•\t") for line in raw_lines if line.strip()]


def build_relevance_snippet(lyrics: str, query: str, title: str, artist: str) -> Optional[str]:
    lines = split_lyrics_lines(lyrics)
    if not lines:
        return None

    terms = {
        term.lower()
        for term in re.findall(r"\w+", f"{query} {title} {artist}")
        if len(term) > 2
    }

    def score_line(line: str) -> int:
        lowered = line.lower()
        return sum(1 for term in terms if term in lowered)

    ranked = sorted(lines, key=score_line, reverse=True)
    best_line = ranked[0] if ranked else lines[0]
    if score_line(best_line) == 0:
        best_line = lines[0]

    snippet = best_line.strip()
    if len(snippet) > LYRICS_SNIPPET_MAX_LENGTH:
        snippet = snippet[: LYRICS_SNIPPET_MAX_LENGTH - 1].rstrip() + "…"
    return snippet


def extract_lyrics_from_html(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "lxml")

    lyrics_blocks = soup.select('[data-lyrics-container="true"]')
    if lyrics_blocks:
        text = "\n".join(block.get_text("\n", strip=True) for block in lyrics_blocks)
        return clean_lyrics_text(text)

    for script in soup.find_all("script"):
        content = script.string or script.get_text() or ""
        if "window.__PRELOADED_STATE__" not in content:
            continue

        match = re.search(r"window\.__PRELOADED_STATE__\s*=\s*JSON\.parse\('(.*)'\);", content, re.DOTALL)
        if not match:
            continue

        try:
            payload = match.group(1).encode("utf-8").decode("unicode_escape")
            data = json.loads(payload)
        except Exception:
            continue

        lyrics_data = data.get("songPage", {}).get("lyricsData", {})
        body = lyrics_data.get("body", {}).get("html")
        if body:
            text = BeautifulSoup(body, "lxml").get_text("\n", strip=True)
            return clean_lyrics_text(text)

    return None


def fetch_genius_lyrics_snippet(title: str, artist: str, query: str) -> Optional[str]:
    if not GENIUS_ACCESS_TOKEN:
        return None

    cache_key = f"{title.lower()}::{artist.lower()}::{query.lower()}"
    cached = get_lyrics_cache(cache_key)
    if cached is not LYRICS_CACHE_MISS:
        return cached

    try:
        response = session.get(
            "https://api.genius.com/search",
            params={"q": f"{title} {artist}"},
            headers={"Authorization": f"Bearer {GENIUS_ACCESS_TOKEN}"},
            timeout=5,
        )
        response.raise_for_status()

        hits = response.json().get("response", {}).get("hits", [])
        if not hits:
            set_lyrics_cache(cache_key, None)
            return None

        best_hit = None
        best_score = -1
        expected_title = normalize_query(title).lower()
        expected_artist = normalize_query(artist).lower()

        for hit in hits:
            result = hit.get("result", {})
            candidate_title = normalize_query(result.get("title", "")).lower()
            candidate_artist = normalize_query(result.get("primary_artist", {}).get("name", "")).lower()
            score = 0
            if expected_title in candidate_title or candidate_title in expected_title:
                score += 4
            if expected_artist in candidate_artist or candidate_artist in expected_artist:
                score += 3
            if result.get("lyrics_state") == "complete":
                score += 1
            if score > best_score:
                best_score = score
                best_hit = result

        if not best_hit or not best_hit.get("url"):
            set_lyrics_cache(cache_key, None)
            return None

        lyrics_page = session.get(best_hit["url"], timeout=5)
        lyrics_page.raise_for_status()
        lyrics = extract_lyrics_from_html(lyrics_page.text)
        if not lyrics:
            set_lyrics_cache(cache_key, None)
            return None

        snippet = build_relevance_snippet(lyrics, query, title, artist)
        set_lyrics_cache(cache_key, snippet)
        return snippet
    except requests.RequestException as exc:
        logger.warning("Genius request failed for %s - %s: %s", title, artist, exc)
    except Exception as exc:
        logger.warning("Failed to extract Genius lyrics for %s - %s: %s", title, artist, exc)

    set_lyrics_cache(cache_key, None)
    return None


async def get_lyrics_snippet(title: str, artist: str, query: str) -> Optional[str]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, fetch_genius_lyrics_snippet, title, artist, query)


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

    lyrics_tasks = [
        get_lyrics_snippet(track["title"], track["artist"]["name"], query)
        for track in tracks[:10]
    ]
    lyrics_results = await asyncio.gather(*lyrics_tasks, return_exceptions=True)

    results = []
    for i, track in enumerate(tracks[:10]):
        try:
            lyrics_snippet = None
            if i < len(lyrics_results) and not isinstance(lyrics_results[i], Exception):
                lyrics_snippet = lyrics_results[i]

            description = "♪ Share this song"
            if lyrics_snippet:
                description = lyrics_snippet[:100] + ("…" if len(lyrics_snippet) > 100 else "")

            results.append(
                InlineQueryResultPhoto(
                    id=str(i),
                    photo_url=track["album"]["cover_big"],
                    thumbnail_url=track["album"]["cover_big"],
                    title=f"{track['title']} — {track['artist']['name']}",
                    description=description,
                    caption=build_caption(
                        user_name,
                        escape_markdown(track["title"]),
                        escape_markdown(track["album"]["title"]),
                        escape_markdown(track["artist"]["name"]),
                        escape_markdown(lyrics_snippet) if lyrics_snippet else None,
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

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    await update.message.reply_text(
        "Esse bot foi criado pelo @tigrao para você compartilhar suas músicas ouvidas onde quiser. Basta digitar aqui, buscar e compartilhar ou citar numa conversa e enviar!"
    )


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


async def send_selected_track(
    callback_query,
    context: ContextTypes.DEFAULT_TYPE,
    index: int,
    include_lyrics: bool,
):
    track = context.user_data["tracks"][index]
    lyrics_snippet = None

    if include_lyrics:
        search_query = context.user_data.get("query", track["title"])
        lyrics_snippet = await get_lyrics_snippet(
            track["title"],
            track["artist"]["name"],
            search_query,
        )

    await callback_query.message.reply_photo(
        photo=track["album"]["cover_big"],
        caption=build_photo_caption(
            escape_markdown(callback_query.from_user.first_name),
            escape_markdown(track["title"]),
            escape_markdown(track["album"]["title"]),
            escape_markdown(track["artist"]["name"]),
            escape_markdown(lyrics_snippet) if lyrics_snippet else None,
        ),
        parse_mode="Markdown",
    )


async def select_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    index = int(query.data.split("_")[1])
    await query.message.reply_text(
        "♪ Lyrics?",
        reply_markup=build_lyrics_prompt_keyboard(index),
    )


async def handle_lyrics_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    matched = re.match(r"^lyrics_(yes|no)_(\d+)$", query.data)
    if not matched:
        return

    include_lyrics = matched.group(1) == "yes"
    index = int(matched.group(2))

    await send_selected_track(query, context, index, include_lyrics)


# =========================
# MAIN
# =========================

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_music))
    app.add_handler(CallbackQueryHandler(more_results, pattern="^more$"))
    app.add_handler(CallbackQueryHandler(handle_lyrics_choice, pattern=r"^lyrics_(yes|no)_\d+$"))
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