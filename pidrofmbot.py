import os
import re
import time
import asyncio
import logging
import requests
import hashlib
import html
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from telegram import (
    Update,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LinkPreviewOptions
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    InlineQueryHandler,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# =========================
# CONFIGURAÇÃO E LOGGING
# =========================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", TOKEN.replace(":", "")[:20] if TOKEN else None)

try:
    PORT = int(os.getenv("PORT", 8443))
except (ValueError, TypeError):
    PORT = 8443

session = requests.Session()
music_cache = {}  
_executor = ThreadPoolExecutor(max_workers=4)

# =========================
# LÓGICA DE LETRAS
# =========================
def get_chorus_via_api(title, artist):
    try:
        clean_artist = re.sub(r'[\(\[].*[\)\]]', '', artist).strip()
        clean_title = re.sub(r'[\(\[].*[\)\]]', '', title).strip()
        url = f"https://api.lyrics.ovh/v1/{clean_artist}/{clean_title}"
        resp = session.get(url, timeout=10)
        if resp.status_code != 200: return None
        full_lyrics = resp.json().get("lyrics", "")
        if not full_lyrics: return None
        parts = re.split(r'(\[Refrão\]|\[Chorus\]|Refrão:|Chorus:)', full_lyrics, flags=re.IGNORECASE)
        if len(parts) > 1: return parts[2].strip().split('\n\n')[0]
        stanzas = [s.strip() for s in full_lyrics.split('\n\n') if len(s.strip()) > 20]
        if stanzas:
            counts = Counter(stanzas)
            most_common = counts.most_common(1)[0]
            if most_common[1] > 1: return most_common[0]
            return stanzas[0]
        return full_lyrics[:250] + "..."
    except Exception as e:
        logger.error(f"Erro na API de Letras: {e}")
        return None

# =========================
# LÓGICA DE BUSCA DEEZER
# =========================
def search_deezer_sync(query):
    query = re.sub(r"[-_]+", " ", query).strip()
    try:
        r = session.get("https://api.deezer.com/search", params={"q": query, "limit": 10}, timeout=5)
        return r.json().get("data", []) if r.status_code == 200 else []
    except Exception: return []

# =========================
# HANDLERS
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "🎹 Bot de música! Digite o nome da música ou use via inline."
    await update.message.reply_text(msg)

async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    author_id = update.message.from_user.id # ID de quem enviou a mensagem
    loop = asyncio.get_event_loop()
    tracks = await loop.run_in_executor(_executor, search_deezer_sync, query)
    
    if not tracks:
        await update.message.reply_text("❌ Nenhuma música encontrada.")
        return

    btns = []
    for t in tracks[:8]:
        t_key = hashlib.md5(t["link"].encode()).hexdigest()[:8]
        music_cache[t_key] = {
            "val": {
                "title": t["title"], "artist": t["artist"]["name"],
                "album": t["album"]["title"], "cover": t["album"]["cover_xl"] or t["album"]["cover_big"],
            },
            "author_id": author_id, # SALVANDO O AUTOR
            "states": {}, 
            "expires_at": time.time() + 1800
        }
        btns.append([InlineKeyboardButton(f"{t['title']} — {t['artist']['name']}", callback_data=f"s|{t_key}")])
    
    await update.message.reply_text("🎧 Escolha uma música...", reply_markup=InlineKeyboardMarkup(btns))

async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("|")
    action, key = parts[0], parts[1]
    
    m_data = music_cache.get(key)
    if not m_data:
        await query.answer("❌ Sessão expirada. Pesquise novamente.", show_alert=True)
        return

    # --- VALIDAÇÃO DE AUTOR ---
    if m_data.get("author_id") and query.from_user.id != m_data["author_id"]:
        await query.answer("⚠️ Só o autor da mensagem pode alterar o layout!", show_alert=True)
        return
    # --------------------------

    await query.answer()
    m = m_data["val"]
    msg_id = str(query.message.message_id) if query.message else query.inline_message_id

    if msg_id not in m_data["states"]:
        m_data["states"][msg_id] = {
            "show_cover": False, "show_lyrics": False,
            "user_name": html.escape(query.from_user.first_name)
        }
    
    state = m_data["states"][msg_id]
    if action == "c": state["show_cover"] = not state["show_cover"]
    elif action == "l": state["show_lyrics"] = not state["show_lyrics"]

    if state["show_lyrics"] and "chorus" not in m_data:
        loop = asyncio.get_event_loop()
        chorus = await loop.run_in_executor(_executor, get_chorus_via_api, m['title'], m['artist'])
        m_data["chorus"] = chorus or "⚠️ Letra não encontrada."

    layout = ""
    if state["show_cover"]: layout += f'<a href="{m["cover"]}">&#8203;</a>'
    layout += (
        f"🎹 {state['user_name']} está ouvindo...\n\n"
        f"🎧 <b>{html.escape(m['title'])}</b> - <i>{html.escape(m['album'])} — {html.escape(m['artist'])}</i>"
    )
    if state["show_lyrics"]:
        layout += f"\n\n<i>📜 Lyrics:</i> <blockquote>{html.escape(m_data.get('chorus', '...'))}</blockquote>"

    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ 🖼️ Cover" if state["show_cover"] else "🖼️ Cover", callback_data=f"c|{key}"),
        InlineKeyboardButton("✅ 📜 Lyrics" if state["show_lyrics"] else "📜 Lyrics", callback_data=f"l|{key}")
    ]])

    try:
        await query.edit_message_text(
            layout, parse_mode=ParseMode.HTML, reply_markup=markup,
            link_preview_options=LinkPreviewOptions(is_disabled=not state["show_cover"])
        )
    except BadRequest: pass

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query
    if not query: return
    author_id = update.inline_query.from_user.id # ID de quem está usando o inline
    loop = asyncio.get_event_loop()
    tracks = await loop.run_in_executor(_executor, search_deezer_sync, query)
    user_name = html.escape(update.inline_query.from_user.first_name)
    results = []

    for i, t in enumerate(tracks[:10]):
        t_key = hashlib.md5(t["link"].encode()).hexdigest()[:8]
        music_cache[t_key] = {
            "val": {
                "title": t["title"], "artist": t["artist"]["name"], 
                "album": t["album"]["title"], "cover": t["album"]["cover_xl"]
            },
            "author_id": author_id, # SALVANDO O AUTOR NO INLINE
            "states": {}, 
            "expires_at": time.time() + 1800
        }
        
        text_content = (
            f"🎹 {user_name} está ouvindo...\n\n"
            f"🎧 <b>{html.escape(t['title'])}</b> - <i>{html.escape(t['album']['title'])} — {html.escape(t['artist']['name'])}</i>"
        )

        results.append(InlineQueryResultArticle(
            id=f"{t_key}_{i}",
            title=f"{t['title']} — {t['artist']['name']}",
            description=f"Album: {t['album']['title']}",
            thumbnail_url=t["album"]["cover_small"],
            input_message_content=InputTextMessageContent(
                text_content, parse_mode=ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True)
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🖼️ Cover", callback_data=f"c|{t_key}"),
                InlineKeyboardButton("📜 Lyrics", callback_data=f"l|{t_key}")
            ]])
        ))
    await update.inline_query.answer(results, cache_time=5)

def main():
    if not TOKEN: return
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search))
    app.add_handler(CallbackQueryHandler(cb_handler, pattern=r"^(c|l|s)\|"))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
