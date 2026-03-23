diff --git a/pidrofmbot.py b/pidrofmbot.py
index c40b3d7e6d02e692ef5bea9d463451f827970700..14e6e7dc772ebf460f5334997d70e8074e49271b 100644
--- a/pidrofmbot.py
+++ b/pidrofmbot.py
@@ -1,370 +1,507 @@
+import asyncio
+import logging
 import os
 import re
 import time
-import asyncio
-import logging
-import requests
-import telegram.error
-
 from concurrent.futures import ThreadPoolExecutor
+from typing import Any, Dict, List, Optional, Tuple
+
+import requests
 from telegram import (
-    Update,
-    InlineQueryResultPhoto,
+    InlineKeyboardButton,
     InlineKeyboardMarkup,
-    InlineKeyboardButton
+    InlineQueryResultPhoto,
+    Update,
 )
-
+from telegram.error import TelegramError
 from telegram.ext import (
     Application,
-    InlineQueryHandler,
-    MessageHandler,
     CallbackQueryHandler,
     ContextTypes,
-    filters
+    InlineQueryHandler,
+    MessageHandler,
+    filters,
 )
 
 logging.basicConfig(
     format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
-    level=logging.INFO
+    level=logging.INFO,
 )
 logger = logging.getLogger(__name__)
 
+# =========================
+# CONFIG
+# =========================
+
 TOKEN = os.getenv("TELEGRAM_TOKEN")
 WEBHOOK_URL = os.getenv("WEBHOOK_URL")
-WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", TOKEN.replace(":", "")[:20] if TOKEN else None)
 
 try:
-    PORT = int(os.getenv("PORT", 8443))
+    PORT = int(os.getenv("PORT", "8443"))
 except ValueError:
     logger.warning("Invalid PORT value, defaulting to 8443")
     PORT = 8443
 
+WEBHOOK_SECRET = os.getenv(
+    "WEBHOOK_SECRET",
+    TOKEN.replace(":", "")[:20] if TOKEN else None,
+)
+
 if not TOKEN:
     raise ValueError("Configure TELEGRAM_TOKEN nas variáveis do Render")
 
+# =========================
+# HTTP / CACHE
+# =========================
+
 session = requests.Session()
-cache = {}
-CACHE_MAX_SIZE = 500
+session.headers.update(
+    {
+        "User-Agent": "PidroFmBot/1.0",
+        "Accept": "application/json",
+    }
+)
+
 _executor = ThreadPoolExecutor(max_workers=4)
 
+CACHE_MAX_SIZE = 500
+CACHE_TTL_SECONDS = 600  # 10 minutos
+
+# cache[cache_key] = (timestamp, value)
+cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
 
-def escape_markdown(text):
+
+# =========================
+# HELPERS
+# =========================
+
+def escape_markdown(text: Any) -> str:
     return re.sub(r"([_*`\[])", r"\\\1", str(text))
 
 
-def evict_cache():
-    if len(cache) >= CACHE_MAX_SIZE:
-        oldest_keys = list(cache.keys())[:100]
-        for k in oldest_keys:
-            del cache[k]
+def normalize_query(query: str) -> str:
+    query = re.sub(r"[-_]+", " ", query)
+    query = re.sub(r"\s+", " ", query).strip()
+    return query
+
 
+def get_cache(cache_key: str) -> Optional[List[Dict[str, Any]]]:
+    item = cache.get(cache_key)
+    if not item:
+        return None
 
-# =========================
-# RANKING INTELIGENTE
-# =========================
+    created_at, value = item
+    if time.time() - created_at > CACHE_TTL_SECONDS:
+        cache.pop(cache_key, None)
+        logger.debug("Cache expired for key=%s", cache_key)
+        return None
+
+    return value
 
-def score_track(track, query):
+
+def set_cache(cache_key: str, value: List[Dict[str, Any]]) -> None:
+    evict_cache_if_needed()
+    cache[cache_key] = (time.time(), value)
+
+
+def evict_cache_if_needed() -> None:
+    if len(cache) < CACHE_MAX_SIZE:
+        return
+
+    now = time.time()
+    expired_keys = [
+        key for key, (created_at, _) in cache.items()
+        if now - created_at > CACHE_TTL_SECONDS
+    ]
+    for key in expired_keys:
+        cache.pop(key, None)
+
+    if len(cache) < CACHE_MAX_SIZE:
+        return
+
+    oldest_keys = sorted(cache.items(), key=lambda item: item[1][0])[:100]
+    for key, _ in oldest_keys:
+        cache.pop(key, None)
+
+    logger.info("Cache eviction executed; current_size=%s", len(cache))
+
+
+def score_track(track: Dict[str, Any], query: str) -> int:
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
-    except (KeyError, AttributeError):
+    except (KeyError, AttributeError, TypeError):
         return 0
 
 
