import asyncio
import html
import json
import logging
import math
import os
import re
import unicodedata
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

from google import genai
from PIL import Image, ImageOps
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("tarot_bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "tarot-webhook").strip("/")
WEBHOOK_URL = (os.getenv("WEBHOOK_URL") or os.getenv("RAILWAY_PUBLIC_DOMAIN") or "").strip()
WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN", "").strip()

PORT = int(os.getenv("PORT", "8080"))
MAX_CARDS = 12
CARDS_PER_PAGE = 10

SESSIONS: Dict[int, Dict[str, Any]] = {}

TAROT_MAJOR = [
    "O Louco",
    "O Mago",
    "A Sacerdotisa",
    "A Imperatriz",
    "O Imperador",
    "O Hierofante",
    "Os Enamorados",
    "O Carro",
    "A Força",
    "O Eremita",
    "A Roda da Fortuna",
    "A Justiça",
    "O Enforcado",
    "A Morte",
    "A Temperança",
    "O Diabo",
    "A Torre",
    "A Estrela",
    "A Lua",
    "O Sol",
    "O Julgamento",
    "O Mundo"
]
TAROT_MINOR_RANKS = [
    "Ás",
    "Dois",
    "Três",
    "Quatro",
    "Cinco",
    "Seis",
    "Sete",
    "Oito",
    "Nove",
    "Dez",
    "Valete",
    "Cavaleiro",
    "Rainha",
    "Rei"
]
TAROT_SUITS = [
    "Copas",
    "Paus",
    "Espadas",
    "Ouros"
]
TAROT_CARDS = TAROT_MAJOR + [f"{rank} de {suit}" for suit in TAROT_SUITS for rank in TAROT_MINOR_RANKS]

CIGANO_CARDS = [
    "O Cavaleiro",
    "O Trevo",
    "O Navio",
    "A Casa",
    "A Árvore",
    "As Nuvens",
    "A Cobra",
    "O Caixão",
    "O Buquê",
    "A Foice",
    "O Chicote",
    "Os Pássaros",
    "A Criança",
    "A Raposa",
    "O Urso",
    "As Estrelas",
    "A Cegonha",
    "O Cão",
    "A Torre",
    "O Jardim",
    "A Montanha",
    "Os Caminhos",
    "Os Ratos",
    "O Coração",
    "O Anel",
    "O Livro",
    "A Carta",
    "O Homem",
    "A Mulher",
    "Os Lírios",
    "O Sol",
    "A Lua",
    "A Chave",
    "Os Peixes",
    "A Âncora",
    "A Cruz"
]

DECK_LABELS = {
    "tarot": "Tarot",
    "cigano": "Baralho Cigano",
}

IMAGE_ROOT = Path(os.getenv("RWS_IMAGE_DIR", "assets/rws"))
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
RWS_IMAGE_INDEX: Dict[str, Path] = {}

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

RWS_MINOR_IMAGE_STEMS: Dict[str, str] = {}


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def build_minor_image_stems() -> None:
    RWS_MINOR_IMAGE_STEMS.clear()
    for suit in TAROT_SUITS:
        for idx, rank in enumerate(TAROT_MINOR_RANKS, 1):
            card_name = f"{rank} de {suit}"
            RWS_MINOR_IMAGE_STEMS[card_name] = f"TarotRWS-{suit}-{idx:02d}"


def build_rws_image_index() -> Dict[str, Path]:
    index: Dict[str, Path] = {}
    if not IMAGE_ROOT.exists():
        logger.warning("Pasta de imagens não encontrada: %s", IMAGE_ROOT)
        return index

    for file_path in IMAGE_ROOT.rglob("*"):
        if file_path.is_file() and file_path.suffix.lower() in IMAGE_EXTENSIONS:
            index[slugify(file_path.stem)] = file_path

    logger.info("Índice de imagens carregado: %d arquivos em %s", len(index), IMAGE_ROOT)
    return index


def find_card_image_path(deck: str, card_name: str) -> Optional[Path]:
    if deck != "tarot":
        return None

    candidate_stems: List[str] = []
    if card_name in RWS_MAJOR_IMAGE_STEMS:
        candidate_stems.append(RWS_MAJOR_IMAGE_STEMS[card_name])
    if card_name in RWS_MINOR_IMAGE_STEMS:
        candidate_stems.append(RWS_MINOR_IMAGE_STEMS[card_name])

    candidate_stems.extend([
        card_name,
        slugify(card_name),
        f"TarotRWS-{slugify(card_name)}",
    ])

    for stem in candidate_stems:
        path = RWS_IMAGE_INDEX.get(slugify(stem))
        if path:
            return path
    return None


def render_card_photo_bytes(image_path: Path, reversed_card: bool) -> BytesIO:
    with Image.open(image_path) as img:
        img = ImageOps.exif_transpose(img)
        if reversed_card:
            img = img.rotate(180, expand=True)
        output = BytesIO()
        img.save(output, format="PNG")
        output.seek(0)
        return output


async def send_card_image(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    card: Dict[str, Any],
    prefix: str = "Carta",
) -> bool:
    image_path = find_card_image_path(card.get("deck", ""), card.get("name", ""))
    if not image_path:
        return False

    try:
        photo_bytes = await asyncio.to_thread(render_card_photo_bytes, image_path, bool(card.get("reversed")))
        caption = f"{prefix}: {card.get('name', 'Carta')} ({'invertida' if card.get('reversed') else 'normal'})"
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=InputFile(photo_bytes, filename=f"{slugify(card.get('name', 'carta'))}.png"),
            caption=caption[:1024],
        )
        return True
    except Exception:
        logger.exception("Falha ao enviar imagem da carta: %s", card.get("name"))
        return False

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "meaning": {"type": "string"},
                    "positive": {"type": "string"},
                    "negative": {"type": "string"}
                },
                "required": ["name", "meaning", "positive", "negative"]
            }
        },
        "combinations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "interpretation": {"type": "string"}
                },
                "required": ["title", "interpretation"]
            }
        },
        "global_view": {"type": "string"}
    },
    "required": ["cards", "combinations", "global_view"]
}

