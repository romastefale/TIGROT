import os
import re
import time
import asyncio
import logging
import requests
import hashlib
import html
import google.generativeai as genai
from concurrent.futures import ThreadPoolExecutor

from telegram import (
    Update,
    InlineQueryResultPhoto,
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
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", TOKEN.replace(":", "")[:20] if TOKEN else None)

try:
    PORT = int(os.getenv("PORT", 8443))
except ValueError:
    PORT = 8443

session = requests.Session()
music_cache = {}  # Armazena dados das músicas e estados (toggles)
_executor = ThreadPoolExecutor(max_workers=4)

# =========================
# LÓGICA DE IA (GEMINI)
# =========================
async def get_chorus_via_gemini(title, artist, album):
    if not GEMINI_KEY:
        return "⚠️ Erro: GEMINI_API_KEY não configurada."
    try:
        genai.configure(api_key=GEMINI_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = (
            f"Forneça o Refrão da música {title}, de {artist}, do album {album}. "
            "Retorne APENAS as linhas da letra do refrão. Sem aspas, sem introdução, sem títulos."
        )
        safety = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
        resp = await model.generate_content_async(prompt, safety_settings=safety)
        return resp.text.strip() if resp.text else "⚠️ Letra não encontrada."
    except Exception:
        return "⚠️ Falha ao buscar a letra com IA."

# =========================
# BUSCA E CACHE
# =========================
def score_track(track, query):
    title = track.get("title", "").lower()
    artist = track.get("artist", {}).get("name", "").lower()
    q = query.lower()
    score = 0
    if q in f"{title} {artist}": score += 100
    if q in title: score += 60
    return score

def _search_deezer_sync(query):
    query = re.sub(r"[-_]+", " ", query).strip()
    try:
        r = session.get("https://api.deezer.com/search", params={"q": query, "limit": 15}, timeout=5)
        if r.status_code != 200: return []
        tracks = r.json().get("data", [])
        return sorted(tracks, key=lambda t: score_track(t, query), reverse=True)
    except Exception:
        return []

async def search_deezer(query):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _search_deezer_sync, query)

# =========================
# HELPERS DE UI
# =========================
def get_final_markup(key, show_cover, show_lyrics):
    btn_cover = "✅ 🖼️ Cover" if show_cover else "🖼️ Cover"
    btn_lyrics = "✅ 📜 Lyrics" if show_lyrics else "📜 Lyrics"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(btn_cover, callback_data=f"c|{key}"),
        InlineKeyboardButton(btn_lyrics, callback_data=f"l|{key}")
    ]])

