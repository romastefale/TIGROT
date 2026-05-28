import asyncio
import html
import json
import logging
import os
import random
import re
import time
import unicodedata
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import redis.asyncio as redis
from PIL import Image, ImageOps
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Message, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("tarot_bot")

BOT_TOKEN = (os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()

# ---------------- PONTE @mira ----------------
# A interpretação agora vem do bot @mira no mesmo grupo (sem Gemini).
TIGROT_GROUP_ID = int(os.getenv("TIGROT_GROUP_ID", "-5258446619"))
MIRA_PREFIX = os.getenv("MIRA_PREFIX", "@mira").strip()
MIRA_USERNAME = os.getenv("MIRA_USERNAME", "mira").strip().lstrip("@").lower()
_MIRA_USER_ID_RAW = os.getenv("MIRA_USER_ID", "").strip()
MIRA_USER_ID: Optional[int] = int(_MIRA_USER_ID_RAW) if _MIRA_USER_ID_RAW else None
MIRA_WAIT_TIMEOUT = int(os.getenv("MIRA_WAIT_TIMEOUT", "600"))
# Tempo de "assentamento": a Mira costuma editar/streamar a resposta. Depois de
# receber (ou ver editada) a última parte, esperamos esse tanto sem novas
# alterações antes de considerar a resposta completa.
MIRA_SETTLE_SECONDS = float(os.getenv("MIRA_SETTLE_SECONDS", "8"))

REDIS_URL = os.getenv("REDIS_URL", "").strip()
REDIS_TTL_SECONDS = int(os.getenv("REDIS_TTL_SECONDS", "3600"))
SESSION_CLEANUP_SECONDS = int(os.getenv("SESSION_CLEANUP_SECONDS", "600"))
SESSION_MAX_AGE_SECONDS = int(os.getenv("SESSION_MAX_AGE_SECONDS", "1800"))

MAX_CARDS = 12
CARDS_PER_PAGE = 8

SESSIONS: Dict[int, Dict[str, Any]] = {}

IMAGE_ROOT = Path(os.getenv("RWS_IMAGE_DIR", "assets/rws-png"))
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

redis_client = redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None

# ---------------- CARTAS ----------------

TAROT_MAJOR = [
    "O Louco", "O Mago", "A Sacerdotisa", "A Imperatriz", "O Imperador",
    "O Hierofante", "Os Enamorados", "O Carro", "A Força", "O Eremita",
    "A Roda da Fortuna", "A Justiça", "O Enforcado", "A Morte",
    "A Temperança", "O Diabo", "A Torre", "A Estrela", "A Lua",
    "O Sol", "O Julgamento", "O Mundo",
]

RANKS = [
    "Ás", "Dois", "Três", "Quatro", "Cinco", "Seis", "Sete", "Oito",
    "Nove", "Dez", "Valete", "Cavaleiro", "Rainha", "Rei",
]
SUITS = ["Copas", "Paus", "Espadas", "Ouros"]
MINOR = {s: [f"{r} de {s}" for r in RANKS] for s in SUITS}
ALL_CARDS = TAROT_MAJOR + [c for s in SUITS for c in MINOR[s]]


TIRAGENS = {
    "dia": {
        "label": "🌞 Carta do Dia / Sim ou Não",
        "count": 1,
        "prompt_name": "Carta do Dia / Sim ou Não",
        "description": "Resposta direta e rápida.",
    },
    "pf": {
        "label": "🕰️ Passado, Presente e Futuro",
        "count": 3,
        "prompt_name": "Passado, Presente e Futuro",
        "description": "Foca na evolução de uma situação.",
    },
    "sc": {
        "label": "🧭 Situação, Desafio e Conselho",
        "count": 3,
        "prompt_name": "Situação, Desafio e Conselho",
        "description": "Auxilia na tomada de decisão.",
    },
    "peladan": {
        "label": "✳️ Tiragem Péladan",
        "count": 5,
        "prompt_name": "Tiragem Péladan",
        "description": "Analisa influências, obstáculos e desfecho.",
    },
    "ferradura": {
        "label": "🐎 Ferradura",
        "count": 7,
        "prompt_name": "Ferradura",
        "description": "Análise detalhada sobre amor, trabalho ou questões gerais.",
    },
    "cruz": {
        "label": "✝️ Cruz Celta",
        "count": 10,
        "prompt_name": "Cruz Celta",
        "description": "Uma das tiragens mais completas para previsões profundas.",
    },
    "mandala12": {
        "label": "🪐 Mandala Astrológica (12 cartas)",
        "count": 12,
        "prompt_name": "Mandala Astrológica",
        "description": "Analisa 12 áreas da vida baseada nas casas astrológicas.",
    },
    "mandala13": {
        "label": "🪐 Mandala Astrológica (13 cartas)",
        "count": 13,
        "prompt_name": "Mandala Astrológica",
        "description": "Analisa 13 posições da Mandala Astrológica.",
    },
}

RWS_MAJOR_IMAGE_STEMS = {
    "O Louco": "TarotRWS-00-louco",
    "O Mago": "TarotRWS-01-mago",
    "A Sacerdotisa": "TarotRWS-02-alta-sacerdotisa",
    "A Imperatriz": "TarotRWS-03-imperatriz",
    "O Imperador": "TarotRWS-04-imperador",
    "O Hierofante": "TarotRWS-05-hierofante",
    "Os Enamorados": "TarotRWS-06-enamorados",
    "O Carro": "TarotRWS-07-carro",
    "A Força": "TarotRWS-08-forca",
    "O Eremita": "TarotRWS-09-eremita",
    "A Roda da Fortuna": "TarotRWS-10-roda",
    "A Justiça": "TarotRWS-11-justica",
    "O Enforcado": "TarotRWS-12-pendurado",
    "A Morte": "TarotRWS-13-morte",
    "A Temperança": "TarotRWS-14-temperanca",
    "O Diabo": "TarotRWS-15-diabo",
    "A Torre": "TarotRWS-16-torre",
    "A Estrela": "TarotRWS-17-estrela",
    "A Lua": "TarotRWS-18-lua",
    "O Sol": "TarotRWS-19-sol",
    "O Julgamento": "TarotRWS-20-julgamento",
    "O Mundo": "TarotRWS-21-mundo",
}

RWS_MINOR_IMAGE_STEMS = {
    f"{rank} de {suit}": f"TarotRWS-{suit}-{idx:02d}"
    for suit in SUITS
    for idx, rank in enumerate(RANKS, 1)
}

# ---------------- UTILITÁRIOS ----------------

def slugify(v: str) -> str:
    v = unicodedata.normalize("NFKD", v or "")
    v = "".join(c for c in v if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", v.lower()).strip("-")


def build_index() -> Dict[str, Path]:
    idx: Dict[str, Path] = {}
    if not IMAGE_ROOT.exists():
        logger.warning("Pasta de imagens não encontrada: %s", IMAGE_ROOT)
        return idx

    for f in IMAGE_ROOT.rglob("*"):
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS:
            idx[slugify(f.stem)] = f

    logger.info("Índice de imagens carregado: %d arquivos em %s", len(idx), IMAGE_ROOT)
    return idx


IMAGE_INDEX = build_index()


def find_image(card_name: str) -> Optional[Path]:
    candidate_stems: List[str] = []

    if card_name in RWS_MAJOR_IMAGE_STEMS:
        candidate_stems.append(RWS_MAJOR_IMAGE_STEMS[card_name])
    if card_name in RWS_MINOR_IMAGE_STEMS:
        candidate_stems.append(RWS_MINOR_IMAGE_STEMS[card_name])

    candidate_stems.extend([
        card_name,
        f"TarotRWS-{slugify(card_name)}",
        slugify(card_name),
    ])

    for stem in candidate_stems:
        path = IMAGE_INDEX.get(slugify(stem))
        if path:
            return path
    return None


def render_image(path: Path, rev: bool) -> BytesIO:
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        if rev:
            img = img.rotate(180, expand=True)
        b = BytesIO()
        img.save(b, "PNG")
        b.seek(0)
        return b


def split_text(text: str, limit: int = 3800) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts: List[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < 100:
            cut = text.rfind(". ", 0, limit)
        if cut < 100:
            cut = limit
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts



def markdown_to_html(text: str) -> str:
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<u>\1</u>", text)
    text = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<i>\1</i>", text)
    return text


def prepare_telegram_html(text: str) -> str:
    # Correção: remover tags inválidas que a IA possa inventar
    text = text.replace("<tg-b>", "<b>").replace("</tg-b>", "</b>")
    text = text.replace("<tg-i>", "<i>").replace("</tg-i>", "</i>")
    
    converted = markdown_to_html(text)
    escaped = html.escape(converted)
    for tag in ("b", "/b", "i", "/i", "u", "/u"):
        escaped = escaped.replace(f"&lt;{tag}&gt;", f"<{tag}>")
    return escaped


async def send_split_message(chat_id: int, text: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    for part in split_text(text):
        await context.bot.send_message(
            chat_id=chat_id,
            text=prepare_telegram_html(part),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

# ---------------- SESSÃO ----------------

def _default_session() -> Dict[str, Any]:
    return {
        "cards": [],
        "group": None,
        "page": 0,
        "pending": None,
        "updated_at": time.time(),
    }


def _sanitize_session(data: Dict[str, Any]) -> Dict[str, Any]:
    base = _default_session()
    base.update({
        "cards": data.get("cards", []),
        "group": data.get("group"),
        "page": int(data.get("page", 0) or 0),
        "pending": data.get("pending"),
        "updated_at": float(data.get("updated_at", time.time()) or time.time()),
    })
    return base


async def load_session(uid: int) -> Dict[str, Any]:
    session = SESSIONS.get(uid)
    if session:
        session["updated_at"] = time.time()
        return session

    if redis_client:
        try:
            raw = await redis_client.get(f"session:{uid}")
            if raw:
                session = _sanitize_session(json.loads(raw))
                session["updated_at"] = time.time()
                SESSIONS[uid] = session
                return session
        except Exception:
            logger.exception("Falha ao carregar sessão do Redis para uid=%s", uid)

    session = _default_session()
    SESSIONS[uid] = session
    return session


async def save_session(uid: int, session: Dict[str, Any]) -> None:
    session["updated_at"] = time.time()
    SESSIONS[uid] = session
    if redis_client:
        try:
            await redis_client.set(
                f"session:{uid}",
                json.dumps(session, ensure_ascii=False),
                ex=REDIS_TTL_SECONDS,
            )
        except Exception:
            logger.exception("Falha ao salvar sessão no Redis para uid=%s", uid)


async def delete_session(uid: int) -> None:
    SESSIONS.pop(uid, None)
    if redis_client:
        try:
            await redis_client.delete(f"session:{uid}")
        except Exception:
            logger.exception("Falha ao excluir sessão no Redis para uid=%s", uid)


async def cleanup_sessions_task() -> None:
    while True:
        try:
            now = time.time()
            expired = [
                uid for uid, data in list(SESSIONS.items())
                if now - float(data.get("updated_at", now)) > SESSION_MAX_AGE_SECONDS
            ]
            for uid in expired:
                SESSIONS.pop(uid, None)
        except Exception:
            logger.exception("Falha na limpeza automática de sessões")
        await asyncio.sleep(SESSION_CLEANUP_SECONDS)

# ---------------- PONTE @mira ----------------

if not BOT_TOKEN:
    raise RuntimeError("Defina a variável de ambiente BOT_TOKEN (ou TELEGRAM_BOT_TOKEN).")

class _MiraPending:
    """Acumula a resposta da Mira (que costuma chegar editada/em partes)."""

    def __init__(self, loop: "asyncio.AbstractEventLoop") -> None:
        self.future: "asyncio.Future[str]" = loop.create_future()
        self.loop = loop
        # message_id da Mira -> texto mais recente daquela mensagem.
        self.parts: Dict[int, str] = {}
        self._settle_handle: Optional["asyncio.TimerHandle"] = None

    def merged_text(self) -> str:
        return "\n\n".join(self.parts[k] for k in sorted(self.parts)).strip()

    def update_part(self, message_id: int, text: str) -> None:
        self.parts[message_id] = text
        if self._settle_handle is not None:
            self._settle_handle.cancel()
        self._settle_handle = self.loop.call_later(MIRA_SETTLE_SECONDS, self._settle)

    def _settle(self) -> None:
        if not self.future.done():
            self.future.set_result(self.merged_text())

    def cancel_timer(self) -> None:
        if self._settle_handle is not None:
            self._settle_handle.cancel()
            self._settle_handle = None


# Map: message_id da nossa pergunta enviada no grupo -> estado da resposta.
_pending_by_relay: Dict[int, _MiraPending] = {}


def _cards_block(cards: List[Dict[str, Any]]) -> str:
    return "\n".join(
        f"{i + 1}. {c['name']} ({'invertida' if c['rev'] else 'normal'})"
        for i, c in enumerate(cards)
    )


def build_prompt_tecnica(cards: List[Dict[str, Any]], tiragem_name: Optional[str] = None) -> str:
    tiragem_line = f"Tiragem: {tiragem_name}\n" if tiragem_name else ""
    return (
        f"{MIRA_PREFIX} faz a LEITURA TÉCNICA dessas cartas do Tarot Rider-Waite-Smith.\n"
        f"{tiragem_line}"
        "Vá carta por carta na ordem informada. Para cada uma traga: significado clássico, "
        "ponto positivo, ponto negativo e, se invertida, interprete a inversão explicitamente. "
        "No fim, comente combinações e dê uma visão global coerente com a tiragem.\n"
        "Responda em PORTUGUÊS DO BRASIL.\n\n"
        f"Cartas:\n{_cards_block(cards)}"
    )


def build_prompt_preditiva(cards: List[Dict[str, Any]], tiragem_name: Optional[str] = None) -> str:
    tiragem_line = f"Tiragem: {tiragem_name}\n" if tiragem_name else ""
    return (
        f"{MIRA_PREFIX} agora faz a LEITURA PREDITIVA GERAL dessas mesmas cartas.\n"
        f"{tiragem_line}"
        "Seja ousada, direta e prática: o que tende a acontecer, prazos aproximados quando "
        "fizer sentido, riscos concretos, oportunidades e conselho final acionável. "
        "Não suavize o que for difícil, mas não afirme certezas absolutas. "
        "Responda em PORTUGUÊS DO BRASIL.\n\n"
        f"Cartas:\n{_cards_block(cards)}"
    )


async def ask_mira(bot, prompt: str) -> str:
    """Envia uma pergunta para @mira no grupo TIGROT_GROUP_ID e aguarda a resposta dela.

    A Mira costuma responder editando a mensagem (streaming) ou em várias partes,
    então acumulamos tudo e só consideramos pronto após MIRA_SETTLE_SECONDS sem
    novas alterações (debounce). Timeout total: MIRA_WAIT_TIMEOUT.
    """
    loop = asyncio.get_running_loop()
    pending = _MiraPending(loop)

    text = prompt if prompt.lstrip().lower().startswith(MIRA_PREFIX.lower()) else f"{MIRA_PREFIX} {prompt}"
    sent = await bot.send_message(chat_id=TIGROT_GROUP_ID, text=text, disable_web_page_preview=True)
    _pending_by_relay[sent.message_id] = pending
    logger.info("MIRA_RELAY_SENT | mid=%s | chars=%d", sent.message_id, len(text))

    try:
        return await asyncio.wait_for(pending.future, timeout=MIRA_WAIT_TIMEOUT)
    except asyncio.TimeoutError:
        # Se chegou algo parcial antes do timeout, entrega o que veio.
        partial = pending.merged_text()
        if partial:
            logger.warning("MIRA_TIMEOUT_PARTIAL | mid=%s | chars=%d", sent.message_id, len(partial))
            return partial
        logger.warning("MIRA_TIMEOUT_EMPTY | mid=%s", sent.message_id)
        raise
    finally:
        pending.cancel_timer()
        _pending_by_relay.pop(sent.message_id, None)


async def mira_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Roteia respostas da Mira (reply à nossa pergunta, inclusive editadas) ao pending."""
    msg = update.effective_message
    if not msg or not msg.reply_to_message:
        return
    if msg.chat_id != TIGROT_GROUP_ID:
        return

    sender = msg.from_user
    if not sender:
        return
    sender_username = (sender.username or "").lstrip("@").lower()
    if sender_username != MIRA_USERNAME:
        return

    relay_id = msg.reply_to_message.message_id
    pending = _pending_by_relay.get(relay_id)
    if pending is None:
        logger.info("MIRA_REPLY_NO_PENDING | relay=%s | mid=%s", relay_id, msg.message_id)
        return
    if pending.future.done():
        return

    answer = (msg.text or msg.caption or "").strip()
    if not answer:
        return
    pending.update_part(msg.message_id, answer)
    logger.info(
        "MIRA_PART | relay=%s | mid=%s | edit=%s | chars=%d",
        relay_id, msg.message_id, bool(update.edited_message), len(answer),
    )


async def interpretar(bot, cards: List[Dict[str, Any]], tiragem_name: Optional[str]) -> Dict[str, str]:
    """Pede ao @mira as duas leituras (técnica e preditiva). Falhas viram aviso amigável."""
    out: Dict[str, str] = {}
    try:
        out["tecnica"] = await ask_mira(bot, build_prompt_tecnica(cards, tiragem_name))
    except Exception:
        logger.exception("Falha ao obter leitura técnica do @mira")
        out["tecnica"] = "⚠️ A leitura técnica não chegou a tempo."
    try:
        out["preditiva"] = await ask_mira(bot, build_prompt_preditiva(cards, tiragem_name))
    except Exception:
        logger.exception("Falha ao obter leitura preditiva do @mira")
        out["preditiva"] = "⚠️ A leitura preditiva não chegou a tempo."
    return out


async def enviar_interpretacao(chat_id: int, ctx: ContextTypes.DEFAULT_TYPE, cards: List[Dict[str, Any]], tiragem_name: Optional[str]) -> None:
    await ctx.bot.send_message(
        chat_id=chat_id,
        text="🔮 Preparando a interpretação das cartas...",
        parse_mode="HTML",
    )
    leituras = await interpretar(ctx.bot, cards, tiragem_name)

    await ctx.bot.send_message(
        chat_id=chat_id,
        text="🧩 <b>Leitura técnica</b>",
        parse_mode="HTML",
    )
    await send_split_message(chat_id, leituras["tecnica"], ctx)

    await ctx.bot.send_message(
        chat_id=chat_id,
        text="🌠 <b>Leitura preditiva geral</b>",
        parse_mode="HTML",
    )
    await send_split_message(chat_id, leituras["preditiva"], ctx)

def menu_grupos() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🃏 Arcanos Maiores", callback_data="g:major")],
        [InlineKeyboardButton("❤️ Copas", callback_data="g:Copas")],
        [InlineKeyboardButton("🔥 Paus", callback_data="g:Paus")],
        [InlineKeyboardButton("⚔️ Espadas", callback_data="g:Espadas")],
        [InlineKeyboardButton("💰 Ouros", callback_data="g:Ouros")],
    ])


def menu_cartas(group: str, page: int) -> InlineKeyboardMarkup:
    cards = TAROT_MAJOR if group == "major" else MINOR[group]
    start = page * CARDS_PER_PAGE
    subset = cards[start:start + CARDS_PER_PAGE]

    kb = [[InlineKeyboardButton(c, callback_data=f"c:{c}")] for c in subset]

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"nav:{group}:{page - 1}"))
    if start + CARDS_PER_PAGE < len(cards):
        nav.append(InlineKeyboardButton("➡️", callback_data=f"nav:{group}:{page + 1}"))
    if nav:
        kb.append(nav)

    kb.append([InlineKeyboardButton("🔙 Voltar", callback_data="back")])
    return InlineKeyboardMarkup(kb)


def pos_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬆️ Normal", callback_data="p:n")],
        [InlineKeyboardButton("⬇️ Invertida", callback_data="p:r")],
    ])


def selected_cards_text(cards: List[Dict[str, Any]], max_cards: int = MAX_CARDS) -> str:
    lines = [
        f"{i + 1}. {c['name']} ({'invertida' if c['rev'] else 'normal'})"
        for i, c in enumerate(cards)
    ]
    lines.append("")
    lines.append(f"Total: {len(cards)}/{max_cards}")
    return "\n".join(lines)


def tiragens_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(TIRAGENS["dia"]["label"], callback_data="tir:dia")],
        [InlineKeyboardButton(TIRAGENS["pf"]["label"], callback_data="tir:pf")],
        [InlineKeyboardButton(TIRAGENS["sc"]["label"], callback_data="tir:sc")],
        [InlineKeyboardButton(TIRAGENS["peladan"]["label"], callback_data="tir:peladan")],
        [InlineKeyboardButton(TIRAGENS["ferradura"]["label"], callback_data="tir:ferradura")],
        [InlineKeyboardButton(TIRAGENS["cruz"]["label"], callback_data="tir:cruz")],
        [InlineKeyboardButton("🪐 Mandala Astrológica", callback_data="tir:mandala")],
        [InlineKeyboardButton("🔙 Voltar", callback_data="tirback")],
    ])


def tiragem_mandala_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(TIRAGENS["mandala12"]["label"], callback_data="tir:mandala12")],
        [InlineKeyboardButton(TIRAGENS["mandala13"]["label"], callback_data="tir:mandala13")],
        [InlineKeyboardButton("🔙 Voltar", callback_data="tirback")],
    ])


def tiragem_confirm_kb(tir_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirmar", callback_data=f"tirrun:{tir_id}")],
        [InlineKeyboardButton("🔙 Cancelar", callback_data=f"tircancel:{tir_id}")],
    ])


def tiragem_preview_text(tir_id: str) -> str:
    info = TIRAGENS[tir_id]
    return (
        f"🎲 <b>{html.escape(info['label'])}</b>\n\n"
        f"{html.escape(info['description'])}\n\n"
        f"🃏 Quantidade de cartas: <b>{info['count']}</b>\n\n"
        "Confirma para iniciar a tiragem?"
    )
# ---------------- BOT ----------------

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔮 <b>Bem-vindo ao Tarot Interativo</b>\n\n"
        "Aqui você pode explorar o <b>Tarot Rider-Waite</b> de forma clara, intuitiva e completa.\n\n"
        "<b>Comandos disponíveis:</b>\n"
        "/ler - 🃏 <b>Escolher cartas manualmente:</b> Selecione você mesmo as cartas e suas posições (normal ou invertida) para uma leitura personalizada.\n"
        "/tirar - 🎲 <b>Fazer tiragem automática guiada:</b> Escolha um método de tiragem e o bot sorteará e interpretará as cartas para você.\n"
        "/buscar - 🔎 <b>Buscar carta pelo nome:</b> Encontre rapidamente uma carta específica no baralho (ex: /buscar torre).\n"
        "/reset - ♻️ <b>Limpar sessão atual:</b> Apaga suas seleções atuais e reinicia a leitura do zero.\n\n"
        "👇 <i>Ou use os botões abaixo para iniciar sua interpretação manualmente</i>",
        reply_markup=menu_grupos(),
        parse_mode="HTML"
    )
async def ler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🃏 <b>Escolher cartas manualmente</b>\n\n"
        "Neste modo, você mesmo escolhe as cartas e suas posições (normal ou invertida) para criar uma leitura personalizada.\n\n"
        "👇 <i>Use os botões abaixo para iniciar sua seleção:</i>",
        reply_markup=menu_grupos(),
        parse_mode="HTML"
    )


async def reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await delete_session(update.effective_user.id)
    await update.message.reply_text("♻️ Sessão resetada.")


async def buscar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Use assim:\n/buscar torre")
        return

    termo = " ".join(ctx.args).strip().lower()
    matches = [c for c in ALL_CARDS if termo in c.lower()]

    if not matches:
        await update.message.reply_text("Nenhuma carta encontrada.")
        return

    text = "🔎 Resultados:\n\n" + "\n".join(f"• {c}" for c in matches[:20])
    await update.message.reply_text(text)




async def tirar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎲 <b>Escolha a tiragem</b>\n\n"
        "Selecione um formato abaixo para eu preparar a leitura com o tipo certo de interpretação.",
        reply_markup=tiragens_menu(),
        parse_mode="HTML",
    )
async def _run_tiragem(chat_id: int, uid: int, tiragem_id: str, tiragem_name: str, count: int, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cards = random.sample(ALL_CARDS, count)
    result = [{"name": c, "rev": random.choice([True, False])} for c in cards]

    await save_session(uid, {
        "cards": result,
        "group": None,
        "page": 0,
        "pending": None,
        "updated_at": time.time(),
    })

    await ctx.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🎴 <b>{html.escape(tiragem_name)}</b>\n\n"
            f"📌 Tiragem selecionada: <b>{html.escape(TIRAGENS[tiragem_id]['label'])}</b>\n"
            f"🃏 Cartas sorteadas: <b>{count}</b>\n"
            "🔎 Agora a leitura será enviada em partes."
        ),
        parse_mode="HTML",
    )

    for c in result:
        img = find_image(c["name"])
        if img:
            b = render_image(img, c["rev"])
            await ctx.bot.send_photo(
                chat_id=chat_id,
                photo=InputFile(b),
                caption=f"🃏 {html.escape(c['name'])} ({'invertida' if c['rev'] else 'normal'})",
                parse_mode="HTML",
            )
        else:
            await ctx.bot.send_message(
                chat_id=chat_id,
                text=f"🃏 <b>{html.escape(c['name'])}</b> ({'invertida' if c['rev'] else 'normal'})",
                parse_mode="HTML",
            )

    await enviar_interpretacao(chat_id, ctx, result, tiragem_name)

    await ctx.bot.send_message(
        chat_id=chat_id,
        text="✨ Tiragem finalizada.\nUse /ler ou /tirar para nova leitura.",
        parse_mode="HTML",
    )

    await delete_session(uid)

async def cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return

    await q.answer()
    uid = q.from_user.id
    s = await load_session(uid)
    data = q.data or ""

    if data == "tirback":
        await q.edit_message_text(
            "🎲 <b>Escolha a tiragem</b>\n\nSelecione um formato abaixo.",
            reply_markup=tiragens_menu(),
            parse_mode="HTML",
        )
        return

    if data == "tir:mandala":
        await q.edit_message_text(
            "🪐 <b>Mandala Astrológica</b>\n\nEscolha se deseja 12 ou 13 cartas.",
            reply_markup=tiragem_mandala_kb(),
            parse_mode="HTML",
        )
        return

    if data.startswith("tir:"):
        tir_id = data.split(":", 1)[1]
        if tir_id not in TIRAGENS:
            await q.answer("Tiragem inválida.", show_alert=True)
            return

        await q.edit_message_text(
            tiragem_preview_text(tir_id),
            reply_markup=tiragem_confirm_kb(tir_id),
            parse_mode="HTML",
        )
        return

    if data.startswith("tirrun:"):
        tir_id = data.split(":", 1)[1]
        if tir_id not in TIRAGENS:
            await q.answer("Tiragem inválida.", show_alert=True)
            return

        info = TIRAGENS[tir_id]
        await _run_tiragem(
            q.message.chat.id,
            uid,
            tir_id,
            info["prompt_name"],
            info["count"],
            ctx,
        )
        return

    if data.startswith("tircancel:"):
        tir_id = data.split(":", 1)[1]
        if tir_id.startswith("mandala"):
            await q.edit_message_text(
                "🪐 <b>Mandala Astrológica</b>\n\nEscolha se deseja 12 ou 13 cartas.",
                reply_markup=tiragem_mandala_kb(),
                parse_mode="HTML",
            )
        else:
            await q.edit_message_text(
                "🎲 <b>Escolha a tiragem</b>\n\nSelecione um formato abaixo.",
                reply_markup=tiragens_menu(),
                parse_mode="HTML",
            )
        return

    if data.startswith("g:"):
        g = data.split(":", 1)[1]
        s["group"] = g
        s["page"] = 0
        s["pending"] = None
        await save_session(uid, s)
        await q.edit_message_text("Escolha:", reply_markup=menu_cartas(g, 0))
        return

    if data.startswith("nav:"):
        try:
            _, g, p = data.split(":")
            p = max(0, int(p))
        except Exception:
            await q.answer("Página inválida.", show_alert=True)
            return

        s["group"] = g
        s["page"] = p
        await save_session(uid, s)
        await q.edit_message_reply_markup(reply_markup=menu_cartas(g, p))
        return

    if data == "back":
        s["pending"] = None
        await save_session(uid, s)
        await q.edit_message_text("Escolha:", reply_markup=menu_grupos())
        return

    if data.startswith("c:"):
        card = data.split(":", 1)[1]
        s["pending"] = card
        await save_session(uid, s)
        await q.edit_message_text(
            f"🃏 <b>{html.escape(card)}</b>\n\nEscolha a posição:",
            reply_markup=pos_kb(),
            parse_mode="HTML",
        )
        return

    if data.startswith("p:"):
        if not s.get("pending"):
            await q.answer("Escolha uma carta primeiro", show_alert=True)
            return

        if len(s["cards"]) >= MAX_CARDS:
            await q.answer("Limite de cartas atingido", show_alert=True)
            return

        rev = data == "p:r"
        card = s["pending"]

        s["cards"].append({"name": card, "rev": rev})
        s["pending"] = None
        s["updated_at"] = time.time()
        await save_session(uid, s)

        txt = selected_cards_text(s["cards"], MAX_CARDS)
        kb = [
            [InlineKeyboardButton("➕ Continuar", callback_data="cont")],
            [InlineKeyboardButton("✅ Finalizar", callback_data="fim")],
        ]
        await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))
        return

    if data == "cont":
        s["pending"] = None
        await save_session(uid, s)
        await q.edit_message_text("Escolha:", reply_markup=menu_grupos())
        return

    if data == "fim":
        cards = s["cards"]
        if not cards:
            await q.answer("Selecione ao menos uma carta.", show_alert=True)
            return

        for c in cards:
            img = find_image(c["name"])
            if img:
                b = render_image(img, c["rev"])
                await ctx.bot.send_photo(chat_id=q.message.chat.id, photo=InputFile(b))
            else:
                await ctx.bot.send_message(
                    chat_id=q.message.chat.id,
                    text=f"🃏 <b>{html.escape(c['name'])}</b> ({'invertida' if c['rev'] else 'normal'})",
                    parse_mode="HTML",
                )

        await enviar_interpretacao(q.message.chat.id, ctx, cards, None)

        await ctx.bot.send_message(
            chat_id=q.message.chat.id,
            text="✨ Tiragem finalizada.\nUse /ler ou /tirar para nova leitura.",
            parse_mode="HTML",
        )

        await delete_session(uid)
        return
# ---------------- MAIN ----------------

async def post_init(app: Application):
    app.bot_data["cleanup_task"] = asyncio.create_task(cleanup_sessions_task())


def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ler", ler))
    app.add_handler(CommandHandler("tirar", tirar))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("buscar", buscar))
    app.add_handler(CallbackQueryHandler(cb))
    app.add_handler(
        MessageHandler(
            filters.Chat(chat_id=TIGROT_GROUP_ID) & filters.REPLY & (filters.TEXT | filters.CAPTION),
            mira_router,
        )
    )

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()