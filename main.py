import asyncio
import json
import logging
import math
import os
import random
import re
import time
import unicodedata
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types
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
CARDS_PER_PAGE = 8

if not BOT_TOKEN:
    raise RuntimeError("Defina a variável de ambiente BOT_TOKEN.")
if not GEMINI_API_KEY:
    raise RuntimeError("Defina GEMINI_API_KEY ou GOOGLE_API_KEY.")

GENAI_CLIENT = genai.Client(api_key=GEMINI_API_KEY)

# ---------------------------------------------------------------------
# BARALHO ÚNICO: RWS
# ---------------------------------------------------------------------

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
    "O Mundo",
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
    "Rei",
]

TAROT_SUITS = ["Copas", "Paus", "Espadas", "Ouros"]
TAROT_MINOR_BY_SUIT = {suit: [f"{rank} de {suit}" for rank in TAROT_MINOR_RANKS] for suit in TAROT_SUITS}
ALL_CARDS = TAROT_MAJOR + [card for suit in TAROT_SUITS for card in TAROT_MINOR_BY_SUIT[suit]]

# ---------------------------------------------------------------------
# IMAGENS LOCAIS
# ---------------------------------------------------------------------

IMAGE_ROOT = Path(os.getenv("RWS_IMAGE_DIR", "assets/rws"))
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
IMAGE_INDEX: Dict[str, Path] = {}

RWS_MAJOR_STEMS: Dict[str, List[str]] = {
    "O Louco": ["TarotRWS-00-louco", "TarotRWS-00-o-louco"],
    "O Mago": ["TarotRWS-01-mago"],
    "A Sacerdotisa": ["TarotRWS-02-alta-sacerdotisa", "TarotRWS-02-sacerdotisa"],
    "A Imperatriz": ["TarotRWS-03-imperatriz"],
    "O Imperador": ["TarotRWS-04-imperador"],
    "O Hierofante": ["TarotRWS-05-hierofante"],
    "Os Enamorados": ["TarotRWS-06-enamorados"],
    "O Carro": ["TarotRWS-07-carro"],
    "A Força": ["TarotRWS-08-forca"],
    "O Eremita": ["TarotRWS-09-eremita"],
    "A Roda da Fortuna": ["TarotRWS-10-roda", "TarotRWS-10-roda-da-fortuna"],
    "A Justiça": ["TarotRWS-11-justica"],
    "O Enforcado": ["TarotRWS-12-pendurado"],
    "A Morte": ["TarotRWS-13-morte"],
    "A Temperança": ["TarotRWS-14-temperanca"],
    "O Diabo": ["TarotRWS-15-diabo"],
    "A Torre": ["TarotRWS-16-torre"],
    "A Estrela": ["TarotRWS-17-estrela"],
    "A Lua": ["TarotRWS-18-lua"],
    "O Sol": ["TarotRWS-19-sol"],
    "O Julgamento": ["TarotRWS-20-julgamento"],
    "O Mundo": ["TarotRWS-21-mundo"],
}

RANK_TO_NUMBER = {
    "Ás": 1,
    "Dois": 2,
    "Três": 3,
    "Quatro": 4,
    "Cinco": 5,
    "Seis": 6,
    "Sete": 7,
    "Oito": 8,
    "Nove": 9,
    "Dez": 10,
    "Valete": 11,
    "Cavaleiro": 12,
    "Rainha": 13,
    "Rei": 14,
}

# ---------------------------------------------------------------------
# GEMINI
# ---------------------------------------------------------------------

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
                    "negative": {"type": "string"},
                },
                "required": ["name", "meaning", "positive", "negative"],
            },
        },
        "combinations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "interpretation": {"type": "string"},
                },
                "required": ["title", "interpretation"],
            },
        },
        "global_view": {"type": "string"},
        "heart_question": {"type": "string"},
        "answered_energy": {"type": "string"},
    },
    "required": ["cards", "combinations", "global_view", "heart_question", "answered_energy"],
}

