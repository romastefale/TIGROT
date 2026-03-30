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
# SANITIZAÇÃO DE IDIOMAS PROIBIDOS
# =========================
# RegEx corrigido: Uso de \U com 8 dígitos para caracteres estendidos (Chinês)
FORBIDDEN_ALPHABETS_REGEX = re.compile(
    r'['
    r'\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF' # Árabe
    r'\u0400-\u04FF\u0500-\u052F\u2DE0-\u2DFF\uA640-\uA69F'               # Cirílico
    r'\u4E00-\u9FFF\u3400-\u4DBF\U00020000-\U0002A6DF'                   # Chinês (Corrigido para \U maiúsculo)
    r'\u0900-\u097F'                                                     # Hindi
    r'\u0980-\u09FF'                                                     # Bengali
    r']'
)

def contains_forbidden(text):
    """Verifica se o texto possui algum caractere dos alfabetos proibidos."""
    if not text: return False
    return bool(FORBIDDEN_ALPHABETS_REGEX.search(text))

def sanitize_text(text):
    """Traduz para o inglês ou omite os caracteres proibidos garantindo o alfabeto latino."""
    if not text: return text
    if not contains_forbidden(text): return text
    
    # REGRA 1.1: Tentativa de tradução via API gratuita do Google Translate (para Inglês/Latino)
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "auto", "tl": "en", "dt": "t", "q": text}
        resp = session.get(url, params=params, timeout=3)
        if resp.status_code == 200:
            translated = "".join([sentence[0] for sentence in resp.json()[0]])
            # Verifica se a tradução realmente limpou os caracteres proibidos
            if not contains_forbidden(translated):
                return translated.strip()
    except Exception as e:
        logger.error(f"Erro na tradução automática: {e}")
        pass
    
    # REGRA 1.2: Se falhar a tradução, omitir apenas os termos proibidos
    cleaned = FORBIDDEN_ALPHABETS_REGEX.sub('', text).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned) # Remove espaços excessivos deixados para trás
    return cleaned if cleaned else "Desconhecido"


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
        
        # REGRA 1.1.1: Se a letra estiver num idioma proibido, simula sucesso e oculta a letra.
        if contains_forbidden(full_lyrics):
            return "🎵 [Letra bloqueada: Idioma original não suportado neste grupo]"

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
    msg = (
        "🎹 Esse é o bot do @tigrao para mostrar as músicas que voce esta ouvindo! \n\n"
        "🎧 Para usar, basta digitar o nome da música…\n\n"
        "📜 Se quiser a letra do refrão só pedir!"
    )
    await update.message.reply_text(msg)

async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    user_id = update.message.from_user.id
    loop = asyncio.get_event_loop()
    tracks = await loop.run_in_executor(_executor, search_deezer_sync, query)
    
    if not tracks:
        await update.message.reply_text("❌ Nenhuma música encontrada.")
        return

    btns = []
    for t in tracks[:8]:
        # Aplicação das regras 1.1 e 1.2
        title = sanitize_text(t["title"])
        artist = sanitize_text(t["artist"]["name"])
        album = sanitize_text(t["album"]["title"])
        
        t_key = hashlib.md5(t["link"].encode()).hexdigest()[:8]
        music_cache[t_key] = {
            "val": {
                "title": title, "artist": artist,
                "album": album, "cover": t["album"]["cover_xl"] or t["album"]["cover_big"],
            },
            "author_id": user_id,
            "states": {}, 
            "expires_at": time.time() + 1800
        }
        btns.append([InlineKeyboardButton(f"{title} — {artist}", callback_data=f"s|{t_key}")])
    
    await update.message.reply_text("🎧 Escolha uma música...", reply_markup=InlineKeyboardMarkup(btns))

async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    parts = query.data.split("|")
    action, key = parts[0], parts[1]
    m_data = music_cache.get(key)
    
    if not m_data:
        await query.answer("❌ Sessão expirada.", show_alert=True)
        return

    if m_data.get("author_id") and query.from_user.id != m_data["author_id"]:
        await query.answer("⚠️ Apenas quem enviou a mensagem pode alterar o layout!", show_alert=True)
        return

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
    user_id = update.inline_query.from_user.id
    loop = asyncio.get_event_loop()
    tracks = await loop.run_in_executor(_executor, search_deezer_sync, query)
    user_name = html.escape(update.inline_query.from_user.first_name)
    results = []

    for i, t in enumerate(tracks[:10]):
        # Aplicação das regras 1.1 e 1.2
        title = sanitize_text(t["title"])
        artist = sanitize_text(t["artist"]["name"])
        album = sanitize_text(t["album"]["title"])
        
        t_key = hashlib.md5(t["link"].encode()).hexdigest()[:8]
        music_cache[t_key] = {
            "val": {
                "title": title, "artist": artist, 
                "album": album, "cover": t["album"]["cover_xl"]
            },
            "author_id": user_id,
            "states": {}, 
            "expires_at": time.time() + 1800
        }
        
        text_content = (
            f"🎹 {user_name} está ouvindo...\n\n"
            f"🎧 <b>{html.escape(title)}</b> - <i>{html.escape(album)} — {html.escape(artist)}</i>"
        )

        results.append(InlineQueryResultArticle(
            id=f"{t_key}_{i}",
            title=f"{title} — {artist}",
            description=f"Album: {album}",
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
