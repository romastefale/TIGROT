import os
import uuid
import asyncio
import hashlib
import requests
from bs4 import BeautifulSoup
import logging
from aiohttp import web

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InlineQueryResultArticle,
    InputTextMessageContent
)

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    InlineQueryHandler,
    ContextTypes
)
from telegram.error import Conflict, NetworkError

from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GENIUS_API_KEY = os.getenv("GENIUS_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", "8080"))

client = OpenAI(api_key=OPENAI_API_KEY)

music_cache: dict[str, dict] = {}

def make_key(data: dict) -> str:
    raw = f"{data['title']}|{data['artist']}|{data['url']}"
    return hashlib.md5(raw.encode()).hexdigest()[:8]

def store_music(data: dict) -> str:
    key = make_key(data)
    music_cache[key] = data
    return key

def get_music(key: str) -> dict | None:
    return music_cache.get(key)

# =========================
# GENIUS
# =========================

def buscar_varias_musicas(query):
    try:
        r = requests.get(
            "https://api.genius.com/search",
            headers={"Authorization": f"Bearer {GENIUS_API_KEY}"},
            params={"q": query},
            timeout=10
        )
        data = r.json()
        hits = data.get("response", {}).get("hits", [])

        out = []
        for h in hits[:5]:
            s = h["result"]
            out.append({
                "title": s["title"],
                "artist": s["primary_artist"]["name"],
                "album": (s.get("album") or {}).get("name", "Single"),
                "url": s["url"],
                "thumb": s.get("song_art_image_thumbnail_url") or ""
            })
        return out
    except Exception as e:
        logger.error(f"Genius error: {e}")
        return []

# =========================
# SCRAPING
# =========================

def pegar_letra(url):
    try:
        r = requests.get(url, timeout=10)
        soup = BeautifulSoup(r.text, "lxml")
        divs = soup.find_all("div", {"data-lyrics-container": "true"})
        letras = []
        for d in divs:
            letras.append(d.get_text("\n"))
        texto = "\n".join(letras).strip()
        return texto if texto else None
    except Exception as e:
        logger.error(f"Scraping error: {e}")
        return None

# =========================
# OPENAI
# =========================

def extrair_refrao(letra):
    if not letra:
        return "Letra não encontrada."
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": "Extraia apenas o refrão (máx 8 linhas):\n\n" + letra
            }]
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        return "Erro ao gerar refrão."

# =========================
# FORMAT
# =========================

def formatar_letra(refrao):
    refrao = (refrao or "").replace("```", "'''")
    linhas = [l.strip() for l in refrao.split("\n") if l.strip()]
    return "```\n" + "\n".join(linhas) + "\n```"

def msg_musica(m):
    return f"♬ *{m['title']}*\n▶ _{m['album']}_\n★ _{m['artist']}_"

def msg_letra(m, refrao):
    return (
        f"♬ *{m['title']}*\n"
        f"▶ _{m['album']}_\n"
        f"★ _{m['artist']}_\n\n"
        f"_♪ ♫ Lyrics:_\n\n"
        f"{formatar_letra(refrao)}"
    )

# =========================
# HANDLERS
# =========================

async def music(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Digite o nome da música")
        return

    query = " ".join(context.args)
    musicas = buscar_varias_musicas(query)

    if not musicas:
        await update.message.reply_text("❌ Nenhuma música encontrada")
        return

    kb = []
    for m in musicas:
        key = store_music(m)
        kb.append([InlineKeyboardButton(
            f"{m['title']} — {m['artist']}",
            callback_data=f"s|{key}"
        )])

    await update.message.reply_text("🎵 Escolha:", reply_markup=InlineKeyboardMarkup(kb))

async def selecionar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    _, key = q.data.split("|", 1)
    m = get_music(key)

    if not m:
        await q.message.reply_text("❌ Dados expirados, tente novamente com /music")
        return

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✓ Yes", callback_data=f"y|{key}"),
        InlineKeyboardButton("✕ No", callback_data=f"n|{key}")
    ]])

    await q.message.reply_text("♪ Lyrics?", reply_markup=kb)

async def final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    parts = q.data.split("|", 1)
    tipo, key = parts[0], parts[1]

    m = get_music(key)
    if not m:
        await q.message.reply_text("❌ Dados expirados, tente novamente com /music")
        return

    if tipo == "n":
        await q.message.reply_text(msg_musica(m), parse_mode="Markdown")
        return

    await q.message.reply_text("🎧 Buscando...")

    letra = pegar_letra(m["url"])
    refrao = extrair_refrao(letra)

    await q.message.reply_text(msg_letra(m, refrao), parse_mode="Markdown")

async def inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.inline_query.query
    if not q:
        return

    musicas = buscar_varias_musicas(q)
    res = []

    for m in musicas:
        res.append(InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title=f"{m['title']} — {m['artist']}",
            description=m["album"],
            thumbnail_url=m["thumb"] or None,
            input_message_content=InputTextMessageContent(
                msg_musica(m), parse_mode="Markdown"
            )
        ))

    await update.inline_query.answer(res, cache_time=1)

# =========================
# ERROR HANDLER
# =========================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, Conflict):
        logger.warning("Conflito detectado (outra instância rodando). Aguardando 5s...")
        await asyncio.sleep(5)
    elif isinstance(err, NetworkError):
        logger.warning(f"Erro de rede: {err}. Tentando novamente...")
    else:
        logger.error(f"Erro não tratado: {err}", exc_info=err)

# =========================
# MAIN
# =========================

def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN não definido")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("music", music))
    app.add_handler(CallbackQueryHandler(selecionar, pattern=r"^s\|"))
    app.add_handler(CallbackQueryHandler(final, pattern=r"^(y|n)\|"))
    app.add_handler(InlineQueryHandler(inline))
    app.add_error_handler(error_handler)

    if WEBHOOK_URL:
        logger.info(f"Iniciando em modo WEBHOOK na porta {PORT}")

        async def on_startup(aioapp: web.Application):
            await app.initialize()
            await app.bot.set_webhook(
                url=f"{WEBHOOK_URL.rstrip('/')}/webhook/{TELEGRAM_TOKEN}",
                allowed_updates=Update.ALL_TYPES
            )
            await app.start()

        async def on_shutdown(aioapp: web.Application):
            await app.stop()
            await app.shutdown()

        async def webhook_handler(request: web.Request):
            data = await request.json()
            update = Update.de_json(data, app.bot)
            await app.process_update(update)
            return web.Response(text="ok")

        aioapp = web.Application()
        aioapp.router.add_post(f"/webhook/{TELEGRAM_TOKEN}", webhook_handler)
        aioapp.on_startup.append(on_startup)
        aioapp.on_shutdown.append(on_shutdown)

        web.run_app(aioapp, host="0.0.0.0", port=PORT)
    else:
        logger.info("Iniciando em modo POLLING (sem WEBHOOK_URL definida)")
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query", "inline_query"],
        )

if __name__ == "__main__":
    main()