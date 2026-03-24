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
from telegram.error import Conflict
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
# Env / settings
# =========================
def load_local_env(path: str = ".env") -> None:
    if not os.path.exists(path):
        return

    with open(path, encoding="utf-8") as env_file:
def load_local_env(path: str = ".env") -> None:
    env_path = os.path.join(os.getcwd(), path)
    if not os.path.exists(env_path):
        return

    with open(env_path, encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


load_local_env()


@dataclass(slots=True)
class Settings:
    telegram_token: str
    run_mode: str
    webhook_url: str | None
    webhook_secret: str | None
    port: int
    genius_api_key: str | None
    openai_api_key: str | None


def _normalize_webhook_url(value: str | None) -> str | None:
    if not value:
        return None
    url = value.strip().rstrip("/")
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


def _running_on_railway() -> bool:
    return bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID") or os.getenv("RAILWAY_SERVICE_ID"))


def load_settings() -> Settings:
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    if not token:
        raise ValueError("Configure TELEGRAM_TOKEN no ambiente")

    explicit_webhook = _normalize_webhook_url(os.getenv("WEBHOOK_URL"))
    railway_domain = _normalize_webhook_url(os.getenv("RAILWAY_PUBLIC_DOMAIN"))
    static_domain = _normalize_webhook_url(os.getenv("RAILWAY_STATIC_URL"))

    webhook_url = explicit_webhook or railway_domain or static_domain

    run_mode = (os.getenv("RUN_MODE", "auto").strip().lower() or "auto")
    if run_mode not in {"auto", "polling", "webhook"}:
        run_mode = "auto"

    if run_mode == "auto":
        run_mode = "webhook" if webhook_url else "polling"

    # Railway sem webhook tende a gerar conflito de polling com múltiplas instâncias
    if _running_on_railway() and run_mode == "polling":
        logger.warning(
            "Railway detectado em modo polling. Configure WEBHOOK_URL para evitar 409 Conflict."
        )
DEEZER_SEARCH_URL = "https://api.deezer.com/search"
GENIUS_SEARCH_URL = "https://api.genius.com/search"
GENIUS_BASE_URL = "https://genius.com"
LYRICS_OVH_URL = "https://api.lyrics.ovh/v1/{artist}/{title}"
SEARCH_PAGE_SIZE = 5
SEARCH_MAX_RESULTS = 15
SEARCH_CACHE_TTL = 600
MUSIC_CACHE_TTL = 1800

session = requests.Session()
cache: dict[str, dict[str, Any]] = {}
music_cache: dict[str, dict[str, Any]] = {}
search_sessions: dict[str, dict[str, Any]] = {}


@dataclass(slots=True)
class Settings:
    token: str | None
    genius_api_key: str | None
    openai_api_key: str | None
    webhook_url: str | None
    webhook_secret: str | None
    port: int
    railway_public_domain: str | None


def normalize_webhook_url(url: str | None) -> str | None:
    if not url:
        return None

    normalized = url.strip().rstrip("/")
    if not normalized:
        return None
    if not normalized.startswith(("http://", "https://")):
        normalized = f"https://{normalized}"
    return normalized


def detect_railway_public_url() -> str | None:
    candidates = [
        os.getenv("WEBHOOK_URL"),
        os.getenv("RAILWAY_PUBLIC_DOMAIN"),
        os.getenv("RAILWAY_STATIC_URL"),
    ]
    for candidate in candidates:
        normalized = normalize_webhook_url(candidate)
        if normalized:
            return normalized
    return None


def running_on_railway() -> bool:
    return bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID") or os.getenv("RAILWAY_SERVICE_ID"))


def get_settings() -> Settings:
    token = os.getenv("TELEGRAM_TOKEN")
    railway_public_domain = detect_railway_public_url()
    webhook_url = railway_public_domain
    webhook_secret = os.getenv(
        "WEBHOOK_SECRET",
        token.replace(":", "")[:20] if token else None,
    )

    try:
        port = int(os.getenv("PORT", "8443"))
    except ValueError:
        port = 8443

    secret = os.getenv("WEBHOOK_SECRET")
    if not secret:
        secret = token.replace(":", "")[:32]

    return Settings(
        telegram_token=token,
        run_mode=run_mode,
        webhook_url=webhook_url,
        webhook_secret=secret,
        port=port,
        genius_api_key=os.getenv("GENIUS_API_KEY"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
    )


# =========================
# Domain logic
# =========================
SEARCH_PAGE_SIZE = 5
SEARCH_MAX_RESULTS = 15
CACHE_TTL_SECONDS = 600
SELECTION_TTL_SECONDS = 1800

DEEZER_SEARCH_URL = "https://api.deezer.com/search"
GENIUS_SEARCH_URL = "https://api.genius.com/search"

http = requests.Session()


search_cache: dict[str, dict[str, Any]] = {}
selection_cache: dict[str, dict[str, Any]] = {}
search_sessions: dict[str, dict[str, Any]] = {}


def now() -> float:
    return time.time()


def _cleanup() -> None:
    t = now()
    for bucket in (search_cache, selection_cache, search_sessions):
        expired = [k for k, v in bucket.items() if v.get("expires_at", 0) <= t]
        for key in expired:
            bucket.pop(key, None)


def normalize_query(text: str) -> str:
    normalized = re.sub(r"[_\-]+", " ", text or "")
        logger.warning("PORT inválido, usando 8443")
        port = 8443

    return Settings(
        token=token,
        genius_api_key=os.getenv("GENIUS_API_KEY"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
        port=port,
        railway_public_domain=railway_public_domain,
    )


def build_webhook_target_url(webhook_url: str, token: str) -> str:
    return f"{webhook_url}/{token}"


settings = get_settings()
client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None


# =========================
# HELPERS / FORMAT
# =========================

def now_ts() -> float:
    return time.time()


def escape_markdown(text: str) -> str:
    text = text or ""
    for char in ("_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"):
        text = text.replace(char, f"\\{char}")
    return text


def normalize_query(query: str) -> str:
    normalized = re.sub(r"[_\-]+", " ", query or "")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def score_track(track: dict[str, Any], query: str) -> int:
    q = normalize_query(query).casefold()
    title = normalize_query(track.get("title", "")).casefold()
    artist = normalize_query((track.get("artist") or {}).get("name", "")).casefold()

    score = 0
    if q == title:
        score += 100
    if q and q in title:
        score += 50
    if q and q in f"{title} {artist}":
    normalized_query = normalize_query(query).casefold()
    normalized_title = normalize_query(track.get("title", "")).casefold()
    normalized_artist = normalize_query(track.get("artist", {}).get("name", "")).casefold()

    score = 0
    if normalized_query == normalized_title:
        score += 100
    if normalized_query in normalized_title:
        score += 50
    if normalized_query and normalized_query in f"{normalized_title} {normalized_artist}":
        score += 20
    return score


def escape_html(text: str) -> str:
    return html.escape(text or "")


def format_track_message(track: dict[str, Any]) -> str:
    return (
        f"♬ <b>{escape_html(track['title'])}</b>\n"
        f"★ <i>{escape_html(track['artist'])}</i>\n"
        f"▶ <i>{escape_html(track['album'])}</i>"
    )


def make_track_key(track: dict[str, Any]) -> str:
    raw = f"{track.get('title','')}|{track.get('artist','')}|{track.get('deezer_url','')}|{track.get('lyrics_url','')}"
    return hashlib.md5(raw.encode()).hexdigest()[:10]


def save_track(track: dict[str, Any]) -> str:
    _cleanup()
    key = make_track_key(track)
    selection_cache[key] = {
        "value": track,
        "expires_at": now() + SELECTION_TTL_SECONDS,
    }
    return key


def get_track(key: str) -> dict[str, Any] | None:
    _cleanup()
    item = selection_cache.get(key)
    return None if item is None else item["value"]


def save_search_session(query: str, tracks: list[dict[str, Any]]) -> str:
    _cleanup()
    session_id = uuid.uuid4().hex[:12]
    search_sessions[session_id] = {
        "value": {"query": query, "tracks": tracks},
        "expires_at": now() + CACHE_TTL_SECONDS,
    }
    return session_id


def get_search_session(session_id: str) -> dict[str, Any] | None:
    _cleanup()
    item = search_sessions.get(session_id)
    return None if item is None else item["value"]


def _search_deezer_sync(query: str) -> list[dict[str, Any]]:
    q = normalize_query(query)
    if not q:
        return []

    response = http.get(DEEZER_SEARCH_URL, params={"q": q, "limit": SEARCH_MAX_RESULTS}, timeout=12)
    if response.status_code != 200:
        logger.warning("Deezer status=%s query=%r", response.status_code, q)
        return []

    payload = response.json()
    tracks = payload.get("data", [])
    tracks = sorted(tracks, key=lambda t: score_track(t, q), reverse=True)[:SEARCH_MAX_RESULTS]

    parsed: list[dict[str, Any]] = []
    for track in tracks:
        artist = track.get("artist") or {}
        album = track.get("album") or {}
        parsed.append(
            {
                "title": track.get("title", "Sem título"),
                "artist": artist.get("name", "Artista desconhecido"),
                "album": album.get("title", "Single"),
                "thumb": album.get("cover_big") or album.get("cover_medium") or "",
                "deezer_url": track.get("link") or "",
                "preview": track.get("preview") or "",
                "lyrics_url": "",
            }
        )
    return parsed


def _find_lyrics_url_sync(title: str, artist: str, genius_api_key: str | None) -> str | None:
    if not genius_api_key:
        return None

    response = http.get(
        GENIUS_SEARCH_URL,
        headers={"Authorization": f"Bearer {genius_api_key}"},
        params={"q": f"{title} {artist}"},
        timeout=12,
    )
    if response.status_code != 200:
def make_key(data: dict[str, Any]) -> str:
    raw = f"{data.get('title', '')}|{data.get('artist', '')}|{data.get('url', '')}|{data.get('deezer_url', '')}"
    return hashlib.md5(raw.encode()).hexdigest()[:8]


def prune_expired_state() -> None:
    current = now_ts()
    for state in (cache, music_cache, search_sessions):
        expired = [key for key, value in state.items() if value.get("expires_at", current + 1) <= current]
        for key in expired:
            state.pop(key, None)


def store_music(data: dict[str, Any]) -> str:
    prune_expired_state()
    key = make_key(data)
    music_cache[key] = {
        "value": data,
        "expires_at": now_ts() + MUSIC_CACHE_TTL,
    }
    return key


def get_music(key: str) -> dict[str, Any] | None:
    prune_expired_state()
    entry = music_cache.get(key)
    return None if entry is None else entry["value"]


def set_cache(key: str, value: list[dict[str, Any]]) -> None:
    cache[key] = {
        "value": value,
        "expires_at": now_ts() + SEARCH_CACHE_TTL,
    }


def get_cache(key: str) -> list[dict[str, Any]] | None:
    prune_expired_state()
    entry = cache.get(key)
    return None if entry is None else entry["value"]


def create_search_session(query: str, items: list[dict[str, Any]]) -> str:
    prune_expired_state()
    session_key = hashlib.md5(f"{query}|{now_ts()}".encode()).hexdigest()[:10]
    search_sessions[session_key] = {
        "value": {
            "query": query,
            "items": items,
        },
        "expires_at": now_ts() + SEARCH_CACHE_TTL,
    }
    return session_key


def get_search_session(session_key: str) -> dict[str, Any] | None:
    prune_expired_state()
    entry = search_sessions.get(session_key)
    return None if entry is None else entry["value"]


def html_text(value: str) -> str:
    return html.escape(value or "")


def msg_musica(music: dict[str, Any]) -> str:
    parts = [
        f"♬ <b>{html_text(music['title'])}</b>",
        f"★ <i>{html_text(music['artist'])}</i>",
        f"▶ <i>{html_text(music['album'])}</i>",
    ]
    if music.get("preview"):
        parts.append("🎧 Preview disponível no botão abaixo")
    if music.get("url") and "genius.com" in music["url"]:
        parts.append("📝 Letra disponível")
    return "\n".join(parts)


def msg_letra(music: dict[str, Any], refrao: str, fonte: str) -> str:
    return (
        f"♬ <b>{html_text(music['title'])}</b>\n"
        f"★ <i>{html_text(music['artist'])}</i>\n"
        f"▶ <i>{html_text(music['album'])}</i>\n\n"
        f"<b>♪ ♫ Refrão</b>\n"
        f"<i>Fonte: {html_text(fonte)}</i>\n\n"
        f"<blockquote>{html_text(refrao)}</blockquote>"
    )


def action_keyboard(music: dict[str, Any], key: str) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton("✓ Refrão", callback_data=f"y|{key}"),
        InlineKeyboardButton("✕ Só música", callback_data=f"n|{key}"),
    ]]

    links = []
    if music.get("preview"):
        links.append(InlineKeyboardButton("🎧 Preview", url=music["preview"]))
    if music.get("deezer_url"):
        links.append(InlineKeyboardButton("🔗 Deezer", url=music["deezer_url"]))
    if music.get("url") and "genius.com" in music["url"]:
        links.append(InlineKeyboardButton("📝 Letra", url=music["url"]))
    if links:
        rows.append(links[:3])

    return InlineKeyboardMarkup(rows)


def build_search_keyboard(session_key: str, items: list[dict[str, Any]], page: int) -> InlineKeyboardMarkup:
    start = page * SEARCH_PAGE_SIZE
    end = start + SEARCH_PAGE_SIZE
    chunk = items[start:end]
    keyboard = []
    for item in chunk:
        key = store_music(item)
        keyboard.append([
            InlineKeyboardButton(
                f"{item['title']} — {item['artist']}",
                callback_data=f"s|{key}",
            )
        ])

    controls = []
    if page > 0:
        controls.append(InlineKeyboardButton("◀️ Voltar", callback_data=f"p|{session_key}|{page - 1}"))
    if end < len(items):
        controls.append(InlineKeyboardButton("Load more ▶️", callback_data=f"p|{session_key}|{page + 1}"))
    if controls:
        keyboard.append(controls)

    return InlineKeyboardMarkup(keyboard)


# =========================
# SEARCH / LYRICS
# =========================

def _search_deezer_sync(query: str) -> list[dict[str, Any]]:
    normalized = normalize_query(query)
    if not normalized:
        return []

    response = session.get(
        DEEZER_SEARCH_URL,
        params={"q": normalized, "limit": SEARCH_MAX_RESULTS},
        timeout=10,
    )
    if response.status_code != 200:
        logger.warning("Deezer retornou status %s para query %r", response.status_code, normalized)
        return []

    payload = response.json()
    items = payload.get("data", [])
    sorted_items = sorted(items, key=lambda item: score_track(item, normalized), reverse=True)

    output = []
    for item in sorted_items[:SEARCH_MAX_RESULTS]:
        artist = item.get("artist") or {}
        album = item.get("album") or {}
        output.append(
            {
                "title": item.get("title", "Sem título"),
                "artist": artist.get("name", "Artista desconhecido"),
                "album": album.get("title", "Single"),
                "thumb": album.get("cover_big") or album.get("cover_medium") or "",
                "deezer_url": item.get("link") or "",
                "preview": item.get("preview") or "",
                "url": "",
            }
        )
    return output


def buscar_varias_musicas(query: str) -> list[dict[str, Any]]:
    normalized = normalize_query(query)
    if not normalized:
        return []

    cached = get_cache(normalized.casefold())
    if cached is not None:
        return cached

    try:
        tracks = _search_deezer_sync(normalized)
        enriched = []
        for track in tracks:
            genius_url = find_genius_url(track["title"], track["artist"])
            enriched.append({**track, "url": genius_url or track["deezer_url"]})

        set_cache(normalized.casefold(), enriched)
        return enriched
    except Exception:
        logger.exception("Erro buscando músicas para %r", normalized)
        return []


def find_genius_url(title: str, artist: str) -> str | None:
    if not settings.genius_api_key:
        return None

    response = session.get(
        GENIUS_SEARCH_URL,
        headers={"Authorization": f"Bearer {settings.genius_api_key}"},
        params={"q": f"{title} {artist}"},
        timeout=10,
    )
    if response.status_code != 200:
        logger.warning("Genius retornou status %s para %s - %s", response.status_code, artist, title)
        return None

    hits = response.json().get("response", {}).get("hits", [])
    if not hits:
        return None

    url = (hits[0].get("result") or {}).get("url")
    return url


def search_tracks(query: str, genius_api_key: str | None) -> list[dict[str, Any]]:
    q = normalize_query(query)
    if not q:
        return []

    _cleanup()
    cached = search_cache.get(q.casefold())
    if cached:
        return cached["value"]

    base_tracks = _search_deezer_sync(q)
    enriched = []
    for track in base_tracks:
        lyrics_url = _find_lyrics_url_sync(track["title"], track["artist"], genius_api_key)
        enriched.append({**track, "lyrics_url": lyrics_url or ""})

    search_cache[q.casefold()] = {
        "value": enriched,
        "expires_at": now() + CACHE_TTL_SECONDS,
    }
    return enriched


def _fetch_lyrics_sync(track: dict[str, Any]) -> tuple[str | None, str]:
    # 1) Genius page scrape
    lyrics_url = track.get("lyrics_url") or ""
    if "genius.com" in lyrics_url:
        try:
            response = http.get(lyrics_url, timeout=12)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "lxml")
                blocks = soup.find_all("div", {"data-lyrics-container": "true"})
                text = "\n".join([b.get_text("\n") for b in blocks]).strip()
                if text:
                    return text, "Genius"
        except Exception:
            logger.exception("Falha ao raspar letra do Genius")

    # 2) lyrics.ovh fallback
    try:
        artist = requests.utils.quote(track.get("artist", ""))
        title = requests.utils.quote(track.get("title", ""))
        if artist and title:
            response = http.get(f"https://api.lyrics.ovh/v1/{artist}/{title}", timeout=12)
            if response.status_code == 200:
                text = (response.json().get("lyrics") or "").strip()
                if text:
                    return text, "lyrics.ovh"
    except Exception:
        logger.exception("Falha no fallback lyrics.ovh")

    return None, "indisponível"


