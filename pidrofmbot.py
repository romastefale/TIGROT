import asyncio
import hashlib
import html
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================
# Logging
# =========================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# =========================
# Configurações / Env
# =========================
@dataclass(slots=True)
class Settings:
    token: str
    genius_api_key: str | None
    openai_api_key: str | None
    webhook_url: str | None
    webhook_secret: str | None
    port: int

def load_settings() -> Settings:
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    if not token:
        raise ValueError("Configure TELEGRAM_TOKEN no ambiente")

    raw_url = os.getenv("WEBHOOK_URL") or os.getenv("RAILWAY_PUBLIC_DOMAIN") or os.getenv("RAILWAY_STATIC_URL")
    webhook_url = None
    if raw_url:
        webhook_url = raw_url.strip().rstrip("/")
        if not webhook_url.startswith(("http://", "https://")):
            webhook_url = f"https://{webhook_url}"

    secret = os.getenv("WEBHOOK_SECRET") or token.replace(":", "")[:32]
    
    try:
        port = int(os.getenv("PORT", "8443"))
    except ValueError:
        port = 8443

    return Settings(
        token=token,
        genius_api_key=os.getenv("GENIUS_API_KEY"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        webhook_url=webhook_url,
        webhook_secret=secret,
        port=port
    )

# =========================
# Cache e Variáveis Globais
# =========================
SEARCH_MAX_RESULTS = 10
CACHE_TTL = 1800 # 30 min

session = requests.Session()
music_cache: dict[str, dict[str, Any]] = {}

def get_now() -> float:
    return time.time()

def cleanup_cache():
    now = get_now()
    expired = [k for k, v in music_cache.items() if v.get("expires_at", 0) <= now]
    for k in expired:
        music_cache.pop(k, None)

# =========================
# Lógica de Busca e Letras
# =========================
def normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_\-]+", " ", query or "")).strip()

def score_track(track: dict[str, Any], query: str) -> int:
    q = query.casefold()
    title = str(track.get("title", "")).casefold()
    artist = str(track.get("artist", {}).get("name", "")).casefold()
    score = 0
    if q == title: score += 100
    if q in title: score += 50
    if q in f"{title} {artist}": score += 20
    return score

def search_deezer(query: str) -> list[dict[str, Any]]:
    q = normalize_query(query)
    if not q: return []
    
    try:
        resp = session.get("https://api.deezer.com/search", params={"q": q, "limit": 15}, timeout=10)
        if resp.status_code != 200: return []
        data = resp.json().get("data", [])
        data = sorted(data, key=lambda t: score_track(t, q), reverse=True)
        
        results = []
        for item in data[:SEARCH_MAX_RESULTS]:
            album = item.get("album", {})
            cover = album.get("cover_xl") or album.get("cover_big") or album.get("cover")
            
            results.append({
                "title": item.get("title", "Unknown"),
                "artist": item.get("artist", {}).get("name", "Unknown"),
                "album": album.get("title", "Single"),
                "deezer_url": item.get("link", ""),
                "cover": cover
            })
        return results
    except Exception:
        logger.exception("Erro Deezer")
        return []

def get_genius_url(title: str, artist: str, api_key: str | None) -> str | None:
    if not api_key: return None
    try:
        resp = session.get("https://api.genius.com/search", 
                           headers={"Authorization": f"Bearer {api_key}"},
                           params={"q": f"{title} {artist}"}, timeout=10)
        hits = resp.json().get("response", {}).get("hits", [])
        return hits[0]["result"]["url"] if hits else None
    except: return None

def fetch_lyrics(genius_url: str | None) -> str | None:
    if not genius_url or "genius.com" not in genius_url: return None
    try:
        resp = session.get(genius_url, timeout=10)
        soup = BeautifulSoup(resp.text, "lxml")
        containers = soup.find_all("div", {"data-lyrics-container": "true"})
        return "\n".join([c.get_text("\n") for c in containers]).strip()
    except: return None

def extract_chorus(lyrics: str | None, openai_key: str | None) -> str:
    if not lyrics: return "Letra não encontrada no Genius."
    
    if openai_key:
        try:
            client = OpenAI(api_key=openai_key)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Extraia apenas o refrão principal da letra. Máximo 8 linhas, sem comentários."},
                    {"role": "user", "content": lyrics}
                ],
                temperature=0.3
            )
            content = resp.choices[0].message.content
            return content.strip() if content else "Erro ao extrair o refrão."
        except: pass

    blocks = [b.strip() for b in re.split(r"\n\s*\n", lyrics) if b.strip()]
    if not blocks: return "Não foi possível extrair."
    best = max(blocks, key=lambda b: (blocks.count(b), len(b)))
    return "\n".join(best.splitlines()[:8])

# =========================
# Utilitários de UX
# =========================
async def get_or_fetch_chorus(m: dict, context: ContextTypes.DEFAULT_TYPE) -> str:
    st = context.application.bot_data["settings"]
    g_url = await asyncio.to_thread(get_genius_url, m['title'], m['artist'], st.genius_api_key)
    lyrics = await asyncio.to_thread(fetch_lyrics, g_url)
    chorus = await asyncio.to_thread(extract_chorus, lyrics, st.openai_api_key)
    return chorus

def get_final_markup(key: str, show_cover: bool, show_lyrics: bool) -> InlineKeyboardMarkup:
    # UX: Adiciona um checkmark ✓ minimalista se o toggle estiver ativo
    btn_cover = "✓ ❑ Cover" if show_cover else "❑ Cover"
    btn_lyrics = "✓ ♩Lyrics" if show_lyrics else "♩Lyrics"
    
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(btn_cover, callback_data=f"c|{key}"),
        InlineKeyboardButton(btn_lyrics, callback_data=f"l|{key}")
    ]])

