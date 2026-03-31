import asyncio
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
from google import genai
from PIL import Image, ImageOps
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tarot_bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

REDIS_URL = os.getenv("REDIS_URL", "").strip()
REDIS_TTL_SECONDS = int(os.getenv("REDIS_TTL_SECONDS", "3600"))
SESSION_CLEANUP_SECONDS = int(os.getenv("SESSION_CLEANUP_SECONDS", "600"))
SESSION_MAX_AGE_SECONDS = int(os.getenv("SESSION_MAX_AGE_SECONDS", "1800"))

MAX_CARDS = 12
CARDS_PER_PAGE = 8

SESSIONS: Dict[int, Dict[str, Any]] = {}

IMAGE_ROOT = Path(os.getenv("RWS_IMAGE_DIR", "assets/rws"))
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

redis_client = redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None

# ---------------- CARTAS ----------------

TAROT_MAJOR = [
    "O Louco", "O Mago", "A Sacerdotisa", "A Imperatriz", "O Imperador",
    "O Hierofante", "Os Enamorados", "O Carro", "A Força", "O Eremita",
    "A Roda da Fortuna", "A Justiça", "O Enforcado", "A Morte",
    "A Temperança", "O Diabo", "A Torre", "A Estrela", "A Lua",
    "O Sol", "O Julgamento", "O Mundo"
]

RANKS = [
    "Ás", "Dois", "Três", "Quatro", "Cinco", "Seis", "Sete", "Oito",
    "Nove", "Dez", "Valete", "Cavaleiro", "Rainha", "Rei"
]
SUITS = ["Copas", "Paus", "Espadas", "Ouros"]
MINOR = {s: [f"{r} de {s}" for r in RANKS] for s in SUITS}
ALL_CARDS = TAROT_MAJOR + [c for s in SUITS for c in MINOR[s]]

# Arquivos locais do RWS
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

# ---------------- UTILS ----------------

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


async def send_split_message(chat_id: int, text: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    for part in split_text(text):
        await context.bot.send_message(chat_id=chat_id, text=part)

# ---------------- SESSION ----------------

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
        raw = await redis_client.get(f"session:{uid}")
        if raw:
            try:
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

# ---------------- GEMINI ----------------

if not BOT_TOKEN:
    raise RuntimeError("Defina a variável de ambiente BOT_TOKEN.")
if not GEMINI_API_KEY:
    raise RuntimeError("Defina GEMINI_API_KEY ou GOOGLE_API_KEY.")

client = genai.Client(api_key=GEMINI_API_KEY)


def build_prompt(cards: List[Dict[str, Any]]) -> str:
    txt = "\n".join(
        f"{i + 1}. {c['name']} ({'invertida' if c['rev'] else 'normal'})"
        for i, c in enumerate(cards)
    )

    return f"""
Você é um intérprete didático de tarot.

Responda sempre nesta ordem:
1. Carta 1
   - significado
   - ponto positivo
   - ponto negativo
2. Carta 2
   - significado
   - ponto positivo
   - ponto negativo
3. Combinações entre cartas
4. Visão global da tiragem

Regras:
- Não omita aspectos negativos.
- Não suavize o que for difícil.
- Não afirme certezas absolutas.
- Seja claro, direto e didático.
- Escreva em português do Brasil.
- Use separação por parágrafos.

Cartas:
{txt}
""".strip()


def ai(cards: List[Dict[str, Any]]) -> str:
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=build_prompt(cards),
    )
    return (response.text or "").strip()

# ---------------- UI ----------------

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

# ---------------- BOT ----------------

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔮 Tarot\n\nEscolha:",
        reply_markup=menu_grupos(),
    )


async def ler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return await start(update, ctx)


async def reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await delete_session(update.effective_user.id)
    await update.message.reply_text("Resetado")


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
    args = ctx.args or []
    try:
        n = int(args[0]) if args else 3
    except Exception:
        n = 3

    n = max(1, min(n, MAX_CARDS))

    cards = random.sample(ALL_CARDS, n)
    result = [{"name": c, "rev": random.choice([True, False])} for c in cards]

    await save_session(update.effective_user.id, {
        "cards": result,
        "group": None,
        "page": 0,
        "pending": None,
        "updated_at": time.time(),
    })

    for c in result:
        img = find_image(c["name"])
        if img:
            b = render_image(img, c["rev"])
            await ctx.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=InputFile(b),
            )
        else:
            await ctx.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"🃏 {c['name']} ({'invertida' if c['rev'] else 'normal'})",
            )

    try:
        res = await asyncio.to_thread(ai, result)
    except Exception:
        logger.exception("Erro ao gerar interpretação do Gemini")
        res = "Erro ao gerar interpretação."

    for part in split_text(res):
        await ctx.bot.send_message(chat_id=update.effective_chat.id, text=part)

    await ctx.bot.send_message(
        chat_id=update.effective_chat.id,
        text="✨ Tiragem finalizada.\nUse /ler ou /tirar para nova.",
    )

    await delete_session(update.effective_user.id)


async def cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return

    await q.answer()
    uid = q.from_user.id
    s = await load_session(uid)
    data = q.data or ""

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
        await q.edit_message_text(f"{card}\n\nPosição?", reply_markup=pos_kb())
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
                    text=f"🃏 {c['name']} ({'invertida' if c['rev'] else 'normal'})",
                )

        try:
            res = await asyncio.to_thread(ai, cards)
        except Exception:
            logger.exception("Erro ao gerar interpretação do Gemini")
            res = "Erro ao gerar interpretação."

        partes = split_text(res)
        if not partes:
            partes = ["Sem resposta de interpretação no momento."]

        for p in partes:
            await ctx.bot.send_message(chat_id=q.message.chat.id, text=p.strip())

        await ctx.bot.send_message(
            chat_id=q.message.chat.id,
            text="✨ Tiragem finalizada.\nUse /ler ou /tirar para nova.",
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

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
