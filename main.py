import os
import uuid
import hashlib
import requests
from bs4 import BeautifulSoup
import logging

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InlineQueryResultArticle,
    InputTextMessageContent
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    InlineQueryHandler,
    ContextTypes
)

from openai import OpenAI

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")
GENIUS_API_KEY = os.getenv("GENIUS_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", TOKEN.replace(":", "")[:20] if TOKEN else None)

try:
    PORT = int(os.getenv("PORT", 8443))
except ValueError:
    logger.warning("PORT inválido, usando 8443")
    PORT = 8443

if not TOKEN:
    raise ValueError("Configure TELEGRAM_TOKEN nas variáveis de ambiente")

client = OpenAI(api_key=OPENAI_API_KEY)

music_cache: dict[str, dict] = {}

# =========================
# CACHE DE MÚSICAS
# =========================

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
        letras = [d.get_text("\n") for d in divs]
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
        await q.message.reply_text("❌ Dados expirados, use /music novamente")
        return

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✓ Yes", callback_data=f"y|{key}"),
        InlineKeyboardButton("✕ No", callback_data=f"n|{key}")
    ]])

    await q.message.reply_text("♪ Lyrics?", reply_markup=kb)


async def final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    tipo, key = q.data.split("|", 1)
    m = get_music(key)

    if not m:
        await q.message.reply_text("❌ Dados expirados, use /music novamente")
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
# MAIN
# =========================

def main():
    app = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("music", music))
    app.add_handler(CallbackQueryHandler(selecionar, pattern=r"^s\|"))
    app.add_handler(CallbackQueryHandler(final, pattern=r"^(y|n)\|"))
    app.add_handler(InlineQueryHandler(inline))

    if WEBHOOK_URL:
        logger.info(f"Iniciando em modo WEBHOOK — porta {PORT}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TOKEN}",
            secret_token=WEBHOOK_SECRET,
            drop_pending_updates=True,
        )
    else:
        logger.info("Iniciando em modo POLLING")
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