SYSTEM_INSTRUCTION = """
Você é um intérprete didático de tarot Rider-Waite-Smith.
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
- Inclua também uma última leitura simbólica com dois campos:
  - heart_question: qual pergunta o coração parece ter levado
  - answered_energy: qual energia de verdade foi respondida pela tiragem
- Nessa leitura final, deixe claro que é uma hipótese simbólica, não uma verdade objetiva.
- Seja sincero, claro, didático e completo.
""".strip()

# ---------------------------------------------------------------------
# ESTADO
# ---------------------------------------------------------------------

SESSIONS: Dict[int, Dict[str, Any]] = {}


def current_ts() -> float:
    return time.monotonic()


def new_ler_state() -> Dict[str, Any]:
    return {
        "step": "idle",
        "category": None,
        "page": 0,
        "pool": [],
        "cards": [],
        "pending_index": None,
        "updated_at": current_ts(),
    }


def new_tirar_state() -> Dict[str, Any]:
    return {
        "step": "idle",
        "quantity": None,
        "drawn": [],
        "updated_at": current_ts(),
    }


def get_user_root(user_id: int) -> Dict[str, Any]:
    if user_id not in SESSIONS:
        SESSIONS[user_id] = {"ler": new_ler_state(), "tirar": new_tirar_state()}
    return SESSIONS[user_id]


def get_mode_state(user_id: int, mode: str) -> Dict[str, Any]:
    return get_user_root(user_id)[mode]


def reset_mode(user_id: int, mode: str) -> Dict[str, Any]:
    root = get_user_root(user_id)
    if mode == "ler":
        root["ler"] = new_ler_state()
    elif mode == "tirar":
        root["tirar"] = new_tirar_state()
    else:
        raise ValueError(f"Modo inválido: {mode}")
    return root[mode]


def clear_all(user_id: int) -> None:
    SESSIONS.pop(user_id, None)

# ---------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------


def normalize_public_url(raw: str) -> str:
    raw = (raw or "").strip().rstrip("/")
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    return f"https://{raw}"


PUBLIC_URL = normalize_public_url(WEBHOOK_URL)


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def build_image_index() -> Dict[str, Path]:
    index: Dict[str, Path] = {}
    if not IMAGE_ROOT.exists():
        logger.warning("Pasta de imagens não encontrada: %s", IMAGE_ROOT)
        return index
    for file_path in IMAGE_ROOT.rglob("*"):
        if file_path.is_file() and file_path.suffix.lower() in IMAGE_EXTENSIONS:
            index[slugify(file_path.stem)] = file_path
    logger.info("Índice de imagens carregado: %d arquivos em %s", len(index), IMAGE_ROOT)
    return index


IMAGE_INDEX = build_image_index()


def cards_pages_for_pool(pool: List[int]) -> int:
    return max(1, math.ceil(len(pool) / CARDS_PER_PAGE))


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