SYSTEM_INSTRUCTION = """
Você é um intérprete didático de tarot e baralho cigano.
Você responde em português do Brasil.

Regras:
- Interprete cada carta separadamente.
- Se a carta vier invertida, considere a posição invertida.
- Se não vier indicação, considere posição comum.
- Para cada carta, entregue significado, ponto positivo e ponto negativo.
- Não omita os aspectos difíceis, tensos, sombrios ou desfavoráveis.
- Não suavize o negativo para agradar.
- Quando houver ambiguidade, mostre possibilidades, não certezas absolutas.
- Depois combine as cartas em leituras possíveis.
- Finalize com uma visão global da tiragem.
- Seja sincero, claro, didático e completo.
- Mantenha o texto útil para estudo, sem prometer verdade absoluta.
""".strip()

if not BOT_TOKEN:
    raise RuntimeError("Defina a variável de ambiente BOT_TOKEN.")
if not GEMINI_API_KEY:
    raise RuntimeError("Defina GEMINI_API_KEY ou GOOGLE_API_KEY.")

GENAI_CLIENT = genai.Client(api_key=GEMINI_API_KEY)

build_minor_image_stems()
RWS_IMAGE_INDEX = build_rws_image_index()


def normalize_public_url(raw: str) -> str:
    raw = (raw or "").strip().rstrip("/")
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    return f"https://{raw}"


PUBLIC_URL = normalize_public_url(WEBHOOK_URL)


def now_ts() -> float:
    return asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else 0.0


def new_session() -> Dict[str, Any]:
    return {
        "deck": None,
        "page": 0,
        "cards": [],
        "pending_index": None,
        "updated_at": 0.0,
    }


