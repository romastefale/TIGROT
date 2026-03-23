import os
import uuid
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
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    InlineQueryHandler,
    ContextTypes
)

from openai import OpenAI

# LOG
logging.basicConfig(level=logging.INFO)

# ENV
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GENIUS_API_KEY = os.getenv("GENIUS_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

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
                "album": s.get("album", {}).get("name", "Single"),
                "url": s["url"],
                "thumb": s.get("song_art_image_thumbnail_url") or ""
            })
        return out
    except:
        return []

# =========================
# SCRAPING (CORRIGIDO)
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
    except:
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
    except:
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
# CHAT
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
        kb.append([InlineKeyboardButton(
            f"{m['title']} — {m['artist']}",
            callback_data=f"s|{m['url']}|{m['title']}|{m['artist']}|{m['album']}"
        )])

    await update.message.reply_text("🎵 Escolha:", reply_markup=InlineKeyboardMarkup(kb))

async def selecionar(update, context):
    q = update.callback_query
    await q.answer()

    _, url, t, a, al = q.data.split("|")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✓ Yes", callback_data=f"y|{url}|{t}|{a}|{al}"),
        InlineKeyboardButton("✕ No", callback_data=f"n|{url}|{t}|{a}|{al}")
    ]])

    await q.message.reply_text("♪ Lyrics?", reply_markup=kb)

async def final(update, context):
    q = update.callback_query
    await q.answer()

    tipo, url, t, a, al = q.data.split("|")

    m = {"title": t, "artist": a, "album": al, "url": url}

    if tipo == "n":
        await q.message.reply_text(msg_musica(m), parse_mode="Markdown")
        return

    await q.message.reply_text("🎧 Buscando...")

    letra = pegar_letra(url)
    refrao = extrair_refrao(letra)

    await q.message.reply_text(msg_letra(m, refrao), parse_mode="Markdown")

# =========================
# INLINE
# =========================

async def inline(update, context):
    q = update.inline_query.query
    if not q:
        return

    musicas = buscar_varias_musicas(q)
    res = []

    for m in musicas:
        res.append(InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title=f"{m['title']} — {m['artist']}",
            description=m['album'],
            thumbnail_url=m["thumb"],
            input_message_content=InputTextMessageContent(
                msg_musica(m), parse_mode="Markdown"
            )
        ))

        res.append(InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title=f"{m['title']} — {m['artist']} ♪ ♫ Lyrics",
            description="Com refrão",
            thumbnail_url=m["thumb"],
            input_message_content=InputTextMessageContent(
                msg_letra(m, "Carregando..."), parse_mode="Markdown"
            )
        ))

    await update.inline_query.answer(res, cache_time=1)

# =========================
# MAIN
# =========================

def main():
    print("BOT ONLINE")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("music", music))
    app.add_handler(CallbackQueryHandler(selecionar, pattern="^s\\|"))
    app.add_handler(CallbackQueryHandler(final, pattern="^(y|n)\\|"))
    app.add_handler(InlineQueryHandler(inline))

    app.run_polling()

if __name__ == "__main__":
    main()