async def send_long_message(chat_id: int, text: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    for part in split_telegram_text(text):
        await context.bot.send_message(chat_id=chat_id, text=part)


def render_card_photo_bytes(image_path: Path, reversed_card: bool) -> BytesIO:
    with Image.open(image_path) as img:
        img = ImageOps.exif_transpose(img)
        if reversed_card:
            img = img.rotate(180, expand=True)
        output = BytesIO()
        img.save(output, format="PNG")
        output.seek(0)
        return output


def major_image_candidates(card_name: str) -> List[str]:
    return RWS_MAJOR_STEMS.get(card_name, [f"TarotRWS-{slugify(card_name)}"])


def minor_image_candidates(card_name: str) -> List[str]:
    match = re.match(r"^(Ás|Dois|Três|Quatro|Cinco|Seis|Sete|Oito|Nove|Dez|Valete|Cavaleiro|Rainha|Rei) de (Copas|Paus|Espadas|Ouros)$", card_name)
    if not match:
        return [f"TarotRWS-{slugify(card_name)}"]
    rank, suit = match.groups()
    num = RANK_TO_NUMBER.get(rank)
    if not num:
        return [f"TarotRWS-{slugify(card_name)}"]
    return [
        f"TarotRWS-{suit}-{num:02d}",
        f"TarotRWS-{suit}-{num:02d}-{slugify(card_name)}",
        f"TarotRWS-{suit.lower()}-{num:02d}",
    ]


def find_card_image_path(card_name: str) -> Optional[Path]:
    candidates = major_image_candidates(card_name) if card_name in TAROT_MAJOR else minor_image_candidates(card_name)
    for stem in candidates:
        path = IMAGE_INDEX.get(slugify(stem))
        if path:
            return path
    card_slug = slugify(card_name)
    for stem, path in IMAGE_INDEX.items():
        if card_slug and card_slug in stem:
            return path
    return None


async def send_card_image(context: ContextTypes.DEFAULT_TYPE, chat_id: int, card: Dict[str, Any], prefix: str = "Carta") -> bool:
    image_path = find_card_image_path(card.get("name", ""))
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

# ---------------------------------------------------------------------
# CATEGORIAS / UI
# ---------------------------------------------------------------------


def deck_label() -> str:
    return "Tarot Rider-Waite-Smith"


def deck_cards() -> List[str]:
    return ALL_CARDS


def tarot_category_items() -> List[Dict[str, Any]]:
    return [
        {"id": "major", "label": "🔺 Arcanos Maiores", "indices": list(range(0, 22))},
        {"id": "cups", "label": "💧 Copas", "indices": list(range(22, 36))},
        {"id": "wands", "label": "🔥 Paus", "indices": list(range(36, 50))},
        {"id": "swords", "label": "⚔️ Espadas", "indices": list(range(50, 64))},
        {"id": "pentacles", "label": "🪙 Ouros", "indices": list(range(64, 78))},
        {"id": "minor_all", "label": "🧩 Arcanos Menores", "indices": list(range(22, 78))},
        {"id": "all", "label": "✨ Baralho Completo", "indices": list(range(0, 78))},
    ]


def category_label(category_id: Optional[str]) -> str:
    if not category_id:
        return "—"
    for item in tarot_category_items():
        if item["id"] == category_id:
            return item["label"]
    return category_id


def category_indices(category_id: str) -> List[int]:
    for item in tarot_category_items():
        if item["id"] == category_id:
            return list(item["indices"])
    return list(range(len(deck_cards())))


def welcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📖 Ler", callback_data="goto:ler")],
            [InlineKeyboardButton("🎲 Tirar", callback_data="goto:tirar")],
            [InlineKeyboardButton("♻️ Reset", callback_data="goto:reset")],
        ]
    )


def category_keyboard() -> InlineKeyboardMarkup:
    items = tarot_category_items()
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for item in items:
        row.append(InlineKeyboardButton(item["label"], callback_data=f"ler:cat:{item['id']}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🏠 Início", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def position_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⬆️ Normal", callback_data="ler:pos:normal")],
            [InlineKeyboardButton("⬇️ Invertida", callback_data="ler:pos:reversed")],
            [InlineKeyboardButton("↩️ Voltar", callback_data="ler:back")],
        ]
    )


def ler_card_pool(session: Dict[str, Any]) -> List[int]:
    return session.get("pool") or []


def ler_page_keyboard(session: Dict[str, Any]) -> InlineKeyboardMarkup:
    pool = ler_card_pool(session)
    page = session["page"]
    start = page * CARDS_PER_PAGE
    end = min(len(pool), start + CARDS_PER_PAGE)

    rows: List[List[InlineKeyboardButton]] = []
    cards = deck_cards()
    for pool_pos in range(start, end):
        card_index = pool[pool_pos]
        rows.append([InlineKeyboardButton(cards[card_index], callback_data=f"ler:pick:{pool_pos}")])

    nav_row: List[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"ler:nav:{page - 1}"))
    if page < cards_pages_for_pool(pool) - 1:
        nav_row.append(InlineKeyboardButton("Próxima ➡️", callback_data=f"ler:nav:{page + 1}"))
    if nav_row:
        rows.append(nav_row)

    footer: List[InlineKeyboardButton] = [InlineKeyboardButton("🧭 Categorias", callback_data="ler:cats")]
    if session["cards"] and len(session["cards"]) < MAX_CARDS:
        footer.append(InlineKeyboardButton("➕ Adicionar outra", callback_data="ler:more"))
    if session["cards"]:
        footer.append(InlineKeyboardButton("✅ Finalizar", callback_data="ler:finish"))
    rows.append(footer)

    return InlineKeyboardMarkup(rows)