def touch_session(user_id: int) -> Dict[str, Any]:
    session = SESSIONS.get(user_id)
    if session is None:
        session = new_session()
        SESSIONS[user_id] = session
    session["updated_at"] = asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else 0.0
    return session


def clear_session(user_id: int) -> None:
    SESSIONS.pop(user_id, None)


def get_cards(deck: str) -> List[str]:
    return TAROT_CARDS if deck == "tarot" else CIGANO_CARDS


def deck_title(deck: str) -> str:
    return DECK_LABELS.get(deck, deck)


def cards_pages(deck: str) -> int:
    return max(1, math.ceil(len(get_cards(deck)) / CARDS_PER_PAGE))


def format_selected_cards(cards: List[Dict[str, Any]]) -> str:
    if not cards:
        return "Nenhuma carta selecionada ainda."

    lines = ["Tiragem atual:"]
    for i, card in enumerate(cards, 1):
        pos = "invertida" if card["reversed"] else "normal"
        lines.append(f"{i}. {card['name']} ({pos})")
    lines.append("")
    lines.append(f"Total: {len(cards)}/{MAX_CARDS}")
    return "\n".join(lines)


def split_telegram_text(text: str, limit: int = 3800) -> List[str]:
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
    for part in split_telegram_text(text):
        await context.bot.send_message(chat_id=chat_id, text=part)


def selection_text(session: Dict[str, Any]) -> str:
    deck = session["deck"]
    page = session["page"]
    total_pages = cards_pages(deck)
    selected = session["cards"]

    lines = [
        f"Baralho: {deck_title(deck)}",
        f"Página: {page + 1}/{total_pages}",
        f"Selecionadas: {len(selected)}/{MAX_CARDS}",
        "",
        "Escolha uma carta pelos botões abaixo.",
    ]

    if selected:
        lines.append("")
        lines.append(format_selected_cards(selected))

    return "\n".join(lines)


def page_keyboard(session: Dict[str, Any]) -> InlineKeyboardMarkup:
    deck = session["deck"]
    page = session["page"]
    cards = get_cards(deck)
    start = page * CARDS_PER_PAGE
    end = min(len(cards), start + CARDS_PER_PAGE)

    rows: List[List[InlineKeyboardButton]] = []

    for idx in range(start, end):
        rows.append([
            InlineKeyboardButton(cards[idx], callback_data=f"pick:{deck}:{idx}")
        ])

    nav_row: List[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"nav:{deck}:{page - 1}"))
    if page < cards_pages(deck) - 1:
        nav_row.append(InlineKeyboardButton("Próxima ➡️", callback_data=f"nav:{deck}:{page + 1}"))
    if nav_row:
        rows.append(nav_row)

    footer: List[InlineKeyboardButton] = []
    if session["cards"] and len(session["cards"]) < MAX_CARDS:
        footer.append(InlineKeyboardButton("➕ Adicionar outra", callback_data="more"))
    if session["cards"]:
        footer.append(InlineKeyboardButton("✅ Finalizar", callback_data="finish"))
    if footer:
        rows.append(footer)

    return InlineKeyboardMarkup(rows)


def position_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬆️ Normal", callback_data="pos:normal")],
        [InlineKeyboardButton("⬇️ Invertida", callback_data="pos:reversed")],
        [InlineKeyboardButton("↩️ Voltar", callback_data="back")],
    ])


def deck_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🃏 Tarot", callback_data="deck:tarot")],
        [InlineKeyboardButton("🔮 Baralho Cigano", callback_data="deck:cigano")],
    ])


def build_prompt(cards: List[Dict[str, Any]]) -> str:
    items = []
    for i, card in enumerate(cards, 1):
        pos = "invertida" if card["reversed"] else "normal"
        items.append(f"{i}. {card['name']} ({pos})")
    cards_block = "\n".join(items)

    return f"""
Você recebeu uma tiragem para interpretar.

Regras da resposta:
- Entregue os campos de forma clara, completa e didática.
- Para cada carta, explique significado, ponto positivo e ponto negativo.
- Não omita partes difíceis, tensas ou desfavoráveis.
- Seja sincero e não afirme certezas absolutas.
- Se houver nuance, mostre possibilidades.
- Depois inclua combinações relevantes entre as cartas.
- Finalize com visão global da tiragem.
- Escreva em português do Brasil.

Cartas:
{cards_block}
""".strip()


