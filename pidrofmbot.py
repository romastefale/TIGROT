import os
import re
import time
import asyncio
import logging
import requests
import hashlib
import html
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup

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
# LÓGICA DE LETRAS APRIMORADA
# =========================
def get_chorus_via_scraping(title, artist):
    """Busca a letra e identifica o refrão por tag ou repetição."""
    try:
        def clean_name(text):
            # Remove (feat...), [Remix], - Live, etc, que poluem a busca
            text = re.sub(r'[\(\[][Ff]eat\.?.*[\)\]]', '', text)
            text = re.sub(r'[\(\[][Rr]emix.*[\)\]]', '', text)
            text = re.sub(r'[\(\[].*[\)\]]', '', text)
            text = text.split(' - ')[0] # Remove sufixos após traço
            return text.strip()

        def slugify(text):
            text = clean_name(text).lower()
            # Remove acentos
            text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('utf-8')
            text = re.sub(r'[^a-z0-9]', '-', text)
            return text.strip('-')

        # Tenta a URL com nome limpo e a URL bruta
        slug_artist = slugify(artist)
        slug_title = slugify(title)
        
        urls = [
            f"https://www.letras.mus.br/{slug_artist}/{slug_title}/",
            f"https://www.letras.mus.br/{slugify(artist)}/{slugify(title)}/"
        ]

        full_text = ""
        for url in urls:
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = session.get(url, headers=headers, timeout=5)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                lyrics_div = soup.find('div', class_='lyric-canv') or soup.find('div', class_='cnt-letra')
                if lyrics_div:
                    for br in lyrics_div.find_all("br"): br.replace_with("\n")
                    full_text = lyrics_div.get_text("\n").strip()
                    break
        
        if not full_text: return None

        # 1. Tenta achar pela tag [Refrão] ou [Chorus]
        parts = re.split(r'(\[Refrão\]|\[Chorus\]|Refrão:|Chorus:)', full_text, flags=re.IGNORECASE)
        if len(parts) > 1:
            return parts[2].strip().split('\n\n')[0]

        # 2. Heurística: Identifica a estrofe que mais se repete (O Refrão real)
        stanzas = [s.strip() for s in full_text.split('\n\n') if len(s.strip()) > 20]
        if stanzas:
            counts = Counter(stanzas)
            most_common = counts.most_common(1)[0]
            if most_common[1] > 1: # Se repete pelo menos uma vez
                return most_common[0]
            return stanzas[0] # Fallback: primeira estrofe

        return None
    except Exception as e:
        logger.error(f"Erro no scraping: {e}")
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
    msg = (
        "🎹 Esse é o bot do @tigrao para mostrar as músicas que voce esta ouvindo! \n\n"
        "🎧 Para usar, basta digitar o nome da música…\n\n"
        "📜 Se quiser a letra do refrão só pedir!"
    )
    await update.message.reply_text(msg)

async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
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
                "title": t["title"],
                "artist": t["artist"]["name"],
                "album": t["album"]["title"],
                "cover": t["album"]["cover_xl"] or t["album"]["cover_big"],
            },
            "states": {},
            "expires_at": time.time() + 1800
        }
        btns.append([InlineKeyboardButton(f"{t['title']} — {t['artist']['name']}", callback_data=f"s|{t_key}")])
    
    await update.message.reply_text("🎧 Escolha uma música...", reply_markup=InlineKeyboardMarkup(btns))

async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("|")
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
        loop = asyncio.get_event_loop()
        chorus = await loop.run_in_executor(_executor, get_chorus_via_scraping, m['title'], m['artist'])
        m_data["chorus"] = chorus or "⚠️ Refrão não encontrado nesta fonte."

    layout = ""
    if state["show_cover"]: layout += f'<a href="{m["cover"]}">&#8203;</a>'
    
    layout += (
        f"🎹 {state['user_name']} está ouvindo...\n\n"
        f"🎧 <b>{html.escape(m['title'])}</b>\n"
        f"💿 <i>{html.escape(m['album'])}</i>\n"
        f"🎤 <i>{html.escape(m['artist'])}</i>"
    )

    if state["show_lyrics"]:
        layout += f"\n\n<i>📜 Lyrics:</i>\n\n<blockquote>{html.escape(m_data.get('chorus', '...'))}</blockquote>"

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
            "states": {}, "expires_at": time.time() + 1800
        }
        
        text_content = (
            f"🎹 {user_name} está ouvindo...\n\n"
            f"🎧 <b>{html.escape(t['title'])}</b>\n"
            f"💿 <i>{html.escape(t['album']['title'])}</i>\n"
            f"🎤 <i>{html.escape(t['artist']['name'])}</i>"
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

# =========================
# MAIN
# =========================
def main():
    if not TOKEN: return
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search))
    app.add_handler(CallbackQueryHandler(cb_handler, pattern=r"^(c|l|s)\|"))

    if WEBHOOK_URL:
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN,
                        webhook_url=f"{WEBHOOK_URL}/{TOKEN}",
                        secret_token=WEBHOOK_SECRET, drop_pending_updates=True)
    else:
        app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
