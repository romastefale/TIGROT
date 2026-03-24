import asyncio
import hashlib
import html
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
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

    # Detectar URL para Webhook (Prioridade: WEBHOOK_URL > Railway Domain)
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
SEARCH_PAGE_SIZE = 5
SEARCH_MAX_RESULTS = 15
CACHE_TTL = 1800 # 30 min

session = requests.Session()
search_cache: dict[str, dict[str, Any]] = {}
music_cache: dict[str, dict[str, Any]] = {}
session_cache: dict[str, dict[str, Any]] = {}

def get_now() -> float:
    return time.time()

def cleanup_cache():
    now = get_now()
    for store in (search_cache, music_cache, session_cache):
        expired = [k for k, v in store.items() if v.get("expires_at", 0) <= now]
        for k in expired:
            store.pop(k, None)

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
        resp = session.get("https://api.deezer.com/search", params={"q": q, "limit": SEARCH_MAX_RESULTS}, timeout=10)
        if resp.status_code != 200: return []
        data = resp.json().get("data", [])
        data = sorted(data, key=lambda t: score_track(t, q), reverse=True)
        
        results = []
        for item in data:
            results.append({
                "title": item.get("title", "Unknown"),
                "artist": item.get("artist", {}).get("name", "Unknown"),
                "album": item.get("album", {}).get("title", "Single"),
                "thumb": item.get("album", {}).get("cover_big", ""),
                "deezer_url": item.get("link", ""),
                "preview": item.get("preview", ""),
                "genius_url": None
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

def extract_chorus(lyrics: str | None, openai_key: str | None) -> tuple[str, str]:
    if not lyrics: return "Letra não encontrada.", "Erro"
    
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
            return (content.strip() if content else "Erro ao extrair"), "OpenAI"
        except: pass

    # Heurística local (bloco mais repetido)
    blocks = [b.strip() for b in re.split(r"\n\s*\n", lyrics) if b.strip()]
    if not blocks: return "Não foi possível extrair.", "Local"
    best = max(blocks, key=lambda b: (blocks.count(b), len(b)))
    return "\n".join(best.splitlines()[:8]), "Heurística Local"

# =========================
# Telegram Handlers
# =========================
def get_track_markup(music: dict[str, Any], key: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if key:
        rows.append([
            InlineKeyboardButton("✓ Refrão", callback_data=f"y|{key}"),
            InlineKeyboardButton("✕ Só Info", callback_data=f"n|{key}")
        ])
    
    links = []
    if music.get("preview"): links.append(InlineKeyboardButton("🎧 Preview", url=music["preview"]))
    if music.get("deezer_url"): links.append(InlineKeyboardButton("🔗 Deezer", url=music["deezer_url"]))
    if links: rows.append(links)
    return InlineKeyboardMarkup(rows)

async def cmd_music(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Uso: /music nome da música")
        return

    cleanup_cache()
    # Adicionado asyncio.to_thread para não bloquear o bot!
    tracks = await asyncio.to_thread(search_deezer, query)
    
    if not tracks:
        await update.message.reply_text("Nenhuma música encontrada.")
        return

    session_id = uuid.uuid4().hex[:10]
    session_cache[session_id] = {"query": query, "tracks": tracks, "expires_at": get_now() + CACHE_TTL}
    
    await send_page(update, session_id, 0)

async def send_page(update: Update, session_id: str, page: int):
    data = session_cache.get(session_id)
    if not data: return
    
    start = page * SEARCH_PAGE_SIZE
    chunk = data["tracks"][start:start+SEARCH_PAGE_SIZE]
    
    btns = []
    for t in chunk:
        t_key = hashlib.md5(f"{t['deezer_url']}".encode()).hexdigest()[:8]
        music_cache[t_key] = {"val": t, "expires_at": get_now() + CACHE_TTL}
        btns.append([InlineKeyboardButton(f"{t['title']} - {t['artist']}", callback_data=f"s|{t_key}")])
    
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("◀️", callback_data=f"p|{session_id}|{page-1}"))
    if start + SEARCH
