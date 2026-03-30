import os
import re
import sys
import time
import asyncio
import logging
import requests
import telegram.error
from threading import Lock
from concurrent.futures import ThreadPoolExecutor

from telegram import (
    Update,
    InlineQueryResultPhoto,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    InlineQueryHandler,
    MessageHandler,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    filters
)

# ==========================================
# LAYOUT 1: CONFIGURAÇÃO DE LOGS E AMBIENTE
# ==========================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Variáveis do Railway
TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", TOKEN.replace(":", "")[:20] if TOKEN else None)

ADMIN_ID_RAW = os.getenv("ADMIN_ID")
try:
    ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW else None
except ValueError:
    logger.warning("Valor de ADMIN_ID inválido. O bot rodará sem restrições de administrador.")
    ADMIN_ID = None

try:
    PORT = int(os.getenv("PORT", 8080))
except ValueError:
    logger.warning("Valor de PORT inválido, usando 8080 por padrão.")
    PORT = 8080

if not TOKEN:
    raise ValueError("ERRO CRÍTICO: Configure TELEGRAM_TOKEN nas variáveis do Railway!")

# ==========================================
# LAYOUT 2: VARIÁVEIS GLOBAIS E PADRÕES
# ==========================================
session = requests.Session()
cache = {}
cache_lock = Lock()
CACHE_MAX_SIZE = 500
_executor = ThreadPoolExecutor(max_workers=4)

FORBIDDEN_PATTERN = re.compile(
    r'['
    r'\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF'  # Árabe
    r'\u0400-\u04FF\u0500-\u052F\u2DE0-\u2DFF\uA640-\uA69F'               # Cirílico
    r'\u4E00-\u9FFF\u3400-\u4DBF'                                         # Chinês
    r'\u0900-\u097F'                                                      # Hindi
    r'\u0980-\u09FF'                                                      # Bengali
    r']'
)

# ==========================================
# LAYOUT 3: NÚCLEO DE HIGIENIZAÇÃO E SEGURANÇA
# ==========================================
async def sanitize_text(text):
    if not text:
        return text

    text = str(text)

    if not FORBIDDEN_PATTERN.search(text):
        return text

    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "auto", "tl": "en", "dt": "t", "q": text}
        response = await asyncio.to_thread(session.get, url, params=params, timeout=3)
        
        if response.status_code == 200:
            data = response.json()
            translated_text = "".join([sentence[0] for sentence in data[0]])
            if not FORBIDDEN_PATTERN.search(translated_text):
                return translated_text
    except Exception as e:
        logger.warning(f"Falha na tradução automática: {e}")

    sanitized = FORBIDDEN_PATTERN.sub("", text)
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    return sanitized if sanitized else "Unknown"

def escape_markdown(text):
    return re.sub(r"([_*`\[\]()~`>#+\-=|{}.!])", r"\\\1", str(text))

def evict_cache():
    with cache_lock:
        if len(cache) >= CACHE_MAX_SIZE:
            oldest_keys = list(cache.keys())[:100]
            for k in oldest_keys:
                del cache[k]

def is_admin(user_id: int | None) -> bool:
    return ADMIN_ID is not None and user_id == ADMIN_ID

# ==========================================
# LAYOUT 4: COMANDOS E INTERAÇÕES DO BOT
# ==========================================
async def comando_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responde ao comando /start com base no nível de acesso."""
    user = update.effective_user
    
    if is_admin(user.id):
        saudacao = f"Olá, Admin {user.first_name}! Sistema operacional no Railway."
    else:
        saudacao = f"Olá, {user.first_name}! Envie um texto e eu farei a higienização."
        
    await update.message.reply_text(saudacao)

async def processar_mensagem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Intercepta mensagens, higieniza e devolve o resultado seguro."""
    texto_original = update.message.text
    
    # Feedback visual enquanto a tradução ocorre em background
    msg_temporaria = await update.message.reply_text("⏳ Processando texto...")
    
    # Passa pelo seu filtro
    texto_limpo = await sanitize_text(texto_original)
    texto_seguro = escape_markdown(texto_limpo)
    
    # Atualiza a mensagem com o resultado final
    await msg_temporaria.edit_text(
        f"*Texto Higienizado:*\n{texto_seguro}", 
        parse_mode=ParseMode.MARKDOWN_V2
    )

# ==========================================
# LAYOUT 5: MOTOR DE EXECUÇÃO (MAIN)
# ==========================================
def main():
    logger.info("Iniciando o sistema...")
    app = Application.builder().token(TOKEN).build()

    # Registrando os comandos do Layout 4
    app.add_handler(CommandHandler("start", comando_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, processar_mensagem))

    # Decisão de Inicialização: Railway (Webhook) vs Local (Polling)
    if WEBHOOK_URL:
        logger.info(f"Modo Webhook ativado (Railway). Escutando na porta {PORT}...")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            secret_token=WEBHOOK_SECRET,
            webhook_url=WEBHOOK_URL
        )
    else:
        logger.info("Modo Polling ativado (Local).")
        app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
