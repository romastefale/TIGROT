import os
import re
import time
import asyncio
import logging
import requests
import hashlib
import html
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup

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
# LÓGICA DE SCRAPING (SEM API)
# =========================
def get_chorus_via_scraping(title, artist):
    """Busca a letra no Letras.mus.br e tenta extrair o refrão."""
    try:
        def slugify(text):
            text = text.lower()
            text = re.sub(r'[àáâãäå]', 'a', text)
            text = re.sub(r'[èéêë]', 'e', text)
            text = re.sub(r'[ìíîï]', 'i', text)
            text = re.sub(r'[òóôõö]', 'o', text)
            text = re.sub(r'[ùúûü]', 'u', text)
            text = re.sub(r'ç', 'c', text)
            text = re.sub(r'[^a-z0-9]', '-', text)
            return text.strip('-')

        # Monta a URL provável
        url = f"https://www.letras.mus.br/{slugify(artist)}/{slugify(title)}/"
        
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = session.get(url, headers=headers, timeout=5)

        if response.status_code != 200:
            return None

        soup = BeautifulSoup(response.text, 'html.parser')
        lyrics_div = soup.find('div', class_='lyric-canv') or soup.find('div', class_='cnt-letra')
        
        if not lyrics_div:
            return None

        # Preserva quebras de linha
        for br in lyrics_div.find_all("br"):
            br.replace_with("\n")
        
        full_text = lyrics_div.get_text("\n")

        # Busca bloco de refrão por tags comuns [Refrão], [Chorus] ou (Refrão)
        parts = re.split(r'(\[Refrão\]|\[Chorus\]|Refrão:)', full_text, flags=re.IGNORECASE)
        
        if len(parts) > 1:
            # O conteúdo do refrão geralmente vem após o marcador
            chorus = parts[2].strip().split('\n\n')[0]
            return chorus
        
        # Fallback: Se não achar tag de refrão, pega as primeiras 5 linhas
        lines = [line.strip() for line in full_text.split('\n') if line.strip()]
        return "\n".join(lines[:5]) + "..."

    except Exception as e:
        logger.error(f"Erro no scraping: {e}")
        return None

# =========================
# LÓGICA DE BUSCA DEEZER
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
# HANDLERS
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

    # Busca a letra apenas se o botão for clicado e não estiver no cache
    if state["show_lyrics"] and "chorus" not in m_data:
        # Executa o scraping em uma thread separada para não travar o bot
        loop = asyncio.get_event_loop()
        chorus = await loop.run_in_executor(_executor, get_chorus_via_scraping, m['title'], m['artist'])
        m_data["chorus"] = chorus or "⚠️ Refrão não encontrado automaticamente."

    layout = ""
    if state["show_cover"]: layout += f'<a href="{m["cover"]}">&#8203;</a>'
    
    layout += (
        f"🎹 {state['user_name']} está ouvindo...\n\n"
        f"🎧 <b>{html.escape(m['title'])}</b>\n"
        f"💿 <i>{html.escape(m['album'])}</i>\n"
        f"🎤 <i>{html.escape(m['artist'])}</i>"
    )

    if state["show_lyrics"]:
        lyrics = m_data.get("chorus", "⚠️ Carregando...")
        layout += f"\n\n<i>📜 Refrão:</i>\n\n<blockquote>{html.escape(lyrics)}</blockquote>"

    try:
        await query.edit_message_text(
            layout,
            parse_mode=ParseMode.HTML,
            reply_markup=get_final_markup(key, state["show_cover"], state["show_lyrics"]),
            link_preview_options=LinkPreviewOptions(is_disabled=not state["show_cover"])
        )
    except BadRequest: pass

async def search_music(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    tracks = await search_deezer(query)
    if not tracks:
        await update.message.reply_text("❌ Nenhuma música encontrada.")
        return

    keyboard = []
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
        keyboard.append([InlineKeyboardButton(f"{t['title']} — {t['artist']['name']}", callback_data=f"l|{t_key}")])

    await update.message.reply_text("🎧 Escolha uma música para exibir o card:", reply_markup=InlineKeyboardMarkup(keyboard))

def main():
    if not TOKEN:
        print("Erro: TELEGRAM_TOKEN não definido!")
        return

    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_music))
    app.add_handler(CallbackQueryHandler(cb_handler, pattern=r"^(c|l)\|"))

    if WEBHOOK_URL:
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN,
                        webhook_url=f"{WEBHOOK_URL}/{TOKEN}",
                        secret_token=WEBHOOK_SECRET, drop_pending_updates=True)
    else:
        app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
