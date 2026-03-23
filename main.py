import os
import re
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from openai import OpenAI

# =========================
# CONFIG
# =========================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GENIUS_API_KEY = os.getenv("GENIUS_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN não definido")
if not GENIUS_API_KEY:
    raise ValueError("GENIUS_API_KEY não definido")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY não definido")

client = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# GENIUS SEARCH
# =========================

def buscar_musica(query):
    url = "https://api.genius.com/search"
    headers = {"Authorization": f"Bearer {GENIUS_API_KEY}"}
    params = {"q": query}

    r = requests.get(url, headers=headers, params=params)
    data = r.json()

    hits = data.get("response", {}).get("hits", [])
    if not hits:
        return None

    song = hits[0]["result"]
    return {
        "title": song["title"],
        "artist": song["primary_artist"]["name"],
        "url": song["url"]
    }

# =========================
# SCRAPING LETRA
# =========================

def pegar_letra(url):
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    r = requests.get(url, headers=headers)
    soup = BeautifulSoup(r.text, "lxml")

    lyrics_divs = soup.find_all("div", {"data-lyrics-container": "true"})

    letra = []
    for div in lyrics_divs:
        texto = div.get_text(separator="\n")
        letra.append(texto)

    return "\n".join(letra).strip()

# =========================
# OPENAI - EXTRAIR REFRÃO
# =========================

def extrair_refrao(letra):
    try:
        prompt = f"""
Extraia APENAS o refrão principal da música abaixo.

- Retorne SOMENTE o refrão
- Sem explicações
- Sem colchetes tipo [Chorus]
- Máximo 8 linhas

Letra:
{letra}
"""

        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )

        return resp.choices[0].message.content.strip()

    except Exception as e:
        print("Erro OpenAI:", e)
        return None

# =========================
# COMANDO /lyrics
# =========================

async def lyrics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Use: /lyrics nome da música")
        return

    query = " ".join(context.args)

    await update.message.reply_text("🔎 Buscando música...")

    musica = buscar_musica(query)

    if not musica:
        await update.message.reply_text("❌ Música não encontrada.")
        return

    letra = pegar_letra(musica["url"])

    if not letra:
        await update.message.reply_text("❌ Não consegui obter a letra.")
        return

    refrao = extrair_refrao(letra)

    if not refrao:
        await update.message.reply_text("❌ Não consegui extrair o refrão.")
        return

    # Formatação estilo citação Telegram
    refrao_formatado = "\n".join([f"> {linha}" for linha in refrao.split("\n") if linha.strip()])

    mensagem = (
        f"🎵 *{musica['title']}*\n"
        f"👤 _{musica['artist']}_\n\n"
        f"{refrao_formatado}"
    )

    await update.message.reply_text(
        mensagem,
        parse_mode="Markdown"
    )

# =========================
# MAIN
# =========================

def main():
    print("🚀 BOT INICIANDO...")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("lyrics", lyrics))

    app.run_polling()

if __name__ == "__main__":
    main()