+def build_track_keyboard(tracks: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
+    keyboard: List[List[InlineKeyboardButton]] = []
+
+    for i, track in enumerate(tracks[:10]):
+        title = track.get("title", "Unknown title")
+        artist = track.get("artist", {}).get("name", "Unknown artist")
+
+        keyboard.append([
+            InlineKeyboardButton(
+                f"{title} — {artist}",
+                callback_data=f"track_{i}",
+            )
+        ])
+
+    keyboard.append([
+        InlineKeyboardButton(
+            "Load more",
+            callback_data="more",
+        )
+    ])
+
+    return InlineKeyboardMarkup(keyboard)
+
+
+def build_caption(user_name: str, title: str, album: str, artist: str) -> str:
+    return (
+        f"♫ {user_name} is listening to...\n\n"
+        f"♬ *{title}* - _{album}_ — _{artist}_"
+    )
+
+
+def build_photo_caption(user_name: str, title: str, album: str, artist: str) -> str:
+    return (
+        f"♫ {user_name} is listening to...\n\n"
+        f"♬ *{title}* - _{album} — {artist}_"
+    )
+
+
 # =========================
-# BUSCA NA API
+# DEEZER SEARCH
 # =========================
 
-def _search_deezer_sync(query, index=0):
-
-    query = re.sub(r"[-_]+", " ", query)
-    query = re.sub(r"\s+", " ", query).strip()
+def _search_deezer_sync(query: str, index: int = 0) -> List[Dict[str, Any]]:
+    normalized_query = normalize_query(query)
+    cache_key = f"{normalized_query}_{index}"
 
-    cache_key = f"{query}_{index}"
+    cached = get_cache(cache_key)
+    if cached is not None:
+        logger.debug("Cache hit for query=%s index=%s", normalized_query, index)
+        return cached
 
-    if cache_key in cache:
-        return cache[cache_key]
+    logger.info("Searching Deezer query=%s index=%s", normalized_query, index)
 
-    for attempt in range(3):
+    for attempt in range(1, 4):
         try:
-            r = session.get(
+            response = session.get(
                 "https://api.deezer.com/search",
-                params={"q": query, "index": index},
-                timeout=5
+                params={"q": normalized_query, "index": index},
+                timeout=5,
             )
 
-            if r.status_code != 200:
+            if response.status_code != 200:
+                logger.warning(
+                    "Deezer returned non-200 status=%s query=%s index=%s attempt=%s",
+                    response.status_code,
+                    normalized_query,
+                    index,
+                    attempt,
+                )
                 return []
 
-            tracks = r.json().get("data", [])
+            payload = response.json()
+            tracks = payload.get("data", [])
 
             tracks = sorted(
                 tracks,
-                key=lambda t: score_track(t, query),
-                reverse=True
+                key=lambda track: score_track(track, normalized_query),
+                reverse=True,
             )
 
-            evict_cache()
-            cache[cache_key] = tracks
+            set_cache(cache_key, tracks)
 
+            logger.info(
+                "Deezer search success query=%s index=%s results=%s",
+                normalized_query,
+                index,
+                len(tracks),
+            )
             return tracks
 
-        except Exception:
-            time.sleep(1)
+        except requests.exceptions.Timeout:
+            logger.warning(
+                "Timeout on Deezer query=%s index=%s attempt=%s",
+                normalized_query,
+                index,
+                attempt,
+            )
+        except requests.exceptions.RequestException as exc:
+            logger.exception(
+                "Request error on Deezer query=%s index=%s attempt=%s error=%s",
+                normalized_query,
+                index,
+                attempt,
+                exc,
+            )
+        except ValueError as exc:
+            logger.exception(
+                "Invalid JSON from Deezer query=%s index=%s attempt=%s error=%s",
+                normalized_query,
+                index,
+                attempt,
+                exc,
+            )
+            return []
+
+        time.sleep(0.5 * attempt)
 
+    logger.error(
+        "Deezer search failed after retries query=%s index=%s",
+        normalized_query,
+        index,
+    )
     return []
 
 
-async def search_deezer(query, index=0):
-    loop = asyncio.get_event_loop()
+async def search_deezer(query: str, index: int = 0) -> List[Dict[str, Any]]:
+    loop = asyncio.get_running_loop()
     return await loop.run_in_executor(_executor, _search_deezer_sync, query, index)
 
 
 # =========================
 # INLINE MODE
 # =========================
 
-async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
+async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
+    if not update.inline_query:
+        return
 
     query = update.inline_query.query
-
     if not query:
         return
 
     tracks = await search_deezer(query)
 
     user = update.inline_query.from_user
     user_name = escape_markdown(user.first_name if user else "Someone")
 
-    results = []
+    results: List[InlineQueryResultPhoto] = []
 
     for i, track in enumerate(tracks[:10]):
-
         try:
-
             title = escape_markdown(track["title"])
             artist = escape_markdown(track["artist"]["name"])
             album = escape_markdown(track["album"]["title"])
             cover = track["album"]["cover_big"]
 
             results.append(
-
                 InlineQueryResultPhoto(
                     id=str(i),
                     photo_url=cover,
                     thumbnail_url=cover,
-
                     title=f"{track['title']} — {track['artist']['name']}",
                     description="♪ Share this song",
-
-                    caption=(
-                        f"♫ {user_name} is listening to...\n\n"
-                        f"♬ *{title}* - _{album}_ — _{artist}_"
-                    ),
-                    parse_mode="Markdown"
+                    caption=build_caption(user_name, title, album, artist),
+                    parse_mode="Markdown",
                 )
             )
-
-        except Exception:
+        except (KeyError, TypeError):
+            logger.warning("Skipping malformed inline track at index=%s", i)
             continue
 
-    await update.inline_query.answer(results, cache_time=5)
+    try:
+        await update.inline_query.answer(results, cache_time=5)
+    except TelegramError:
+        logger.exception("Failed to answer inline query")
 
 
 # =========================
-# BUSCA NO CHAT
+# CHAT SEARCH
 # =========================
 
-async def search_music(update: Update, context: ContextTypes.DEFAULT_TYPE):
+async def search_music(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
+    if not update.message or not update.message.text:
+        return
 
-    query = update.message.text
+    query = update.message.text.strip()
+    if not query:
+        return
 
     context.user_data["query"] = query
     context.user_data["offset"] = 0
 
     await send_results(update, context)
 
 
-# =========================
-# ENVIAR RESULTADOS
-# =========================
-
-async def send_results(update, context):
+async def send_results(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
+    if not update.message:
+        return
 
     query = context.user_data.get("query")
     offset = context.user_data.get("offset", 0)
 
     if not query:
+        logger.warning("send_results called without query in user_data")
         return
 
     tracks = await search_deezer(query, offset)
 
     if not tracks:
         await update.message.reply_text("No results found.")
         return
 
     context.user_data["tracks"] = tracks
 
-    keyboard = []
-
-    for i, track in enumerate(tracks[:10]):
-
-        title = track["title"]
-        artist = track["artist"]["name"]
-
-        keyboard.append([
-            InlineKeyboardButton(
-                f"{title} — {artist}",
-                callback_data=f"track_{i}"
-            )
-        ])
-
-    keyboard.append([
-        InlineKeyboardButton(
-            "Load more",
-            callback_data="more"
+    try:
+        await update.message.reply_text(
+            "♪ Search song...",
+            reply_markup=build_track_keyboard(tracks),
         )
-    ])
-
-    await update.message.reply_text(
-        "♪ Search song...",
-        reply_markup=InlineKeyboardMarkup(keyboard)
-    )
+    except TelegramError:
+        logger.exception("Failed to send results message")
 
 
 # =========================
-# MAIS RESULTADOS
+# MORE RESULTS
 # =========================
 
-async def more_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
+async def more_results(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
+    if not update.callback_query:
+        return
 
     cb_query = update.callback_query
     await cb_query.answer()
 
     search_query = context.user_data.get("query")
+    if not search_query:
+        await cb_query.message.reply_text("No results found.")
+        logger.warning("more_results called without query in user_data")
+        return
 
-    context.user_data["offset"] = context.user_data.get("offset", 0) + 10
-
-    tracks = await search_deezer(
-        search_query,
-        context.user_data["offset"]
-    )
+    current_offset = context.user_data.get("offset", 0)
+    new_offset = current_offset + 10
+    context.user_data["offset"] = new_offset
 
+    tracks = await search_deezer(search_query, new_offset)
     context.user_data["tracks"] = tracks
 
-    keyboard = []
-
-    for i, track in enumerate(tracks[:10]):
-
-        title = track["title"]
-        artist = track["artist"]["name"]
-
-        keyboard.append([
-            InlineKeyboardButton(
-                f"{title} — {artist}",
-                callback_data=f"track_{i}"
-            )
-        ])
+    if not tracks:
+        await cb_query.message.reply_text("No results found.")
+        return
 
-    keyboard.append([
-        InlineKeyboardButton(
-            "Load more",
-            callback_data="more"
+    try:
+        await cb_query.message.reply_text(
+            "♪ Search song...",
+            reply_markup=build_track_keyboard(tracks),
         )
-    ])
-
-    await cb_query.message.reply_text(
-        "♪ Search song...",
-        reply_markup=InlineKeyboardMarkup(keyboard)
-    )
+    except TelegramError:
+        logger.exception("Failed to send more results")
 
 
 # =========================
-# ESCOLHER MÚSICA
+# SELECT TRACK
 # =========================
 
-async def select_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
+async def select_track(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
+    if not update.callback_query:
+        return
 
     cb_query = update.callback_query
     await cb_query.answer()
 
-    index = int(cb_query.data.split("_")[1])
+    try:
+        index = int(cb_query.data.split("_")[1])
+    except (IndexError, ValueError, AttributeError):
+        logger.warning("Invalid callback data received: %s", cb_query.data)
+        await cb_query.message.reply_text("No results found.")
+        return
+
     tracks = context.user_data.get("tracks")
+    if not tracks or not isinstance(tracks, list):
+        logger.warning("Track selection without valid tracks in user_data")
+        await cb_query.message.reply_text("No results found.")
+        return
+
+    if index < 0 or index >= len(tracks):
+        logger.warning("Track index out of range: %s", index)
+        await cb_query.message.reply_text("No results found.")
+        return
 
     track = tracks[index]
 
-    title = escape_markdown(track["title"])
-    artist = escape_markdown(track["artist"]["name"])
-    album = escape_markdown(track["album"]["title"])
-    cover = track["album"]["cover_big"]
+    try:
+        title = escape_markdown(track["title"])
+        artist = escape_markdown(track["artist"]["name"])
+        album = escape_markdown(track["album"]["title"])
+        cover = track["album"]["cover_big"]
+    except (KeyError, TypeError):
+        logger.warning("Malformed selected track at index=%s", index)
+        await cb_query.message.reply_text("No results found.")
+        return
 
-    user_name = escape_markdown(cb_query.from_user.first_name)
+    user_name = escape_markdown(cb_query.from_user.first_name or "Someone")
 
-    await cb_query.message.reply_photo(
-        photo=cover,
-        caption=(
-            f"♫ {user_name} is listening to...\n\n"
-            f"♬ *{title}* - _{album} — {artist}_"
-        ),
-        parse_mode="Markdown"
-    )
+    try:
+        await cb_query.message.reply_photo(
+            photo=cover,
+            caption=build_photo_caption(user_name, title, album, artist),
+            parse_mode="Markdown",
+        )
+    except TelegramError:
+        logger.exception("Failed to send selected track photo")
 
 
 # =========================
-# MAIN
+# APP SETUP
 # =========================
 
-def main():
-
-    app = (
-        Application.builder()
-        .token(TOKEN)
-        .build()
-    )
+def build_application() -> Application:
+    app = Application.builder().token(TOKEN).build()
 
     app.add_handler(InlineQueryHandler(inline_query))
-
     app.add_handler(
         MessageHandler(filters.TEXT & ~filters.COMMAND, search_music)
     )
-
     app.add_handler(
         CallbackQueryHandler(more_results, pattern="^more$")
     )
-
     app.add_handler(
         CallbackQueryHandler(select_track, pattern=r"^track_\d+$")
     )
 
+    return app
+
+
+def main() -> None:
+    app = build_application()
+
     if WEBHOOK_URL:
+        logger.info("Starting bot in webhook mode on port=%s", PORT)
         app.run_webhook(
             listen="0.0.0.0",
             port=PORT,
             url_path=TOKEN,
             webhook_url=f"{WEBHOOK_URL}/{TOKEN}",
             secret_token=WEBHOOK_SECRET,
             drop_pending_updates=True,
         )
     else:
+        logger.info("Starting bot in polling mode")
         app.run_polling(drop_pending_updates=True)
 
 
 if __name__ == "__main__":
     main()