async def safe_edit_message(query, text: str, markup: InlineKeyboardMarkup, disable_preview: bool):
    try:
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
            link_preview_options=LinkPreviewOptions(is_disabled=disable_preview)
        )
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            logger.error(f"Erro ao editar mensagem: {e}")

# =========================
# Telegram Handlers
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "𝄢 Esse é o bot do @tigrao para mostrar as músicas que voce esta ouvindo! \n\n"
        "𝄞 Para usar, basta digitar o nome da música…\n\n"
        "𝄡  Se quiser a letra do refrão só pedir!"
    )
    await update.message.reply_text(msg)

async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    if not query: return

    cleanup_cache()
    tracks = await asyncio.to_thread(search_deezer, query)
    
    if not tracks:
        await update.message.reply_text("❌ Nenhuma música encontrada.")
        return

    btns = []
    for t in tracks:
        t_key = hashlib.md5(f"{t['deezer_url']}".encode()).hexdigest()[:8]
        # Inicia o cache com um sub-dicionário de 'states' para gerenciar as mensagens
        music_cache[t_key] = {"val": t, "states": {}, "expires_at": get_now() + CACHE_TTL}
        btns.append([InlineKeyboardButton(f"{t['title']} - {t['artist']}", callback_data=f"s|{t_key}")])
    
    markup = InlineKeyboardMarkup(btns)
    await update.message.reply_text("♪ Escolha uma música...", reply_markup=markup)

async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("|")
    action = parts[0]
    key = parts[1]

    m_data = music_cache.get(key)
    if not m_data:
        await query.edit_message_text("❌ Busca expirada. Digite o nome da música novamente.")
        return
        
    m = m_data["val"]
    message_id = str(query.message.message_id)

    # 1. Usuário selecionou a música na lista
    if action == "s":
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("✓ Sim", callback_data=f"y|{key}"),
            InlineKeyboardButton("✕ Não", callback_data=f"n|{key}")
        ]])
        await query.edit_message_text("♪ Letra?", reply_markup=markup)

    # 2. Usuário está nos controles finais (Toggle e escolhas iniciais)
    elif action in ("y", "n", "c", "l"):
        
        # Garante que o estado dessa mensagem específica exista
        if message_id not in m_data["states"]:
            m_data["states"][message_id] = {
                "show_cover": False,
                "show_lyrics": False,
                "user_name": html.escape(query.from_user.first_name)
            }
        
        state = m_data["states"][message_id]
        user_name = state["user_name"]

        # Atualiza os Toggles baseados no clique
        if action == "y":
            state["show_lyrics"] = True
        elif action == "n":
            state["show_lyrics"] = False
        elif action == "c":
            state["show_cover"] = not state["show_cover"] # Inverte o estado da Capa
        elif action == "l":
            state["show_lyrics"] = not state["show_lyrics"] # Inverte o estado da Letra

        # Se precisa mostrar a letra e ainda não buscou, busca agora e salva globalmente pra faixa
        if state["show_lyrics"] and "chorus" not in m_data:
            await query.edit_message_text("🎧 Buscando refrão...")
            m_data["chorus"] = await get_or_fetch_chorus(m, context)

        # Constrói o layout de forma dinâmica complementando as opções
        layout = ""
        
        # 1º Bloco: A Capa (Se ativo, joga o link invisível)
        if state["show_cover"]:
            layout += f'<a href="{m["cover"]}">&#8203;</a>'
            
        # 2º Bloco: O cabeçalho e info da música
        layout += (
            f"♫ {user_name} está ouvindo...\n\n"
            f"♬ <b>{html.escape(m['title'])}</b>\n"
            f"▶ <i>{html.escape(m['album'])}</i>\n"
            f"★ <i>{html.escape(m['artist'])}</i>"
        )

        # 3º Bloco: A Letra (Adiciona ao final se estiver ativo)
        if state["show_lyrics"]:
            layout += (
                f"\n\n<i>♪ ♫ Lyrics:</i>\n\n"
                f"<blockquote>{html.escape(m_data['chorus'])}</blockquote>"
            )

        # Monta os botões com seus estados atuais (✓ ou não)
        markup = get_final_markup(key, state["show_cover"], state["show_lyrics"])

        # O preview do link deve ser DESATIVADO caso a capa não deva ser exibida.
        disable_preview = not state["show_cover"]

        await safe_edit_message(query, layout, markup, disable_preview)

async def post_init(app: Application):
    me = await app.bot.get_me()
    logger.info("Bot conectado com sucesso! Username: @%s", me.username)

# =========================
# Main (Railway Setup)
# =========================
def main():
    st = load_settings()
    app = Application.builder().token(st.token).post_init(post_init).build()
    app.bot_data["settings"] = st

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search))
    app.add_handler(CallbackQueryHandler(cb_handler))

    if st.webhook_url:
        webhook_path = st.token.replace(":", "_")
        target_url = f"{st.webhook_url}/{webhook_path}"
        logger.info(f"Iniciando em modo WEBHOOK na porta {st.port}")
        logger.info(f"URL configurada: {target_url}")
        
        app.run_webhook(
            listen="0.0.0.0",
            port=st.port,
            url_path=webhook_path,
            webhook_url=target_url,
            secret_token=st.webhook_secret
        )
    else:
        logger.info("Iniciando em modo POLLING")
        app.run_polling()

if __name__ == "__main__":
    main()