def _heuristic_chorus(lyrics: str) -> str:
    parts = [p.strip() for p in re.split(r"\n\s*\n", lyrics or "") if p.strip()]
    if not parts:
        return "Letra não encontrada."

    freq: dict[str, int] = {}
    for part in parts:
        freq[part] = freq.get(part, 0) + 1

    best = max(freq.items(), key=lambda x: (x[1], len(x[0])))[0]
    lines = [ln.strip() for ln in best.splitlines() if ln.strip()]
    return "\n".join(lines[:8]) if lines else "Letra não encontrada."


def _extract_chorus_sync(lyrics: str | None, openai_api_key: str | None) -> tuple[str, str]:
    if not lyrics:
        return "Letra não encontrada.", "fallback local"

    if not openai_api_key:
        return _heuristic_chorus(lyrics), "fallback local"

    try:
        client = OpenAI(api_key=openai_api_key)
        resp = client.chat.completions.create(
    best = hits[0].get("result", {})
    url = best.get("url")
    if url:
        return url

    path = best.get("path")
    return f"{GENIUS_BASE_URL}{path}" if path else None


def pegar_letra_genius(url: str | None) -> str | None:
    if not url or "genius.com" not in url:
        return None

    response = session.get(url, timeout=10)
    if response.status_code != 200:
        logger.warning("Falha ao abrir página da letra: status=%s url=%s", response.status_code, url)
        return None

    soup = BeautifulSoup(response.text, "lxml")
    containers = soup.find_all("div", {"data-lyrics-container": "true"})
    letras = [container.get_text("\n") for container in containers]
    texto = "\n".join(letras).strip()
    return texto or None


def pegar_letra_lyrics_ovh(title: str, artist: str) -> str | None:
    if not title or not artist:
        return None

    response = session.get(
        LYRICS_OVH_URL.format(artist=requests.utils.quote(artist), title=requests.utils.quote(title)),
        timeout=10,
    )
    if response.status_code != 200:
        return None

    lyrics = response.json().get("lyrics")
    if not lyrics:
        return None
    return lyrics.strip() or None


def pegar_letra(url: str | None) -> str | None:
    return pegar_letra_genius(url)


def heuristic_refrao(letra: str) -> str:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", letra or "") if block.strip()]
    if not blocks:
        return "Letra não encontrada."

    counted: dict[str, int] = {}
    for block in blocks:
        counted[block] = counted.get(block, 0) + 1

    best_block = max(counted.items(), key=lambda item: (item[1], len(item[0])))[0]
    lines = [line.strip() for line in best_block.splitlines() if line.strip()]
    return "\n".join(lines[:8]) or "Letra não encontrada."


def extrair_refrao(letra: str | None) -> tuple[str, str]:
    if not letra:
        return "Letra não encontrada.", "fallback local"

    if client is None:
        return heuristic_refrao(letra), "fallback local"

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.2,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extraia somente o refrão principal da letra. "
                        "Sem comentários, sem markdown, máximo 8 linhas."
                    ),
                },
                {"role": "user", "content": lyrics},
            ],
        )
        content = (resp.choices[0].message.content or "").strip()
        lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
        if lines:
            return "\n".join(lines[:8]), "OpenAI"
    except Exception:
        logger.exception("Falha ao extrair refrão com OpenAI")

    return _heuristic_chorus(lyrics), "fallback local"


