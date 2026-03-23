import os
import uuid
import requests
from bs4 import BeautifulSoup

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

# =========================
# ENV
# =========================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GENIUS_API_KEY = os.getenv("GENIUS_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN não definido")

client = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# GENIUS (ROBUSTO)
# =========================

def buscar_varias_musicas(query):
    try:
        url = "https://api.genius.com/search"
        headers = {"Authorization": f"Bearer {GENIUS_API_KEY}"}
        params = {"q": query}

        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()

        data = r.json()
        hits = data.get("response", {}).get("hits", [])

        resultados = []

        for hit in hits[:5]:
            song = hit["result"]

            resultados.append({
                "title": song.get("title", "Unknown"),
                "artist": song.get("primary_artist", {}).get("name", "Unknown"),
                "album": song.get("album", {}).get("name", "Single"),
                "url": song.get("url"),
                "thumb": song.get("song_art_image_thumbnail_url") or ""
            })

        return resultados

    except Exception as e:
        print("Erro Genius:", e)
        return []

# =========================
# SCRAPING (PROTEGIDO)
# =========================

def pegar_letra(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "lxml")
        divs = soup.find_all("div", {"data-lyrics-container": "true"})

        letra = []
        for d in divs:
            letra.append(d.get_text("\n"))

        texto = "\n".join(letra).strip()
        return texto if texto else None

    except Exception as e:
        print("Erro scraping:", e)
        return None

# =========================
# OPENAI (FALLBACK)
# =========================

def extrair_refrao(letra):
    if not letra:
        return "Letra não encontrada."

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": f"Extraia apenas o refrão principal da música abaixo (máx 8 linhas):\n\n{letra}"
            }],
            temperature=0.3
        )
        return resp.choices[0].message.content.strip()

    except Exception as e:
        print("Erro OpenAI:", e)
        return "Não foi possível extrair o refrão."

# =========================
# FORMAT (SEGURO)
# =========================

def formatar_letra_codigo(refrao):
    refrao = (refrao or "").replace("```", "'''")
    linhas = [l.strip() for l in refrao.split("\n") if l.strip()]

    if not linhas:
        linhas = ["Letra não disponível."]

    return "```\n" + "\n".join(linhas) + "\n```"

def montar_msg_musica(m):
    return (
        f"♬ *{m['title']}*\n"
        f"▶ _{m['album']}_\n"
        f"★ _{m['artist']}_"
    )

def montar_msg_com_letra(m, refrao):
    return (
        f"♬ *{m['title']}*\n"
        f"▶ _{m['album']}_\n"
        f"★ _{m['artist']}_\n\n"
        f"_♪ ♫ Lyrics:_\n\n"
        f"{formatar_letra_codigo(refrao)}"
    )

# =========================
# CHAT FLOW
# =========================

async def music(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.args:
            await update.message.reply_text("Digite o nome da música")
            return

        query = " ".join(context.args)
        musicas = buscar_varias_musicas(query)

        if not musicas:
            await update.message.reply_text("❌ Nenhuma música encontrada.")
            return

        keyboard = []
        for m in musicas:
            keyboard.append([
                InlineKeyboardButton(
                    f"{m['title']} — {m['artist']}",
                    callback_data=f"select|{m['url']}|{m['title']}|{m['artist']}|{m['album']}"
                )
            ])

        await update.message.reply_text(
            "🎵 Escolha a música:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        print("Erro /music:", e)
        await update.message.reply_text("Erro ao buscar música.")

async def selecionar_musica(update, context):
    try:
        q = update.callback_query
        await q.answer()

        _, url, title, artist, album = q.data.split("|")

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✓ Yes", callback_data=f"yes|{url}|{title}|{artist}|{album}"),
                InlineKeyboardButton("✕ No", callback_data=f"no|{url}|{title}|{artist}|{album}")
            ]
        ])

        await q.message.reply_text("♪ Lyrics?", reply_markup=keyboard)

    except Exception as e:
        print("Erro selecionar:", e)

async def resposta_final(update, context):
    try:
        q = update.callback_query
        await q.answer()

        tipo, url, title, artist, album = q.data.split("|")

        musica = {
            "title": title,
            "artist": artist,
            "album": album,
            "url": url
        }

        if tipo == "no":
            await q.message.reply_text(montar_msg_musica(musica), parse_mode="Markdown")
            return

        await q.message.reply_text("🎧 Buscando refrão...")

        letra = pegar_letra(url)
        refrao = extrair_refrao(letra)

        await q.message.reply_text(
            montar_msg_com_letra(musica, refrao),
            parse_mode="Markdown"
        )

    except Exception as e:
        print("Erro resposta:", e)

# =========================
# INLINE (SEGURO)
# =========================

async def inline_query(update, context):
    try:
        query = update.inline_query.query
        if not query:
            return

        musicas = buscar_varias_musicas(query)
        results = []

        for m in musicas:
            thumb = m["thumb"] or None

            results.append(
                InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title=f"{m['title']} — {m['artist']}",
                    description=m['album'],
                    thumbnail_url=thumb,
                    input_message_content=InputTextMessageContent(
                        montar_msg_musica(m),
                        parse_mode="Markdown"
                    )
                )
            )

            results.append(
                InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title=f"{m['title']} — {m['artist']} ♪ ♫ Lyrics",
                    description="Com refrão",
                    thumbnail_url=thumb,
                    input_message_content=InputTextMessageContent(
                        montar_msg_com_letra(m, "Carregando..."),
                        parse_mode="Markdown"
                    )
                )
            )

        await update.inline_query.answer(results, cache_time=1)

    except Exception as e:
        print("Erro inline:", e)

# =========================
# MAIN
# =========================

def main():
    print("🚀 BOT ONLINE")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("music", music))
    app.add_handler(CallbackQueryHandler(selecionar_musica, pattern="^select"))
    app.add_handler(CallbackQueryHandler(resposta_final, pattern="^(yes|no)"))
    app.add_handler(InlineQueryHandler(inline_query))

    app.run_polling()

if __name__ == "__main__":
    main()