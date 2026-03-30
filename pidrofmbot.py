import os
import re
import time
import asyncio
import logging
import requests
import hashlib
import html
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from telegram import (
    Update,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LinkPreviewOptions
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    InlineQueryHandler,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# =========================
# CONFIGURAÇÃO E LOGGING
# =========================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", TOKEN.replace(":", "")[:20] if TOKEN else None)
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

try:
    PORT = int(os.getenv("PORT", 8443))
except (ValueError, TypeError):
    PORT = 8443

session = requests.Session()
music_cache = {}  
_executor = ThreadPoolExecutor(max_workers=4)

# =========================
# CONTROLE DO /log
# =========================
log_sessions = {}

# =========================
# SANITIZAÇÃO DE IDIOMAS PROIBIDOS
# =========================
FORBIDDEN_ALPHABETS_REGEX = re.compile(
    r'['
    r'\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF'
    r'\u0400-\u04FF\u0500-\u052F\u2DE0-\u2DFF\uA640-\uA69F'
    r'\u4E00-\u9FFF\u3400-\u4DBF\U00020000-\U0002A6DF'
    r'\u0900-\u097F'
    r'\u0980-\u09FF'
    r']'
)

def contains_forbidden(text):
    if not text: return False
    return bool(FORBIDDEN_ALPHABETS_REGEX.search(text))

def sanitize_text(text):
    if not text: return text
    if not contains_forbidden(text): return text
    
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "auto", "tl": "en", "dt": "t", "q": text}
        resp = session.get(url, params=params, timeout=3)
        if resp.status_code == 200:
            translated = "".join([sentence[0] for sentence in resp.json()[0]])
            if not contains_forbidden(translated):
                return translated.strip()
    except Exception as e:
        logger.error(f"Erro na tradução automática: {e}")
    
    cleaned = FORBIDDEN_ALPHABETS_REGEX.sub('', text).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned if cleaned else "Desconhecido"

# =========================
# COMANDO /log
# =========================
async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    log_sessions[update.effective_user.id] = {"step": "waiting_text"}
    await update.message.reply_text("📝Qual texto de <i>Update</i> você deseja enviar?", parse_mode=ParseMode.HTML)

async def handle_log_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return

    session_data = log_sessions.get(user_id)
    if not session_data:
        return

    if session_data["step"] == "waiting_text":
        session_data["message"] = update.message
        session_data["step"] = "confirm"

        # reenviar exatamente igual
        await update.message.copy(chat_id=update.effective_chat.id)

        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🆗 Correto", callback_data="log_ok"),
                InlineKeyboardButton("✏️ Editar...", callback_data="log_edit")
            ]
        ])

        await update.message.reply_text("🆗Correto?", reply_markup=markup)

async def cb_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if user_id != ADMIN_ID:
        return

    await query.answer()

    if query.data == "log_ok":
        log_sessions.pop(user_id, None)
        await query.edit_message_text("✅ Enviado com sucesso!")

    elif query.data == "log_edit":
        log_sessions[user_id] = {"step": "waiting_text"}
        await query.edit_message_text("📝Qual texto de <i>Update</i> você deseja enviar?", parse_mode=ParseMode.HTML)

# =========================
# RESTANTE DO CÓDIGO (INALTERADO)
# =========================

def get_chorus_via_api(title, artist):
    try:
        clean_artist = re.sub(r'[\(\[].*[\)\]]', '', artist).strip()
        clean_title = re.sub(r'[\(\[].*[\)\]]', '', title).strip()
        url = f"https://api.lyrics.ovh/v1/{clean_artist}/{clean_title}"
        resp = session.get(url, timeout=10)
        
        if resp.status_code != 200: return None
        full_lyrics = resp.json().get("lyrics", "")
        if not full_lyrics: return None
        
        if contains_forbidden(full_lyrics):
            return "🎵 [Letra bloqueada: Idioma original não suportado neste grupo]"

        parts = re.split(r'(\[Refrão\]|\[Chorus\]|Refrão:|Chorus:)', full_lyrics, flags=re.IGNORECASE)
        if len(parts) > 1: return parts[2].strip().split('\n\n')[0]
        stanzas = [s.strip() for s in full_lyrics.split('\n\n') if len(s.strip()) > 20]
        if stanzas:
            counts = Counter(stanzas)
            most_common = counts.most_common(1)[0]
            if most_common[1] > 1: return most_common[0]
            return stanzas[0]
        return full_lyrics[:250] + "..."
    except Exception as e:
        logger.error(f"Erro na API de Letras: {e}")
        return None

def search_deezer_sync(query):
    query = re.sub(r"[-_]+", " ", query).strip()
    try:
        r = session.get("https://api.deezer.com/search", params={"q": query, "limit": 10}, timeout=5)
        return r.json().get("data", []) if r.status_code == 200 else []
    except Exception: return []

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🎹 Esse é o bot do @tigrao para mostrar as músicas que voce esta ouvindo! \n\n"
        "🎧 Para usar, basta digitar o nome da música…\n\n"
        "📜 Se quiser a letra do refrão só pedir!"
    )
    await update.message.reply_text(msg)

# =========================
# MAIN
# =========================
def main():
    if not TOKEN: return
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("log", cmd_log))

    app.add_handler(MessageHandler(filters.ALL, handle_log_input))
    app.add_handler(CallbackQueryHandler(cb_log, pattern=r"^log_"))

    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search))
    app.add_handler(CallbackQueryHandler(cb_handler, pattern=r"^(c|l|s)\|"))

    if WEBHOOK_URL:
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TOKEN}",
            secret_token=WEBHOOK_SECRET,
            drop_pending_updates=True
        )
    else:
        app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