# =========================
# Telegram UI / handlers
# =========================
def build_results_keyboard(session_id: str, tracks: list[dict[str, Any]], page: int) -> InlineKeyboardMarkup:
    start = page * SEARCH_PAGE_SIZE
    end = start + SEARCH_PAGE_SIZE
    chunk = tracks[start:end]

    rows = []
    for track in chunk:
        key = save_track(track)
        rows.append([InlineKeyboardButton(f"{track['title']} — {track['artist']}", callback_data=f"s|{key}")])

    controls = []
    if page > 0:
        controls.append(InlineKeyboardButton("◀️ Voltar", callback_data=f"p|{session_id}|{page - 1}"))
    if end < len(tracks):
        controls.append(InlineKeyboardButton("Load more ▶️", callback_data=f"p|{session_id}|{page + 1}"))
    if controls:
        rows.append(controls)

    return InlineKeyboardMarkup(rows)


def build_track_actions(track: dict[str, Any], key: str) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton("✓ Refrão", callback_data=f"y|{key}"),
        InlineKeyboardButton("✕ Só música", callback_data=f"n|{key}"),
    ]]

    links = []
    if track.get("preview"):
        links.append(InlineKeyboardButton("🎧 Preview", url=track["preview"]))
    if track.get("deezer_url"):
        links.append(InlineKeyboardButton("🔗 Deezer", url=track["deezer_url"]))
    if track.get("lyrics_url"):
        links.append(InlineKeyboardButton("📝 Letra", url=track["lyrics_url"]))
    if links:
        rows.append(links[:3])

    return InlineKeyboardMarkup(rows)


