import os
import uuid
import logging
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
# LOG (ESSENCIAL)
# =========================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# =========================
# ENV
# =========================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GENIUS_API_KEY = os.getenv("GENIUS_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

logging.info(f"TOKEN OK? {bool(TELEGRAM_TOKEN)}")
logging.info(f"GENIUS OK? {bool(GENIUS_API_KEY)}")
logging.info(f"OPENAI OK? {bool(OPENAI_API_KEY)}")

client = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# GENIUS
# =========================

def buscar_varias_musicas(query):
    try:
        url = "https://api.genius.com/search"
        headers = {"Authorization": f"Bearer {GENIUS_API_KEY}"}
        params = {"q": query}

        r = requests.get(url, headers=headers, params=params, timeout=10)
        data = r.json()

        resultados = []
        for hit in data.get("response", {}).get("hits", [])[:5]:
            song = hit["result"]

            resultados.append({
                "title": song.get("title"),
                "artist": song.get("primary_artist", {}).get("name"),
                "album": song.get("album", {}).get("name", "Single"),
                "url": song.get("url"),
                "thumb": song.get("song_art_image_thumbnail_url") or ""
            })

        return resultados

    except Exception as e:
        logging.error(f"Genius erro: {e}")
        return []

# =========================
# SCRAPING
# =========================

def pegar_letra(url):
    try:
        r = requests.get(url, timeout=10)
        soup = BeautifulSoup(r.text, "lxml")

        divs = soup.find_all("