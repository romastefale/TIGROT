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


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


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
    logger.exception("Erro no update %s", update, exc_info=context.error)


async def post_init(app: Application) -> None:
    me = await app.bot.get_me()
    logger.info("Bot conectado como @%s (%s)", me.username, me.id)



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
            "Conflito no getUpdates: outra instância do bot está usando polling. "
            "Na Railway, prefira webhook com WEBHOOK_URL=https://pidrofmbot-v2-production.up.railway.app"
        )
        raise



def main() -> None:
    current = get_settings()
    token = current.token
    if not token:
        raise ValueError("Configure TELEGRAM_TOKEN nas variáveis de ambiente")

    app = build_application(token)
    run_bot(app, current)


if __name__ == "__main__":
    main()