def gemini_generate(cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    prompt = build_prompt(cards)

    response = GENAI_CLIENT.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config={
            "system_instruction": SYSTEM_INSTRUCTION,
            "temperature": 0.7,
            "response_mime_type": "application/json",
            "response_json_schema": RESPONSE_SCHEMA,
        },
    )

    raw = (response.text or "").strip()
    raw = re.sub(r"^```json\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"^```\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {
            "cards": [],
            "combinations": [],
            "global_view": raw or "A IA não retornou JSON válido.",
        }

    if not isinstance(data, dict):
        data = {"cards": [], "combinations": [], "global_view": str(data)}

    data.setdefault("cards", [])
    data.setdefault("combinations", [])
    data.setdefault("global_view", "")
    return data


async def render_deck_prompt(query, user_id: int) -> None:
    clear_session(user_id)
    SESSIONS[user_id] = new_session()
    session = SESSIONS[user_id]
    session["updated_at"] = asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else 0.0
    await query.edit_message_text(
        "Escolha o baralho:",
        reply_markup=deck_keyboard(),
    )


async def render_selection_page(query, user_id: int) -> None:
    session = touch_session(user_id)
    text = selection_text(session)
    try:
        await query.edit_message_text(
            text,
            reply_markup=page_keyboard(session),
        )
    except BadRequest as exc:
        if "Message is not modified" not in str(exc):
            raise


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    clear_session(user_id)
    SESSIONS[user_id] = new_session()
    await update.message.reply_text(
        "Escolha o baralho para começar:",
        reply_markup=deck_keyboard(),
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    clear_session(user_id)
    await update.message.reply_text("Sessão limpa. Use /start para recomeçar.")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await query.answer()
    user_id = query.from_user.id
    data = query.data or ""
    session = touch_session(user_id)

    if data.startswith("deck:"):
        deck = data.split(":", 1)[1]
        if deck not in ("tarot", "cigano"):
            await query.edit_message_text("Baralho inválido. Use /start para reiniciar.")
            return
        clear_session(user_id)
        session = {
            "deck": deck,
            "page": 0,
            "cards": [],
            "pending_index": None,
            "updated_at": asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else 0.0,
        }
        SESSIONS[user_id] = session
        await query.edit_message_text(
            f"Baralho escolhido: {deck_title(deck)}\n\n{selection_text(session)}",
            reply_markup=page_keyboard(session),
        )
        return

    if session.get("deck") is None:
        await query.edit_message_text("Use /start e escolha um baralho primeiro.")
        return

    if data.startswith("nav:"):
        try:
            _, deck, page_s = data.split(":")
            session["page"] = max(0, min(int(page_s), cards_pages(deck) - 1))
        except Exception:
            await query.answer("Página inválida.", show_alert=True)
            return
        await render_selection_page(query, user_id)
        return

    if data.startswith("pick:"):
        try:
            _, deck, idx_s = data.split(":")
            idx = int(idx_s)
        except Exception:
            await query.answer("Carta inválida.", show_alert=True)
            return

        if deck != session["deck"]:
            await query.answer("O baralho atual não bate com a seleção.", show_alert=True)
            return

        cards = get_cards(deck)
        if idx < 0 or idx >= len(cards):
            await query.answer("Carta fora da faixa.", show_alert=True)
            return

        if len(session["cards"]) >= MAX_CARDS:
            await query.answer("Limite máximo de 12 cartas atingido.", show_alert=True)
            return

        session["pending_index"] = idx
        card_name = cards[idx]
        await query.edit_message_text(
            f"Você escolheu: {card_name}\n\nA carta saiu em qual posição?",
            reply_markup=position_keyboard(),
        )
        return

    if data == "back":
        session["pending_index"] = None
        await render_selection_page(query, user_id)
        return

    if data.startswith("pos:"):
        pending_index = session.get("pending_index")
        if pending_index is None:
            await query.answer("Escolha uma carta primeiro.", show_alert=True)
            return

        orientation = data.split(":", 1)[1]
        if orientation not in ("normal", "reversed"):
            await query.answer("Posição inválida.", show_alert=True)
            return

        deck = session["deck"]
        card_name = get_cards(deck)[pending_index]
        session["cards"].append({
            "deck": deck,
            "name": card_name,
            "reversed": orientation == "reversed",
        })
        session["pending_index"] = None

        await query.edit_message_text(
            format_selected_cards(session["cards"]),
            reply_markup=page_keyboard(session),
        )
        return

    if data == "more":
        await render_selection_page(query, user_id)
        return

    if data == "finish":
        cards = session.get("cards", [])
        if not cards:
            await query.answer("Selecione ao menos uma carta.", show_alert=True)
            return

        await query.edit_message_text("🔮 Gerando a interpretação...")
        try:
            result = await asyncio.to_thread(gemini_generate, cards)
        except Exception as exc:
            logger.exception("Falha ao chamar o Gemini")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"Não consegui gerar a leitura agora. Erro: {exc}",
            )
            return

        await send_split_message(query.message.chat_id, "📌 Interpretação por carta:", context)

        cards_result = result.get("cards") or []
        if not cards_result:
            for card in cards:
                await send_card_image(context, query.message.chat_id, card, prefix="Carta tirada")
                msg = (
                    f"• {card['name']} ({'invertida' if card['reversed'] else 'normal'})\n"
                    "Significado: não retornado pela IA.\n"
                    "Ponto positivo: não retornado pela IA.\n"
                    "Ponto negativo: não retornado pela IA."
                )
                await send_split_message(query.message.chat_id, msg, context)
        else:
            for i, item in enumerate(cards_result, 1):
                source_card = cards[i - 1] if i - 1 < len(cards) else {
                    "deck": "tarot",
                    "name": item.get("name", "Carta"),
                    "reversed": False,
                }
                await send_card_image(context, query.message.chat_id, source_card, prefix=f"Carta {i}")
                msg = (
                    f"📍 Carta {i} — {item.get('name', source_card.get('name', 'Carta'))}\n"
                    f"Significado: {item.get('meaning', '')}\n"
                    f"Ponto positivo: {item.get('positive', '')}\n"
                    f"Ponto negativo: {item.get('negative', '')}"
                )
                await send_split_message(query.message.chat_id, msg, context)

        combos = result.get("combinations") or []
        if combos:
            await send_split_message(query.message.chat_id, "🔗 Combinações e possibilidades:", context)
            for combo in combos:
                title = combo.get("title", "Combinação")
                interpretation = combo.get("interpretation", "")
                await send_split_message(query.message.chat_id, f"• {title}\n{interpretation}", context)

        global_view = result.get("global_view", "").strip()
        if global_view:
            await send_split_message(query.message.chat_id, f"🌙 Visão global:\n{global_view}", context)

        clear_session(user_id)
        return

    await query.answer("Ação não reconhecida.", show_alert=True)


async def text_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Use /start para iniciar a tiragem com botões.")


def build_application() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_fallback))
    return app


def main() -> None:
    app = build_application()
    if PUBLIC_URL:
        webhook_url = f"{PUBLIC_URL}/{WEBHOOK_PATH}"
        logger.info("Iniciando em modo webhook: %s", webhook_url)
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=WEBHOOK_PATH,
            webhook_url=webhook_url,
            secret_token=WEBHOOK_SECRET_TOKEN or None,
            drop_pending_updates=True,
            bootstrap_retries=0,
        )
    else:
        logger.warning("WEBHOOK_URL/RAILWAY_PUBLIC_DOMAIN não definido. Iniciando em polling local.")
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