def ler_selection_text(session: Dict[str, Any]) -> str:
    pool = ler_card_pool(session)
    page = session["page"]
    total_pages = cards_pages_for_pool(pool)
    selected = session["cards"]

    lines = [
        f"Baralho: {deck_label()}",
        f"Categoria: {category_label(session.get('category'))}",
        f"Página: {page + 1}/{total_pages}",
        f"Selecionadas: {len(selected)}/{MAX_CARDS}",
        "",
        "Escolha uma carta pelos botões abaixo.",
    ]

    if selected:
        lines.append("")
        lines.append("Tiragem atual:")
        for i, card in enumerate(selected, 1):
            pos = "invertida" if card["reversed"] else "normal"
            lines.append(f"{i}. {card['name']} ({pos})")

    return "\n".join(lines)


def tirar_start_text() -> str:
    return (
        f"Você escolheu: {deck_label()}\n\n"
        f"Agora me diga quantas cartas deseja tirar.\n"
        f"Envie um número de 1 até {MAX_CARDS}."
    )


def normalize_number_from_text(text: str) -> Optional[int]:
    if not text:
        return None
    match = re.search(r"\b(\d{1,2})\b", text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None

# ---------------------------------------------------------------------
# GEMINI
# ---------------------------------------------------------------------


def build_prompt(cards: List[Dict[str, Any]], mode: str) -> str:
    items: List[str] = []
    for i, card in enumerate(cards, 1):
        pos = "invertida" if card.get("reversed") else "normal"
        items.append(f"{i}. {card['name']} ({pos})")

    cards_block = "\n".join(items)

    return f"""
Você recebeu uma tiragem no modo {mode} usando {deck_label()}.

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


def safe_json_from_text(text: str) -> Dict[str, Any]:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```json\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^```\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        data = {
            "cards": [],
            "combinations": [],
            "global_view": cleaned or "A IA não retornou JSON válido.",
            "heart_question": "A leitura não retornou uma formulação confiável da pergunta do coração.",
            "answered_energy": "A leitura não retornou uma formulação confiável da energia respondida.",
        }
    if not isinstance(data, dict):
        data = {
            "cards": [],
            "combinations": [],
            "global_view": str(data),
            "heart_question": "Não foi possível estruturar a pergunta do coração.",
            "answered_energy": "Não foi possível estruturar a energia respondida.",
        }
    data.setdefault("cards", [])
    data.setdefault("combinations", [])
    data.setdefault("global_view", "")
    data.setdefault("heart_question", "")
    data.setdefault("answered_energy", "")
    return data


def gemini_generate(cards: List[Dict[str, Any]], mode: str) -> Dict[str, Any]:
    prompt = build_prompt(cards, mode=mode)
    response = GENAI_CLIENT.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.7,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
        ),
    )

    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, dict):
        data = parsed
    else:
        data = safe_json_from_text(getattr(response, "text", "") or "")

    if not isinstance(data, dict):
        data = safe_json_from_text(str(data))

    data.setdefault("cards", [])
    data.setdefault("combinations", [])
    data.setdefault("global_view", "")
    data.setdefault("heart_question", "")
    data.setdefault("answered_energy", "")
    return data


async def send_interpretation(chat_id: int, context: ContextTypes.DEFAULT_TYPE, cards: List[Dict[str, Any]], result: Dict[str, Any], mode: str) -> None:
    await send_long_message(chat_id, "📍 Leitura por carta:", context)

    cards_result = result.get("cards") or []
    if cards_result:
        for i, item in enumerate(cards_result, 1):
            source_card = cards[i - 1] if i - 1 < len(cards) else {"name": item.get("name", "Carta"), "reversed": False}
            await send_card_image(context, chat_id, source_card, prefix=f"Carta {i}")
            msg = (
                f"Carta {i} — {item.get('name', 'Carta')}\n"
                f"Significado: {item.get('meaning', '')}\n"
                f"Ponto positivo: {item.get('positive', '')}\n"
                f"Ponto negativo: {item.get('negative', '')}"
            )
            await send_long_message(chat_id, msg, context)
    else:
        for i, card in enumerate(cards, 1):
            await send_card_image(context, chat_id, card, prefix=f"Carta {i}")
            pos = "invertida" if card.get("reversed") else "normal"
            msg = (
                f"Carta {i} — {card['name']} ({pos})\n"
                "Significado: não retornado pela IA.\n"
                "Ponto positivo: não retornado pela IA.\n"
                "Ponto negativo: não retornado pela IA."
            )
            await send_long_message(chat_id, msg, context)

    combos = result.get("combinations") or []
    if combos:
        await send_long_message(chat_id, "🔗 Combinações e possibilidades:", context)
        for combo in combos:
            title = combo.get("title", "Combinação")
            interpretation = combo.get("interpretation", "")
            await send_long_message(chat_id, f"• {title}\n{interpretation}", context)

    global_view = (result.get("global_view") or "").strip()
    if global_view:
        await send_long_message(chat_id, f"🌙 Visão global:\n{global_view}", context)

    heart_question = (result.get("heart_question") or "").strip()
    answered_energy = (result.get("answered_energy") or "").strip()
    if heart_question or answered_energy:
        final_msg = "💗 Leitura do coração:\n"
        if heart_question:
            final_msg += f"Pergunta do coração: {heart_question}\n"
        if answered_energy:
            final_msg += f"Energia respondida: {answered_energy}"
        await send_long_message(chat_id, final_msg.strip(), context)

    await send_long_message(chat_id, "✨ Tiragem finalizada. Use /ler ou /tirar para uma nova leitura.", context)

# ---------------------------------------------------------------------
# FLUXO /LER
# ---------------------------------------------------------------------


async def render_ler_page(query, user_id: int) -> None:
    session = get_mode_state(user_id, "ler")
    text = ler_selection_text(session)
    try:
        await query.edit_message_text(text, reply_markup=ler_page_keyboard(session))
    except BadRequest as exc:
        if "Message is not modified" not in str(exc):
            raise


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    clear_all(user_id)
    await update.message.reply_text(
        "🔮 *Tarot Rider-Waite-Smith*\n\nEscolha uma opção:",
        reply_markup=welcome_keyboard(),
        parse_mode="Markdown",
    )


async def ler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    reset_mode(user_id, "ler")
    await update.message.reply_text(
        f"🃏 *{deck_label()}*\n\nEscolha a categoria:",
        reply_markup=category_keyboard(),
        parse_mode="Markdown",
    )


async def start_tirar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    tirar_state = reset_mode(user_id, "tirar")
    tirar_state["step"] = "awaiting_quantity"
    tirar_state["updated_at"] = current_ts()

    args = (update.message.text or "").split(maxsplit=1)
    if len(args) > 1:
        quantity = normalize_number_from_text(args[1])
        if quantity is not None:
            await process_tirar_quantity(update, context, quantity)
            return

    await update.message.reply_text(tirar_start_text(), reply_markup=welcome_keyboard())


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_all(update.effective_user.id)
    await update.message.reply_text("♻️ Sessões limpas. Use /start para recomeçar.")


async def process_tirar_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE, quantity: int) -> bool:
    user_id = update.effective_user.id
    tirar_state = get_mode_state(user_id, "tirar")

    if quantity < 1 or quantity > MAX_CARDS:
        await update.message.reply_text(f"Digite um número válido entre 1 e {MAX_CARDS}.")
        return True

    tirar_state["quantity"] = quantity
    tirar_state["drawn"] = []
    tirar_state["step"] = "ready"
    tirar_state["updated_at"] = current_ts()

    cards = random.sample(deck_cards(), quantity)
    drawn = [{"name": card, "reversed": random.choice([True, False])} for card in cards]
    tirar_state["drawn"] = drawn

    lines = ["🎲 Sorteio concluído.", "", "Posições sorteadas:"]
    for item in drawn:
        lines.append(f"• {item['name']} ({'invertida' if item['reversed'] else 'normal'})")

    await update.message.reply_text("\n".join(lines))
    await update.message.reply_text("🔮 Gerando a interpretação...")

    try:
        result = await asyncio.to_thread(gemini_generate, drawn, "tirar")
    except Exception as exc:
        logger.exception("Falha ao chamar o Gemini no /tirar")
        await update.message.reply_text(f"Não consegui gerar a leitura agora. Erro: {exc}")
        return True

    await send_interpretation(update.effective_chat.id, context, drawn, result, mode="tirar")
    reset_mode(user_id, "tirar")
    return True


async def text_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    handled = await process_quantity_message(update, context)
    if handled:
        return

    await update.message.reply_text("Use /start, /ler ou /tirar para iniciar.")


async def process_quantity_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    tirar_state = get_mode_state(user_id, "tirar")
    if tirar_state.get("step") != "awaiting_quantity":
        return False

    quantity = normalize_number_from_text(update.message.text or "")
    if quantity is None:
        await update.message.reply_text(f"Envie um número de 1 até {MAX_CARDS}.")
        return True

    return await process_tirar_quantity(update, context, quantity)

# ---------------------------------------------------------------------
# CALLBACKS
# ---------------------------------------------------------------------


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await query.answer()
    user_id = query.from_user.id
    data = query.data or ""
    root = get_user_root(user_id)

    if data == "home":
        clear_all(user_id)
        await query.edit_message_text(
            "🔮 *Tarot Rider-Waite-Smith*\n\nEscolha uma opção:",
            reply_markup=welcome_keyboard(),
            parse_mode="Markdown",
        )
        return

    if data == "goto:ler":
        reset_mode(user_id, "ler")
        await query.edit_message_text(
            f"🃏 *{deck_label()}*\n\nEscolha a categoria:",
            reply_markup=category_keyboard(),
            parse_mode="Markdown",
        )
        return

    if data == "goto:tirar":
        reset_mode(user_id, "tirar")
        tirar_state = get_mode_state(user_id, "tirar")
        tirar_state["step"] = "awaiting_quantity"
        tirar_state["updated_at"] = current_ts()
        await query.edit_message_text(tirar_start_text(), reply_markup=welcome_keyboard())
        return

    if data == "goto:reset":
        clear_all(user_id)
        await query.edit_message_text("♻️ Sessões limpas. Use /start para recomeçar.")
        return

    # /LER
    if data.startswith("ler:cat:"):
        category_id = data.split(":", 2)[2]
        ler_state = reset_mode(user_id, "ler")
        ler_state.update(
            {
                "step": "select_card",
                "category": category_id,
                "page": 0,
                "pool": category_indices(category_id),
                "cards": [],
                "pending_index": None,
                "updated_at": current_ts(),
            }
        )
        await query.edit_message_text(
            f"Baralho: {deck_label()}\nCategoria: {category_label(category_id)}\n\n{ler_selection_text(ler_state)}",
            reply_markup=ler_page_keyboard(ler_state),
        )
        return

    if data == "ler:cats":
        ler_state = root["ler"]
        if not ler_state.get("category"):
            await query.answer("Escolha uma categoria primeiro.", show_alert=True)
            return
        await query.edit_message_text(
            f"🃏 {deck_label()}\n\nEscolha a categoria:",
            reply_markup=category_keyboard(),
        )
        return

    if data.startswith("ler:nav:"):
        ler_state = root["ler"]
        if not ler_state.get("category"):
            await query.answer("Use /ler primeiro.", show_alert=True)
            return
        try:
            page = int(data.split(":", 2)[2])
        except ValueError:
            await query.answer("Página inválida.", show_alert=True)
            return
        ler_state["page"] = max(0, min(page, cards_pages_for_pool(ler_state["pool"]) - 1))
        ler_state["updated_at"] = current_ts()
        await render_ler_page(query, user_id)
        return

    if data.startswith("ler:pick:"):
        ler_state = root["ler"]
        if not ler_state.get("category"):
            await query.answer("Use /ler primeiro.", show_alert=True)
            return
        try:
            pool_pos = int(data.split(":", 2)[2])
        except ValueError:
            await query.answer("Carta inválida.", show_alert=True)
            return
        pool = ler_card_pool(ler_state)
        if pool_pos < 0 or pool_pos >= len(pool):
            await query.answer("Carta fora da faixa.", show_alert=True)
            return
        if len(ler_state["cards"]) >= MAX_CARDS:
            await query.answer("Limite máximo de 12 cartas atingido.", show_alert=True)
            return
        card_index = pool[pool_pos]
        cards = deck_cards()
        ler_state["pending_index"] = card_index
        ler_state["updated_at"] = current_ts()
        await query.edit_message_text(
            f"Você escolheu: {cards[card_index]}\n\nA carta saiu em qual posição?",
            reply_markup=position_keyboard(),
        )
        return

    if data == "ler:back":
        ler_state = root["ler"]
        ler_state["pending_index"] = None
        ler_state["updated_at"] = current_ts()
        if not ler_state.get("category"):
            await query.answer("Use /ler primeiro.", show_alert=True)
            return
        await render_ler_page(query, user_id)
        return

    if data.startswith("ler:pos:"):
        ler_state = root["ler"]
        pending_index = ler_state.get("pending_index")
        if pending_index is None:
            await query.answer("Escolha uma carta primeiro.", show_alert=True)
            return
        orientation = data.split(":", 2)[2]
        if orientation not in ("normal", "reversed"):
            await query.answer("Posição inválida.", show_alert=True)
            return
        if len(ler_state["cards"]) >= MAX_CARDS:
            await query.answer("Limite máximo de 12 cartas atingido.", show_alert=True)
            return
        card_name = deck_cards()[pending_index]
        ler_state["cards"].append({"name": card_name, "reversed": orientation == "reversed"})
        ler_state["pending_index"] = None
        ler_state["step"] = "select_card"
        ler_state["updated_at"] = current_ts()
        await query.edit_message_text(
            ler_selection_text(ler_state),
            reply_markup=ler_page_keyboard(ler_state),
        )
        return

    if data == "ler:more":
        ler_state = root["ler"]
        if not ler_state.get("category"):
            await query.answer("Use /ler primeiro.", show_alert=True)
            return
        await render_ler_page(query, user_id)
        return

    if data == "ler:finish":
        ler_state = root["ler"]
        cards = ler_state.get("cards", [])
        if not cards:
            await query.answer("Selecione ao menos uma carta.", show_alert=True)
            return

        await query.edit_message_text("🔮 Gerando a interpretação...")
        try:
            result = await asyncio.to_thread(gemini_generate, cards, "ler")
        except Exception as exc:
            logger.exception("Falha ao chamar o Gemini no /ler")
            await context.bot.send_message(chat_id=query.message.chat.id, text=f"Não consegui gerar a leitura agora. Erro: {exc}")
            return

        await send_interpretation(query.message.chat.id, context, cards, result, mode="ler")
        reset_mode(user_id, "ler")
        return

    await query.answer("Ação não reconhecida.", show_alert=True)

# ---------------------------------------------------------------------
# APP
# ---------------------------------------------------------------------


def build_application() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ler", ler))
    app.add_handler(CommandHandler("tirar", start_tirar))
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
