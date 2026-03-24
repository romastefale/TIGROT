import os
import re
import time
import asyncio
import logging
import requests
import hashlib
import html
from bs4 import BeautifulSoup
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
except ValueError:
    PORT = 8443

session = requests.Session()
music_cache = {} 
_executor = ThreadPoolExecutor(max_workers=4)

# =========================
# LÓGICA DE REFRÃO (SEM API - HEURÍSTICA)
# =========================
def _get_chorus_heuristic_sync(title, artist):
    """Busca a letra no letras.mus.br e encontra o bloco que mais se repete."""
    try:
        # Tenta formatar a URL para o site letras.mus.br
        clean_artist = re.sub(r'\W+', '-', artist.lower().strip())
        clean_title = re.sub(r'\W+', '-', title.lower().strip())
        url = f"https://www.letras.mus.br/{clean_artist}/{clean_title}/"
        
        r = session.get(url, timeout=5)
        if r.status_code != 200:
            return "⚠️ Letra não encontrada no banco de dados."
            
        soup = BeautifulSoup(r.text, 'html.parser')
        lyric_div = soup.find('div', class_='lyric-original') or soup.find('div', class_='cnt-letra')
        if not lyric_div:
            return "⚠️ Não foi possível processar a letra."

        # Pega os parágrafos da letra
        paragraphs = [p.get_text().strip() for p in lyric_div.find_all('p')]
        if not paragraphs:
            return "⚠️ Letra vazia ou protegida."

        # Heurística: O bloco que mais se repete é provavelmente o refrão
        counts = {}
        for p in paragraphs:
            counts[p] = counts.get(p, 0) + 1
        
        # Pega o mais frequente (se houver empate, pega o primeiro)
        chorus = max(counts, key=counts.get)
        
        # Se nada se repete, pega o maior bloco (geralmente estrofe principal)
        if counts[chorus] == 1:
            chorus = max(paragraphs, key=len)

        return chorus[:500] # Limite de caracteres
    except Exception as e:
        logger.error(f"Erro na heurística: {e}")
        return "⚠️ Erro ao extrair o refrão automaticamente."

# =========================
# BUSCA DEEZER (MOTOR)
# =========================
def score_track(track, query):
    title = track.get("title", "").lower()
    artist = track.get("artist", {}).get("name", "").lower()
    q = query.lower()
    if q in f"{title} {artist}": return 100
    if q in title: return 60
    return 0

def _search_deezer_sync(query, index=0):
    query = re.sub(r"[-_]+", " ", query).strip()
    try:
        r = session.get("https://api.deezer.com/search", params={"q": query, "index": index}, timeout=5)
        if r.status_code != 200: return []
        tracks = r.json().get("data", [])
        return sorted(tracks, key=lambda t: score_track(t, query), reverse=True)
    except Exception:
        return []

# =========================
# HELPERS DE UI (TOGGLES)
# =========================
def get_final_markup(key, show_cover, show_lyrics):
    # ✓ usado conforme solicitado
    btn_cover = "✅ 🖼️ Cover" if show_cover else "🖼️ Cover"
    btn_lyrics = "✅ 📜 Lyrics" if show_lyrics else "📜 Lyrics"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(btn_cover, callback_data=f"c|{key}"),
        InlineKeyboardButton(btn_lyrics, callback_data=f"l|{key}")
    ]])

# =========================
# MODO INLINE (RESOLVIDO)
# =========================
async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query
    if not query: return

    loop = asyncio.get_event_loop()
    tracks = await loop.run_in_executor(_executor, _search_deezer_sync, query)
    
    user_name = html.escape(update.inline_query.from_user.first_name)
    results = []

    for i, track in enumerate(tracks[:10]):
        try:
            t_key = hashlib.md5(track["link"].encode()).hexdigest()[:8]
            
            # Cache: Cover começa como TRUE (✅) conforme solicitado
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

            # Layout Inicial: Com link invisível da foto e Botão de Cover com CHECK
            results.append(
                InlineQueryResultArticle(
                    id=f"{t_key}_{i}",
                    title=f"{track['title']} — {track['artist']['name']}",
                    description=f"💿 {track['album']['title']}",
                    thumbnail_url=track['album']['cover_small'],
                    input_message_content=InputTextMessageContent(
                        message_text=(
                            f'<a href="{track["album"]["cover_big"]}">&#8203;</a>' # Link invisível
                            f"🎹 {user_name} está ouvindo...\n\n"
                            f"🎧 <b>{html.escape(track['title'])}</b>\n"
                            f"💿 <i>{html.escape(track['album']['title'])}</i>\n"
                            f"🎤 <i>{html.escape(track['artist']['name'])}</i>"
                        ),
                        parse_mode=ParseMode.HTML
                    ),
                    reply_markup=get_final_markup(t_key, True, False) # Cover ON por padrão
                )
            )
        except Exception: continue

    await update.inline_query.answer(results, cache_time=5)

# =========================
# CALLBACK HANDLER (AÇÃO DOS BOTÕES)
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
    msg_id = query.inline_message_id or (str(query.message.message_id) if query.message else None)

    # Gerencia estado específico da mensagem (se não existir, inicia com Cover ON)
    if msg_id not in m_data["states"]:
        m_data["states"][msg_id] = {
            "show_cover": True,
            "show_lyrics": False,
            "user_name": html.escape(query.from_user.first_name)
        }
    
    state = m_data["states"][msg_id]

    # Toggle das ações
    if action == "c":
        state["show_cover"] = not state["show_cover"]
    elif action == "l":
        state["show_lyrics"] = not state["show_lyrics"]

    # Busca o refrão se necessário (Heurística sem API)
    if state["show_lyrics"] and "chorus" not in m_data:
        loop = asyncio.get_event_loop()
        m_data["chorus"] = await loop.run_in_executor(_executor, _get_chorus_heuristic_sync, m['title'], m['artist'])

    # Reconstrói o texto
    layout = ""
    if state["show_cover"]:
        layout += f'<a href="{m["cover"]}">&#8203;</a>'
    
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
# BUSCA NO CHAT (DIRECT MESSAGE)
# =========================
async def search_music_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    loop = asyncio.get_event_loop()
    tracks = await loop.run_in_executor(_executor, _search_deezer_sync, text)
    
    if not tracks:
        await update.message.reply_text("❌ Nenhuma música encontrada.")
        return

    keyboard = []
    for t in tracks[:10]:
        t_key = hashlib.md5(t["link"].encode()).hexdigest()[:8]
        music_cache[t_key] = {
            "val": {
                "title": t["title"], "artist": t["artist"]["name"],
                "album": t["album"]["title"], "cover": t["album"]["cover_xl"] or t["album"]["cover_big"]
            },
            "states": {}, "expires_at": time.time() + 1800
        }
        # Botão s| envia o card direto
        keyboard.append([InlineKeyboardButton(f"{t['title']} — {t['artist']['name']}", callback_data=f"s|{t_key}")])

    await update.message.reply_text("🎧 Escolha uma música...", reply_markup=InlineKeyboardMarkup(keyboard))

async def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_music_chat))
    app.add_handler(CallbackQueryHandler(cb_handler, pattern=r"^(c|l)\|"))
    app.add_handler(CallbackQueryHandler(cb_handler, pattern=r"^s\|")) # Inicia o card no chat

    if WEBHOOK_URL:
        await app.bot.set_webhook(url=f"{WEBHOOK_URL}/{TOKEN}", secret_token=WEBHOOK_SECRET)
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f"{WEBHOOK_URL}/{TOKEN}")
    else:
        app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