# =========================
# MODO INLINE
# =========================
async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query
    if not query: return

    tracks = await search_deezer(query)
    user_name = html.escape(update.inline_query.from_user.first_name)
    results = []

    for i, track in enumerate(tracks[:10]):
        try:
            t_key = hashlib.md5(track["link"].encode()).hexdigest()[:8]
            # Salva no cache para os botões funcionarem depois do envio
            music_cache[t_key] = {
                "val": {
                    "title": track["title"],
                    "artist": track["artist"]["name"],
                    "album": track["album"]["title"],
                    "cover": track["album"]["cover_xl"] or track["album"]["cover_big"],
                },
                "states": {},
                "expires_at": time.time() + 1800
            }

            results.append(
                InlineQueryResultPhoto(
                    id=f"{t_key}_{i}",
                    photo_url=track["album"]["cover_big"],
                    thumbnail_url=track["album"]["cover_small"],
                    title=f"{track['title']} — {track['artist']['name']}",
                    description=f"Album: {track['album']['title']}",
                    caption=(
                        f"🎹 {user_name} está ouvindo...\n\n"
                        f"🎧 <b>{html.escape(track['title'])}</b>\n"
                        f"💿 <i>{html.escape(track['album']['title'])}</i>\n"
                        f"🎤 <i>{html.escape(track['artist']['name'])}</i>"
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_final_markup(t_key, False, False)
                )
            )
        except Exception: continue

    await update.inline_query.answer(results, cache_time=5)

# =========================
# CALLBACK HANDLER (TOGGLES)
# =========================
async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("|")
    if len(parts) < 2: return
    action, key = parts[0], parts[1]

    m_data = music_cache.get(key)
    if not m_data: return
        
    m = m_data["val"]
    msg_id = str(query.message.message_id) if query.message else query.inline_message_id

    if msg_id not in m_data["states"]:
        m_data["states"][msg_id] = {
            "show_cover": False,
            "show_lyrics": False,
            "user_name": html.escape(query.from_user.first_name)
        }
    
    state = m_data["states"][msg_id]
    if action == "c": state["show_cover"] = not state["show_cover"]
    elif action == "l": state["show_lyrics"] = not state["show_lyrics"]

    if state["show_lyrics"] and "chorus" not in m_data:
        # Edit temporário para feedback
        state["chorus"] = await get_chorus_via_gemini(m['title'], m['artist'], m['album'])

    layout = ""
    if state["show_cover"]: layout += f'<a href="{m["cover"]}">&#8203;</a>'
    
    layout += (
        f"🎹 {state['user_name']} está ouvindo...\n\n"
        f"🎧 <b>{html.escape(m['title'])}</b>\n"
        f"💿 <i>{html.escape(m['album'])}</i>\n"
        f"🎤 <i>{html.escape(m['artist'])}</i>"
    )

    if state["show_lyrics"]:
        layout += f"\n\n<i>📜 Lyrics:</i>\n\n<blockquote>{html.escape(m_data['chorus'])}</blockquote>"

    try:
        await query.edit_message_text(
            layout,
            parse_mode=ParseMode.HTML,
            reply_markup=get_final_markup(key, state["show_cover"], state["show_lyrics"]),
            link_preview_options=LinkPreviewOptions(is_disabled=not state["show_cover"])
        )
    except BadRequest: pass

# =========================
# BUSCA NO CHAT
# =========================
async def search_music(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    tracks = await search_deezer(query)
    if not tracks:
        await update.message.reply_text("❌ Nenhuma música encontrada.")
        return

    keyboard = []
    for t in tracks[:10]:
        t_key = hashlib.md5(t["link"].encode()).hexdigest()[:8]
        music_cache[t_key] = {
            "val": {
                "title": t["title"],
                "artist": t["artist"]["name"],
                "album": t["album"]["title"],
                "cover": t["album"]["cover_xl"] or t["album"]["cover_big"],
            },
            "states": {},
            "expires_at": time.time() + 1800
        }
        keyboard.append([InlineKeyboardButton(f"{t['title']} — {t['artist']['name']}", callback_data=f"s|{t_key}")])

    await update.message.reply_text("🎧 Escolha uma música...", reply_markup=InlineKeyboardMarkup(keyboard))

async def select_track_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Simula o clique inicial no chat para mostrar o card
    query = update.callback_query
    await query.answer()
    key = query.data.split("|")[1]
    # Reutiliza a lógica do toggle para exibir o card inicial (action 'n' não existe mais, usamos o estado base)
    query.data = f"l|{key}" # Força o fluxo de exibição
    # Como queremos apenas exibir sem a letra primeiro, mas o handler gerencia isso:
    await cb_handler(update, context)

# =========================
# MAIN
# =========================
def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_music))
    app.add_handler(CallbackQueryHandler(cb_handler, pattern=r"^(c|l)\|"))
    app.add_handler(CallbackQueryHandler(cb_handler, pattern=r"^s\|")) # Clique na lista do chat

    if WEBHOOK_URL:
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN,
                        webhook_url=f"{WEBHOOK_URL}/{TOKEN}",
                        secret_token=WEBHOOK_SECRET, drop_pending_updates=True)
    else:
        app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