async def _send_track_card(message: Any, track: dict[str, Any], key: str | None) -> None:
    markup = build_track_actions(track, key) if key else None
    caption = format_track_message(track)

    if track.get("thumb"):
        await message.reply_photo(track["thumb"], caption=caption, parse_mode=ParseMode.HTML, reply_markup=markup)
    else:
        await message.reply_text(caption, parse_mode=ParseMode.HTML, reply_markup=markup)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not update.message:
        return
    await update.message.reply_text(
        "Oi! Use /music <nome da música>.\n"
        "Exemplo: /music Daft Punk One More Time"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not update.message:
        return
    await update.message.reply_text(
        "Comandos:\n"
        "/start\n"
        "/help\n"
        "/music <termo>\n\n"
        "Fluxo: buscar -> escolher -> só música ou refrão."
    )


async def cmd_music(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    if not context.args:
        await update.message.reply_text("Digite o nome da música. Ex.: /music Believer")
        return

    query = " ".join(context.args)
    settings: Settings = context.application.bot_data["settings"]

    tracks = await asyncio.to_thread(search_tracks, query, settings.genius_api_key)
    if not tracks:
        await update.message.reply_text("❌ Nenhuma música encontrada")
        return

    session_id = save_search_session(query, tracks)
    keyboard = build_results_keyboard(session_id, tracks, page=0)
    await update.message.reply_text(f"🎵 Resultados para: {query}", reply_markup=keyboard)


async def cb_paginate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not update.callback_query or not update.callback_query.data or not update.callback_query.message:
        return

    q = update.callback_query
    await q.answer()

    _, session_id, page_raw = q.data.split("|", 2)
    session = get_search_session(session_id)
    if not session:
        await q.message.reply_text("❌ Resultados expiraram. Use /music novamente.")
        return

    page = max(0, int(page_raw))
    keyboard = build_results_keyboard(session_id, session["tracks"], page)
    await q.edit_message_text(f"🎵 Resultados para: {session['query']}", reply_markup=keyboard)


async def cb_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not update.callback_query or not update.callback_query.data or not update.callback_query.message:
        return

    q = update.callback_query
    await q.answer()

    _, key = q.data.split("|", 1)
    track = get_track(key)
    if not track:
        await q.message.reply_text("❌ Seleção expirada. Use /music novamente.")
        return

    await _send_track_card(q.message, track, key)


async def cb_final(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.callback_query or not update.callback_query.data or not update.callback_query.message:
        return

    q = update.callback_query
    await q.answer()

    action, key = q.data.split("|", 1)
    track = get_track(key)
    if not track:
        await q.message.reply_text("❌ Seleção expirada. Use /music novamente.")
        return

    if action == "n":
        await _send_track_card(q.message, track, None)
        return

    await q.message.reply_text("🎧 Buscando letra e refrão...")
    settings: Settings = context.application.bot_data["settings"]

    lyrics, lyrics_source = await asyncio.to_thread(_fetch_lyrics_sync, track)
    chorus, chorus_source = await asyncio.to_thread(_extract_chorus_sync, lyrics, settings.openai_api_key)

    if not lyrics:
        await q.message.reply_text(
            format_track_message(track) + "\n\n⚠️ Não consegui encontrar a letra agora.",
            parse_mode=ParseMode.HTML,
            reply_markup=build_track_actions(track, key),
        )
        return

    payload = (
        f"{format_track_message(track)}\n\n"
        f"<b>♪ ♫ Refrão</b>\n"
        f"<i>Fonte: {escape_html(lyrics_source)} + {escape_html(chorus_source)}</i>\n\n"
        f"<blockquote>{escape_html(chorus)}</blockquote>"
    )
    await q.message.reply_text(payload, parse_mode=ParseMode.HTML, reply_markup=build_track_actions(track, key))


async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not update.inline_query:
        return

    query = normalize_query(update.inline_query.query)
    if not query:
        return

    settings: Settings = update.get_bot()._application.bot_data["settings"]
    tracks = await asyncio.to_thread(search_tracks, query, settings.genius_api_key)

    results = []
    for track in tracks[:10]:
        text = format_track_message(track)
        results.append(
            InlineQueryResultArticle(
                id=uuid.uuid4().hex,
                title=f"{track['title']} — {track['artist']}",
                description=track["album"],
                thumbnail_url=track.get("thumb") or None,
                input_message_content=InputTextMessageContent(text, parse_mode=ParseMode.HTML),
                        "Você é um assistente especializado em letras de música. "
                        "Extraia somente o trecho que melhor representa o refrão. "
                        "Mantenha o idioma original, preserve as quebras de linha, "
                        "não adicione comentários, não use aspas, e limite a no máximo 8 linhas."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Letra completa:\n\n{letra}",
                },
            ],
        )
        content = response.choices[0].message.content or ""
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if lines:
            return "\n".join(lines[:8]), "OpenAI"
    except Exception:
        logger.exception("Erro ao gerar refrão com OpenAI")

    return heuristic_refrao(letra), "fallback local"


def obter_letra_e_fonte(music: dict[str, Any]) -> tuple[str | None, str]:
    letra = pegar_letra_genius(music.get("url"))
    if letra:
        return letra, "Genius"

    letra = pegar_letra_lyrics_ovh(music.get("title", ""), music.get("artist", ""))
    if letra:
        return letra, "lyrics.ovh"

    return None, "indisponível"


# =========================
# TELEGRAM HANDLERS
# =========================

async def send_music_card(target_message: Any, music: dict[str, Any], key: str | None = None) -> None:
    reply_markup = action_keyboard(music, key) if key else None
    if music.get("thumb"):
        await target_message.reply_photo(
            photo=music["thumb"],
            caption=msg_musica(music),
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )
        return

    await target_message.reply_text(
        msg_musica(music),
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message is None:
        return

    await update.message.reply_text(
        "Oi! Envie /music nome da música\n"
        "Exemplo: /music Daft Punk One More Time\n\n"
        "Eu posso buscar a faixa, mostrar capa, preview e tentar extrair o refrão."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message is None:
        return

    await update.message.reply_text(
        "Comandos disponíveis:\n"
        "/start - mensagem inicial\n"
        "/help - ajuda\n"
        "/music <termo> - buscar música\n\n"
        "Depois da busca você pode paginar resultados, abrir preview, Deezer e letra, "
        "ou pedir o refrão automaticamente."
    )


async def music(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    if not context.args:
        await update.message.reply_text("Digite o nome da música. Ex.: /music Cazuza Exagerado")
        return

    query = " ".join(context.args)
    musicas = await asyncio.to_thread(buscar_varias_musicas, query)

    if not musicas:
        await update.message.reply_text("❌ Nenhuma música encontrada")
        return

    session_key = create_search_session(query, musicas)
    keyboard = build_search_keyboard(session_key, musicas, page=0)
    await update.message.reply_text(
        f"🎵 Resultados para: {query}\nEscolha uma música abaixo:",
        reply_markup=keyboard,
    )


async def paginate_results(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    query = update.callback_query
    if query is None or query.message is None or query.data is None:
        return

    await query.answer()
    _, session_key, page_str = query.data.split("|", 2)
    session_data = get_search_session(session_key)
    if not session_data:
        await query.message.reply_text("❌ Resultados expiraram. Faça a busca novamente com /music.")
        return

    page = max(0, int(page_str))
    keyboard = build_search_keyboard(session_key, session_data["items"], page)
    await query.edit_message_text(
        text=f"🎵 Resultados para: {session_data['query']}\nEscolha uma música abaixo:",
        reply_markup=keyboard,
    )


async def selecionar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    query = update.callback_query
    if query is None or query.message is None or query.data is None:
        return

    await query.answer()
    _, key = query.data.split("|", 1)
    music_item = get_music(key)

    if not music_item:
        await query.message.reply_text("❌ Dados expirados, use /music novamente")
        return

    await send_music_card(query.message, music_item, key)


async def final(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    query = update.callback_query
    if query is None or query.message is None or query.data is None:
        return

    await query.answer()
    action, key = query.data.split("|", 1)
    music_item = get_music(key)

    if not music_item:
        await query.message.reply_text("❌ Dados expirados, use /music novamente")
        return

    if action == "n":
        await send_music_card(query.message, music_item)
        return

    await query.message.reply_text("🎧 Buscando letra e preparando o melhor trecho...")
    letra, fonte_letra = await asyncio.to_thread(obter_letra_e_fonte, music_item)
    refrao, fonte_refrao = await asyncio.to_thread(extrair_refrao, letra)

    if not letra:
        await query.message.reply_text(
            msg_musica(music_item) + "\n\n⚠️ Não consegui encontrar a letra dessa música agora.",
            parse_mode=ParseMode.HTML,
            reply_markup=action_keyboard(music_item, key),
        )
        return

    await query.message.reply_text(
        msg_letra(music_item, refrao, f"{fonte_letra} + {fonte_refrao}"),
        parse_mode=ParseMode.HTML,
        reply_markup=action_keyboard(music_item, key),
    )


async def inline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.inline_query is None:
        return

    query = update.inline_query.query
    if not query:
        return

    musicas = await asyncio.to_thread(buscar_varias_musicas, query)
    results = []
    for item in musicas:
        body = msg_musica(item)
        if item.get("url") and "genius.com" in item["url"]:
            body += f"\n\n📝 <a href=\"{html_text(item['url'])}\">Abrir letra</a>"
        results.append(
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title=f"{item['title']} — {item['artist']}",
                description=item["album"],
                thumbnail_url=item["thumb"] or None,
                input_message_content=InputTextMessageContent(body, parse_mode=ParseMode.HTML),
            )
        )

    await update.inline_query.answer(results, cache_time=1)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled update error. update=%s", update, exc_info=context.error)


async def on_post_init(app: Application) -> None:
    logger.exception("Erro no update %s", update, exc_info=context.error)


async def post_init(app: Application) -> None:
    me = await app.bot.get_me()
    logger.info("Bot conectado como @%s (%s)", me.username, me.id)


# =========================
# App bootstrap
# =========================
def build_application(settings: Settings) -> Application:
    app = Application.builder().token(settings.telegram_token).post_init(on_post_init).build()
    app.bot_data["settings"] = settings

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("music", cmd_music))

    app.add_handler(CallbackQueryHandler(cb_paginate, pattern=r"^p\|"))
    app.add_handler(CallbackQueryHandler(cb_select, pattern=r"^s\|"))
    app.add_handler(CallbackQueryHandler(cb_final, pattern=r"^(y|n)\|"))

    app.add_handler(InlineQueryHandler(inline_query))

def build_application(token: str | None = None) -> Application:
    resolved_token = token or get_settings().token
    if not resolved_token:
        raise ValueError("Configure TELEGRAM_TOKEN nas variáveis de ambiente")

    app = Application.builder().token(resolved_token).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("music", music))
    app.add_handler(CallbackQueryHandler(paginate_results, pattern=r"^p\|"))
    app.add_handler(CallbackQueryHandler(selecionar, pattern=r"^s\|"))
    app.add_handler(CallbackQueryHandler(final, pattern=r"^(y|n)\|"))
    app.add_handler(InlineQueryHandler(inline))
    app.add_error_handler(on_error)
    return app


def run(settings: Settings) -> None:
    app = build_application(settings)

    if settings.run_mode == "webhook":
        if not settings.webhook_url:
            raise ValueError("RUN_MODE=webhook exige WEBHOOK_URL")

        target = f"{settings.webhook_url}/{settings.telegram_token}"
        logger.info("Iniciando em modo WEBHOOK na porta %s", settings.port)
        logger.info("Webhook público: %s", target)

        app.run_webhook(
            listen="0.0.0.0",
            port=settings.port,
            url_path=settings.telegram_token,
            webhook_url=target,
            secret_token=settings.webhook_secret,
def run_bot(app: Application, current: Settings) -> None:
    token = current.token
    if not token:
        raise ValueError("Configure TELEGRAM_TOKEN nas variáveis de ambiente")

    if current.webhook_url:
        webhook_target_url = build_webhook_target_url(current.webhook_url, token)
        logger.info("Iniciando em modo WEBHOOK — porta %s", current.port)
        logger.info("Webhook público configurado para %s", webhook_target_url)
        app.run_webhook(
            listen="0.0.0.0",
            port=current.port,
            url_path=token,
            webhook_url=webhook_target_url,
            secret_token=current.webhook_secret,
            drop_pending_updates=True,
        )
        return

    if running_on_railway():
        logger.warning(
            "Railway detectado sem WEBHOOK_URL público. Tentando polling, mas isso pode causar conflito 409. "
            "Configure WEBHOOK_URL ou exponha RAILWAY_PUBLIC_DOMAIN/RAILWAY_STATIC_URL."
        )

    logger.info("Iniciando em modo POLLING")
    try:
        app.run_polling(drop_pending_updates=True)
    except Conflict:
        logger.exception(
            "409 Conflict em getUpdates: há outra instância em polling para este bot. "
            "Use webhook na Railway com WEBHOOK_URL público."
            "Conflito no getUpdates: outra instância do bot está usando polling. "
            "Na Railway, prefira webhook com WEBHOOK_URL=https://pidrofmbot-v2-production.up.railway.app"
        )
        raise


def main() -> None:
    settings = load_settings()
    run(settings)

def main() -> None:
    current = get_settings()
    token = current.token
    if not token:
        raise ValueError("Configure TELEGRAM_TOKEN nas variáveis de ambiente")

    app = build_application(token)
    run_bot(app, current)


if __name__ == "__main__":
    main()
