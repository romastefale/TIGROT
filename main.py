import asyncio
import html
import json
import logging
import os
import secrets
import re
import time
import unicodedata
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import redis.asyncio as redis
from PIL import Image, ImageDraw, ImageFont, ImageOps
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Message, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


_TELEGRAM_BOT_TOKEN_RE = re.compile(r"bot\d{5,}:[A-Za-z0-9_-]{20,}")
_ENV_SECRET_RE = re.compile(
    r"(?i)\b(BOT_TOKEN|TELEGRAM_BOT_TOKEN|GEMINI_API_KEY|GOOGLE_API_KEY|REDIS_URL)=([^\s]+)"
)
_REDIS_AUTH_RE = re.compile(r"(?i)(redis(?:s)?://)([^@\s/]+@)")


def redact_sensitive(value: Any) -> str:
    text = str(value)
    text = _TELEGRAM_BOT_TOKEN_RE.sub("bot<redacted>", text)
    text = _ENV_SECRET_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text)
    text = _REDIS_AUTH_RE.sub(r"\1<redacted>@", text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_sensitive(super().format(record))


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_sensitive(record.getMessage())
            record.args = ()
        except Exception:
            record.msg = "<log message redaction failed>"
            record.args = ()
        return True


logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
for _handler in logging.getLogger().handlers:
    _handler.addFilter(RedactingFilter())
    _handler.setFormatter(RedactingFormatter(LOG_FORMAT))

# Evita que bibliotecas HTTP imprimam URLs completas da API do Telegram.
# A camada de redaction acima permanece como defesa em profundidade.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("tarot_bot")

BOT_TOKEN = (os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
OWNER_IDS_RAW = (
    os.getenv("OWNER_IDS")
    or os.getenv("OWNER_ID")
    or os.getenv("BOT_OWNER_ID")
    or ""
).strip()

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
RUN_BUSY_MAX_SECONDS = int(os.getenv("RUN_BUSY_MAX_SECONDS", "1800"))

MAX_CARDS = 12
CARDS_PER_PAGE = 8

SESSIONS: Dict[int, Dict[str, Any]] = {}
RUNNING_USERS: set[int] = set()
RNG = secrets.SystemRandom()

IMAGE_ROOT = Path(os.getenv("RWS_IMAGE_DIR", "assets/rws-png"))
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

redis_client = redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None


def _parse_owner_ids(raw: str) -> set[int]:
    owner_ids: set[int] = set()
    for part in re.split(r"[,;\s]+", raw or ""):
        part = part.strip()
        if not part:
            continue
        try:
            owner_ids.add(int(part))
        except ValueError:
            logger.warning("OWNER_ID inválido ignorado: %s", part)
    return owner_ids


OWNER_IDS = _parse_owner_ids(OWNER_IDS_RAW)


def user_role(user_id: Optional[int]) -> str:
    if user_id is None:
        return "unknown_user"
    if not OWNER_IDS:
        return "owner_config_missing"
    return "owner" if user_id in OWNER_IDS else "non_owner"


def safe_username(username: Optional[str]) -> str:
    return (username or "sem_username").strip().lstrip("@") or "sem_username"


def safe_action_data(data: Optional[str], limit: int = 160) -> str:
    clean = redact_sensitive(data or "")
    clean = clean.replace("\n", " ").replace("\r", " ").strip()
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def log_user_action(action: str, update: Update, *, data: Optional[str] = None) -> None:
    user = update.effective_user
    chat = update.effective_chat
    logger.info(
        "USER_AUDIT | action=%s | uid=%s | username=@%s | role=%s | chat_id=%s | chat_type=%s | data=%s",
        action,
        user.id if user else None,
        safe_username(user.username if user else None),
        user_role(user.id if user else None),
        chat.id if chat else None,
        chat.type if chat else None,
        safe_action_data(data),
    )


def log_callback_action(update: Update, data: Optional[str]) -> None:
    q = update.callback_query
    msg = q.message if q else None
    chat = msg.chat if msg else update.effective_chat
    user = q.from_user if q else update.effective_user
    logger.info(
        "CALLBACK_AUDIT | uid=%s | username=@%s | role=%s | chat_id=%s | chat_type=%s | message_id=%s | data=%s",
        user.id if user else None,
        safe_username(user.username if user else None),
        user_role(user.id if user else None),
        chat.id if chat else None,
        chat.type if chat else None,
        msg.message_id if msg else None,
        safe_action_data(data),
    )


async def safe_edit_message_text(q, text: str, **kwargs) -> bool:
    try:
        await q.edit_message_text(text, **kwargs)
        return True
    except Exception:
        logger.exception("SAFE_EDIT_MESSAGE_TEXT_FAILED | data=%s", safe_action_data(getattr(q, "data", None)))
        try:
            await q.message.reply_text(text, **kwargs)
            return True
        except Exception:
            logger.exception("SAFE_EDIT_FALLBACK_REPLY_FAILED")
            return False


def valid_group(group: Optional[str]) -> bool:
    return group == "major" or group in SUITS


def cards_for_group(group: str) -> List[str]:
    if group == "major":
        return TAROT_MAJOR
    if group in MINOR:
        return MINOR[group]
    return []


def normalize_page(group: str, page: int) -> Optional[int]:
    cards = cards_for_group(group)
    if not cards:
        return None
    pages = max(1, (len(cards) + CARDS_PER_PAGE - 1) // CARDS_PER_PAGE)
    if page < 0 or page >= pages:
        return None
    return page


def _busy_is_stale(session: Dict[str, Any]) -> bool:
    if not session.get("busy"):
        return False
    try:
        busy_since = float(session.get("busy_since") or 0)
    except Exception:
        busy_since = 0
    return bool(busy_since and time.time() - busy_since > RUN_BUSY_MAX_SECONDS)


async def refresh_busy_state(uid: int, session: Dict[str, Any]) -> Dict[str, Any]:
    if _busy_is_stale(session):
        logger.warning("RUN_BUSY_STALE_CLEARED | uid=%s", uid)
        RUNNING_USERS.discard(uid)
        session["busy"] = False
        session["busy_since"] = None
        await save_session(uid, session)
    return session


def is_flow_busy(uid: int, session: Dict[str, Any]) -> bool:
    return uid in RUNNING_USERS or bool(session.get("busy"))


async def begin_user_run(uid: int, session: Dict[str, Any]) -> bool:
    if is_flow_busy(uid, session):
        return False
    RUNNING_USERS.add(uid)
    session["busy"] = True
    session["busy_since"] = time.time()
    await save_session(uid, session)
    return True


async def end_user_run(uid: int, *, delete: bool = False) -> None:
    RUNNING_USERS.discard(uid)
    if delete:
        await delete_session(uid)
        return
    session = await load_session(uid)
    session["busy"] = False
    session["busy_since"] = None
    await save_session(uid, session)


async def send_flow_error(chat_id: int, ctx: ContextTypes.DEFAULT_TYPE, *, reply_to_message_id: Optional[int] = None) -> None:
    kwargs: Dict[str, Any] = {}
    if reply_to_message_id is not None:
        kwargs["reply_to_message_id"] = reply_to_message_id
    await ctx.bot.send_message(
        chat_id=chat_id,
        text=(
            "⚠️ <b>Não consegui concluir esta etapa.</b>\n\n"
            "A sessão foi liberada para evitar travamento. Você pode tentar novamente com /tirar ou /ler."
        ),
        parse_mode="HTML",
        **kwargs,
    )


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

# Os arquivos reais em assets/rws-png/ usam estes nomes (slug do nome do
# arquivo). Os maiores são "NN-slug" (ex.: 00-louco, 08-força, 20-julgamento)
# e os menores são "Naipe-NN" (ex.: Copas-01). O find_image normaliza via
# slugify, então acentos (força/temperança) casam igual.
_RWS_MAJOR_SLUGS = [
    "00-louco", "01-mago", "02-alta-sacerdotisa", "03-imperatriz", "04-imperador",
    "05-hierofante", "06-enamorados", "07-carro", "08-forca", "09-eremita",
    "10-roda", "11-justica", "12-pendurado", "13-morte", "14-temperanca",
    "15-diabo", "16-torre", "17-estrela", "18-lua", "19-sol",
    "20-julgamento", "21-mundo",
]
RWS_MAJOR_IMAGE_STEMS = dict(zip(TAROT_MAJOR, _RWS_MAJOR_SLUGS))

RWS_MINOR_IMAGE_STEMS = {
    f"{rank} de {suit}": f"{suit}-{idx:02d}"
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


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def _draw_centered(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> None:
    x1, y1, x2, y2 = box
    tw, th = _text_size(draw, text, font)
    draw.text((x1 + (x2 - x1 - tw) / 2, y1 + (y2 - y1 - th) / 2), text, font=font, fill=fill)


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    *,
    max_lines: int = 2,
) -> List[str]:
    words = str(text or "").split()
    if not words:
        return [""]
    lines: List[str] = []
    current = ""
    for word in words:
        test = word if not current else f"{current} {word}"
        if _text_size(draw, test, font)[0] <= max_width:
            current = test
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if words and " ".join(lines).strip() != " ".join(words).strip() and lines:
        while lines[-1] and _text_size(draw, lines[-1] + "…", font)[0] > max_width:
            lines[-1] = lines[-1][:-1].rstrip()
        lines[-1] = (lines[-1].rstrip() + "…") if lines[-1] else "…"
    return lines


def _mosaic_columns(n: int) -> int:
    if n <= 1:
        return 1
    if n <= 3:
        return n
    if n <= 9:
        return 3
    return 4


def _open_card_for_mosaic(card: Dict[str, Any], size: tuple[int, int]) -> Image.Image:
    path = find_image(str(card.get("name") or ""))
    if path:
        try:
            with Image.open(path) as raw:
                img = ImageOps.exif_transpose(raw).convert("RGB")
                if card.get("rev"):
                    img = img.rotate(180, expand=True)
                return ImageOps.contain(img, size, Image.LANCZOS)
        except Exception:
            logger.warning("MOSAIC_CARD_OPEN_FAILED | card=%s | path=%s", card.get("name"), path, exc_info=True)

    placeholder = Image.new("RGB", size, (31, 25, 52))
    d = ImageDraw.Draw(placeholder)
    d.rounded_rectangle((8, 8, size[0] - 8, size[1] - 8), radius=18, outline=(135, 106, 196), width=3)
    font = _font(22, bold=True)
    _draw_centered(d, (18, 18, size[0] - 18, size[1] - 18), "sem imagem", font, (244, 241, 255))
    return placeholder


def render_tiragem_mosaic(cards: List[Dict[str, Any]], tiragem_name: Optional[str]) -> BytesIO:
    if not cards:
        raise ValueError("não há cartas para montar mosaico")

    columns = _mosaic_columns(len(cards))
    rows = (len(cards) + columns - 1) // columns
    margin = 44
    gap = 24
    tile_w = 228
    card_h = 330
    label_h = 92
    tile_h = card_h + label_h
    header_h = 150
    footer_h = 68
    width = margin * 2 + columns * tile_w + (columns - 1) * gap
    height = margin + header_h + rows * tile_h + (rows - 1) * gap + footer_h

    bg = Image.new("RGB", (width, height), (11, 10, 26))
    draw = ImageDraw.Draw(bg)
    title_font = _font(38, bold=True)
    meta_font = _font(22, bold=True)
    small_font = _font(18)
    card_name_font = _font(18, bold=True)
    pos_font = _font(16)

    draw.rounded_rectangle((margin, margin, width - margin, margin + header_h - 18), radius=28, fill=(24, 20, 42))
    title = tiragem_name or "Tiragem manual"
    draw.text((margin + 28, margin + 28), "Resumo visual da tiragem", font=title_font, fill=(244, 241, 255))
    draw.text((margin + 30, margin + 84), f"{title} • {len(cards)} carta{'s' if len(cards) != 1 else ''}", font=meta_font, fill=(181, 140, 254))

    top = margin + header_h
    for idx, card in enumerate(cards):
        row = idx // columns
        col = idx % columns
        x = margin + col * (tile_w + gap)
        y = top + row * (tile_h + gap)
        draw.rounded_rectangle((x, y, x + tile_w, y + tile_h), radius=24, fill=(22, 19, 39), outline=(62, 48, 91), width=2)

        card_img = _open_card_for_mosaic(card, (tile_w - 28, card_h - 24))
        ix = x + (tile_w - card_img.width) // 2
        iy = y + 14
        bg.paste(card_img, (ix, iy))

        badge = f"{idx + 1}"
        draw.ellipse((x + 12, y + 12, x + 54, y + 54), fill=(181, 140, 254))
        _draw_centered(draw, (x + 12, y + 12, x + 54, y + 54), badge, meta_font, (11, 10, 26))

        label_top = y + card_h
        name_lines = _wrap_text(draw, str(card.get("name") or "—"), card_name_font, tile_w - 24, max_lines=2)
        text_y = label_top + 10
        for line in name_lines:
            tw, _ = _text_size(draw, line, card_name_font)
            draw.text((x + (tile_w - tw) / 2, text_y), line, font=card_name_font, fill=(244, 241, 255))
            text_y += 22
        pos = "invertida" if card.get("rev") else "normal"
        pos_fill = (255, 199, 95) if card.get("rev") else (63, 224, 166)
        tw, _ = _text_size(draw, pos, pos_font)
        draw.text((x + (tile_w - tw) / 2, y + tile_h - 30), pos, font=pos_font, fill=pos_fill)

    footer_y = height - footer_h + 10
    footer = "TIGROT • toque nesta imagem para voltar ao resumo da leitura"
    tw, _ = _text_size(draw, footer, small_font)
    draw.text(((width - tw) / 2, footer_y), footer, font=small_font, fill=(168, 163, 198))

    out = BytesIO()
    bg.save(out, "PNG")
    out.seek(0)
    return out


def mosaic_caption(cards: List[Dict[str, Any]], tiragem_name: Optional[str]) -> str:
    title = html.escape(tiragem_name or "Tiragem manual")
    return (
        "🖼️ <b>Resumo visual da tiragem</b>\n"
        f"🎴 <b>{title}</b>\n"
        f"🃏 {len(cards)} carta{'s' if len(cards) != 1 else ''} revelada{'s' if len(cards) != 1 else ''}\n"
        "👇 A interpretação virá respondendo esta imagem."
    )


async def send_tiragem_mosaic(
    chat_id: int,
    ctx: ContextTypes.DEFAULT_TYPE,
    cards: List[Dict[str, Any]],
    tiragem_name: Optional[str],
) -> Optional[int]:
    try:
        img = render_tiragem_mosaic(cards, tiragem_name)
        sent = await ctx.bot.send_photo(
            chat_id=chat_id,
            photo=InputFile(img, filename="tiragem-mosaico.png"),
            caption=mosaic_caption(cards, tiragem_name),
            parse_mode="HTML",
        )
        return sent.message_id
    except Exception:
        logger.exception("MOSAIC_SEND_FAILED")
        try:
            sent = await ctx.bot.send_message(
                chat_id=chat_id,
                text=(
                    "🖼️ <b>Resumo visual indisponível.</b>\n"
                    "Vou manter a leitura ancorada neste resumo textual.\n\n"
                    f"{html.escape(selected_cards_text(cards, max(MAX_CARDS, len(cards))))}"
                ),
                parse_mode="HTML",
            )
            return sent.message_id
        except Exception:
            logger.exception("MOSAIC_TEXT_FALLBACK_FAILED")
            return None


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


def split_text_blocks(text: str, limit: int = 3200) -> List[str]:
    """Divide respostas longas respeitando parágrafos/blocos sempre que possível.

    O Telegram aceita mensagens maiores, mas usamos margem para cabeçalhos e
    parse HTML. Primeiro preserva blocos separados por linha em branco; se um
    bloco isolado estourar, cai no split_text tradicional.
    """
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return []

    raw_blocks = [b.strip() for b in re.split(r"\n{2,}", text) if b.strip()]
    if not raw_blocks:
        return split_text(text, limit)

    parts: List[str] = []
    current = ""
    for block in raw_blocks:
        candidates = split_text(block, limit) if len(block) > limit else [block]
        for candidate in candidates:
            if not current:
                current = candidate
                continue
            merged = f"{current}\n\n{candidate}"
            if len(merged) <= limit:
                current = merged
            else:
                parts.append(current.strip())
                current = candidate
    if current.strip():
        parts.append(current.strip())
    return parts


async def send_split_message(
    chat_id: int,
    text: str,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    reply_to_message_id: Optional[int] = None,
) -> None:
    for part in split_text(text):
        kwargs: Dict[str, Any] = {}
        if reply_to_message_id is not None:
            kwargs["reply_to_message_id"] = reply_to_message_id
        await context.bot.send_message(
            chat_id=chat_id,
            text=prepare_telegram_html(part),
            parse_mode="HTML",
            disable_web_page_preview=True,
            **kwargs,
        )


async def send_reply_series(
    chat_id: int,
    title: str,
    text: str,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    reply_to_message_id: Optional[int] = None,
) -> None:
    parts = split_text_blocks(text)
    if not parts:
        parts = ["⚠️ Nenhum texto retornado para esta etapa."]

    total = len(parts)
    for idx, part in enumerate(parts, 1):
        header = f"{title} ({idx}/{total})" if total > 1 else title
        message_text = f"<b>{html.escape(header)}</b>\n\n{prepare_telegram_html(part)}"
        kwargs: Dict[str, Any] = {}
        if reply_to_message_id is not None:
            kwargs["reply_to_message_id"] = reply_to_message_id
        await context.bot.send_message(
            chat_id=chat_id,
            text=message_text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            **kwargs,
        )

# ---------------- SESSÃO ----------------

def _default_session() -> Dict[str, Any]:
    return {
        "cards": [],
        "group": None,
        "page": 0,
        "pending": None,
        "busy": False,
        "busy_since": None,
        "updated_at": time.time(),
    }


def _sanitize_session(data: Dict[str, Any]) -> Dict[str, Any]:
    base = _default_session()
    base.update({
        "cards": data.get("cards", []),
        "group": data.get("group"),
        "page": int(data.get("page", 0) or 0),
        "pending": data.get("pending"),
        "busy": bool(data.get("busy", False)),
        "busy_since": data.get("busy_since"),
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
                RUNNING_USERS.discard(uid)
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
_MIRA_LOCK: Optional[asyncio.Lock] = None


def mira_lock() -> asyncio.Lock:
    global _MIRA_LOCK
    if _MIRA_LOCK is None:
        _MIRA_LOCK = asyncio.Lock()
    return _MIRA_LOCK


def _cards_block(cards: List[Dict[str, Any]]) -> str:
    return "\n".join(
        f"{i + 1}. {c['name']} ({'invertida' if c['rev'] else 'normal'})"
        for i, c in enumerate(cards)
    )


def _prompt_common_rules() -> str:
    return (
        "Regras de formatação obrigatórias:\n"
        "- Responda em português do Brasil.\n"
        "- Use texto simples, sem HTML, sem Markdown complexo, sem tabelas e sem blocos de código.\n"
        "- Use títulos curtos e blocos separados por linha em branco.\n"
        "- Cada bloco deve poder ser enviado separadamente no Telegram sem perder contexto.\n"
        "- Não faça introdução longa; comece diretamente pela leitura.\n"
        "- Não invente certeza absoluta; quando for tendência, declare como tendência.\n"
    )


def build_prompt_tecnica(cards: List[Dict[str, Any]], tiragem_name: Optional[str] = None) -> str:
    tiragem_line = f"Tiragem: {tiragem_name}\n" if tiragem_name else ""
    return (
        f"{MIRA_PREFIX} faça a LEITURA TÉCNICA das cartas abaixo do Tarot Rider-Waite-Smith.\n\n"
        f"{_prompt_common_rules()}\n"
        "Estrutura obrigatória da resposta:\n\n"
        "LEITURA TÉCNICA\n\n"
        "1. CARTA POR CARTA\n"
        "Para cada carta, na ordem informada, use este formato:\n"
        "Nome da carta — posição normal/invertida\n"
        "• Significado clássico:\n"
        "• Ponto positivo:\n"
        "• Ponto negativo:\n"
        "• Inversão, quando houver:\n"
        "• Função dentro da tiragem:\n\n"
        "2. COMBINAÇÕES\n"
        "• Padrões entre as cartas:\n"
        "• Reforços:\n"
        "• Conflitos ou tensões:\n"
        "• Cartas dominantes:\n\n"
        "3. VISÃO GERAL\n"
        "• Síntese técnica final:\n\n"
        f"{tiragem_line}"
        f"Cartas:\n{_cards_block(cards)}"
    )


def build_prompt_preditiva(cards: List[Dict[str, Any]], tiragem_name: Optional[str] = None) -> str:
    tiragem_line = f"Tiragem: {tiragem_name}\n" if tiragem_name else ""
    return (
        f"{MIRA_PREFIX} faça a LEITURA PREDITIVA GERAL dessas mesmas cartas.\n\n"
        f"{_prompt_common_rules()}\n"
        "Estrutura obrigatória da resposta:\n\n"
        "LEITURA PREDITIVA\n\n"
        "1. TENDÊNCIAS\n"
        "• O que tende a acontecer:\n"
        "• Oportunidades:\n"
        "• Riscos concretos:\n\n"
        "2. TEMPO\n"
        "• Curto prazo:\n"
        "• Médio prazo:\n"
        "• Longo prazo:\n"
        "• Se não houver clareza temporal, diga isso explicitamente.\n\n"
        "3. CONSELHO FINAL\n"
        "• Melhor ação prática:\n"
        "• O que evitar:\n"
        "• Direção mais favorável:\n\n"
        "Seja ousada, prática e honesta, mas sem afirmar fatos futuros como certeza absoluta.\n\n"
        f"{tiragem_line}"
        f"Cartas:\n{_cards_block(cards)}"
    )


async def ask_mira(bot, prompt: str) -> str:
    """Envia uma pergunta para @mira e aguarda resposta com trava global.

    A trava evita que duas tiragens simultâneas misturem respostas da Mira no
    mesmo grupo. Dentro da trava, continuamos aceitando mensagens editadas/em
    partes e liberamos o pending no finally.
    """
    async with mira_lock():
        loop = asyncio.get_running_loop()
        pending = _MiraPending(loop)

        text = prompt if prompt.lstrip().lower().startswith(MIRA_PREFIX.lower()) else f"{MIRA_PREFIX} {prompt}"
        sent = await bot.send_message(chat_id=TIGROT_GROUP_ID, text=text, disable_web_page_preview=True)
        _pending_by_relay[sent.message_id] = pending
        logger.info("MIRA_RELAY_SENT | mid=%s | chars=%d", sent.message_id, len(text))

        try:
            return await asyncio.wait_for(pending.future, timeout=MIRA_WAIT_TIMEOUT)
        except asyncio.TimeoutError:
            partial = pending.merged_text()
            if partial:
                logger.warning("MIRA_TIMEOUT_PARTIAL | mid=%s | chars=%d", sent.message_id, len(partial))
                return partial + "\n\n⚠️ Observação: a resposta da Mira veio parcial por timeout."
            logger.warning("MIRA_TIMEOUT_EMPTY | mid=%s", sent.message_id)
            raise
        finally:
            pending.cancel_timer()
            _pending_by_relay.pop(sent.message_id, None)

async def mira_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Roteia respostas da Mira ao pending ativo.

    O reconhecimento é tolerante: aceita tanto reply formal à nossa pergunta
    quanto qualquer mensagem da Mira enquanto houver uma pergunta pendente
    (as perguntas são sequenciais, então no máximo uma fica ativa por vez).
    Também trata mensagens editadas (streaming) e respostas em partes.
    """
    msg = update.effective_message
    if not msg or msg.chat_id != TIGROT_GROUP_ID:
        return

    sender = msg.from_user
    if not sender:
        return
    sender_username = (sender.username or "").lstrip("@").lower()
    text = (msg.text or msg.caption or "").strip()

    # Diagnóstico: registra TODA mensagem recebida no grupo. Se a Mira não
    # aparecer aqui como from=@mira, o bot não está recebendo as mensagens dela
    # (limitação bot-a-bot do Telegram) — nesse caso é preciso um userbot.
    logger.info(
        "GROUP_MSG | from=@%s | uid=%s | role=%s | reply_to=%s | edit=%s | chars=%d",
        sender_username, sender.id, user_role(sender.id),
        msg.reply_to_message.message_id if msg.reply_to_message else None,
        bool(update.edited_message), len(text),
    )

    if MIRA_USER_ID is not None:
        if sender.id != MIRA_USER_ID:
            return
    elif sender_username != MIRA_USERNAME:
        return
    if not _pending_by_relay:
        logger.info("MIRA_MSG_NO_PENDING | mid=%s", msg.message_id)
        return

    # 1) preferir casar pelo reply à nossa pergunta
    pending = None
    relay_id = None
    if msg.reply_to_message:
        relay_id = msg.reply_to_message.message_id
        pending = _pending_by_relay.get(relay_id)
    # 2) fallback: única pergunta ativa
    if pending is None and len(_pending_by_relay) == 1:
        relay_id, pending = next(iter(_pending_by_relay.items()))

    if pending is None or pending.future.done():
        return
    if not text:
        return

    pending.update_part(msg.message_id, text)
    logger.info(
        "MIRA_PART | relay=%s | mid=%s | edit=%s | chars=%d",
        relay_id, msg.message_id, bool(update.edited_message), len(text),
    )


async def interpretar(bot, cards: List[Dict[str, Any]], tiragem_name: Optional[str]) -> Dict[str, str]:
    """Pede ao @mira as duas leituras (técnica e preditiva). Falhas viram aviso amigável."""
    out: Dict[str, str] = {}
    try:
        out["tecnica"] = await ask_mira(bot, build_prompt_tecnica(cards, tiragem_name))
    except Exception:
        logger.exception("Falha ao obter leitura técnica do @mira")
        out["tecnica"] = (
            "⚠️ A leitura técnica não chegou a tempo.\n\n"
            "As cartas e o mosaico foram preservados acima. Você pode tentar uma nova leitura com /tirar ou /ler."
        )
    try:
        out["preditiva"] = await ask_mira(bot, build_prompt_preditiva(cards, tiragem_name))
    except Exception:
        logger.exception("Falha ao obter leitura preditiva do @mira")
        out["preditiva"] = (
            "⚠️ A leitura preditiva não chegou a tempo.\n\n"
            "A leitura técnica, se enviada, continua válida; para tentar novamente, inicie outra leitura com /tirar ou /ler."
        )
    return out


async def enviar_interpretacao(
    chat_id: int,
    ctx: ContextTypes.DEFAULT_TYPE,
    cards: List[Dict[str, Any]],
    tiragem_name: Optional[str],
    *,
    reply_to_message_id: Optional[int] = None,
) -> None:
    kwargs: Dict[str, Any] = {}
    if reply_to_message_id is not None:
        kwargs["reply_to_message_id"] = reply_to_message_id

    await ctx.bot.send_message(
        chat_id=chat_id,
        text=(
            "🔮 <b>Cartas recebidas.</b>\n"
            "🧩 Preparando a leitura técnica.\n"
            "🌠 Depois envio a leitura preditiva em partes numeradas."
        ),
        parse_mode="HTML",
        **kwargs,
    )
    leituras = await interpretar(ctx.bot, cards, tiragem_name)

    await send_reply_series(
        chat_id,
        "🧩 Leitura técnica",
        leituras["tecnica"],
        ctx,
        reply_to_message_id=reply_to_message_id,
    )

    await send_reply_series(
        chat_id,
        "🌠 Leitura preditiva geral",
        leituras["preditiva"],
        ctx,
        reply_to_message_id=reply_to_message_id,
    )



def btn(text: str, callback_data: str, style: Optional[str] = None) -> InlineKeyboardButton:
    """Cria botão inline com estilo quando o cliente/API suportar.

    `style` é compatível com Bot API atual. Em python-telegram-bot >= 22.7
    o parâmetro existe diretamente; em versões anteriores usamos `api_kwargs`
    para preservar compatibilidade sem forçar atualização da dependência.
    Clientes Telegram antigos podem ignorar a cor e exibir o botão normal.
    """
    if style:
        try:
            return InlineKeyboardButton(text, callback_data=callback_data, style=style)
        except TypeError:
            return InlineKeyboardButton(text, callback_data=callback_data, api_kwargs={"style": style})
    return InlineKeyboardButton(text, callback_data=callback_data)

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("🎲 Tiragem automática", "home:tirar", "primary")],
        [btn("🃏 Escolher cartas manualmente", "home:manual", "primary")],
        [btn("🔎 Como buscar carta", "home:buscar")],
        [btn("♻️ Resetar sessão", "home:reset", "danger")],
    ])


def group_label(group: Optional[str]) -> str:
    labels = {
        "major": "Arcanos Maiores",
        "Copas": "Copas",
        "Paus": "Paus",
        "Espadas": "Espadas",
        "Ouros": "Ouros",
    }
    return labels.get(str(group or ""), "grupo de cartas")


def manual_groups_text(cards_count: int = 0) -> str:
    return (
        "🃏 <b>Escolha manual</b>\n\n"
        "Etapa 1 — escolha o grupo de cartas.\n"
        f"Cartas selecionadas: <b>{cards_count}/{MAX_CARDS}</b>\n\n"
        "Você pode montar a tiragem no seu ritmo e finalizar quando quiser."
    )


def manual_cards_text(group: str, page: int, cards_count: int = 0) -> str:
    cards = cards_for_group(group)
    pages = max(1, (len(cards) + CARDS_PER_PAGE - 1) // CARDS_PER_PAGE)
    return (
        "🃏 <b>Escolha manual</b>\n\n"
        f"Etapa 2 — <b>{html.escape(group_label(group))}</b>\n"
        f"Página: <b>{page + 1}/{pages}</b>\n"
        f"Cartas selecionadas: <b>{cards_count}/{MAX_CARDS}</b>\n\n"
        "Toque em uma carta para escolher a posição."
    )


def menu_grupos() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("🃏 Arcanos Maiores", "g:major")],
        [btn("❤️ Copas", "g:Copas")],
        [btn("🔥 Paus", "g:Paus")],
        [btn("⚔️ Espadas", "g:Espadas")],
        [btn("💰 Ouros", "g:Ouros")],
        [btn("🏠 Menu inicial", "home", "primary")],
    ])


def menu_cartas(group: str, page: int) -> InlineKeyboardMarkup:
    cards = cards_for_group(group)
    start = page * CARDS_PER_PAGE
    subset = cards[start:start + CARDS_PER_PAGE]

    kb = [[btn(c, f"c:{c}")] for c in subset]

    nav = []
    if page > 0:
        nav.append(btn("⬅️", f"nav:{group}:{page - 1}", "primary"))
    if start + CARDS_PER_PAGE < len(cards):
        nav.append(btn("➡️", f"nav:{group}:{page + 1}", "primary"))
    if nav:
        kb.append(nav)

    kb.append([btn("🔙 Voltar", "back", "danger")])
    return InlineKeyboardMarkup(kb)


def pos_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("⬆️ Normal", "p:n", "success")],
        [btn("⬇️ Invertida", "p:r", "primary")],
        [btn("🔙 Trocar carta", "posback", "danger")],
    ])


def selected_cards_text(cards: List[Dict[str, Any]], max_cards: int = MAX_CARDS) -> str:
    lines = [
        "🃏 <b>Escolha manual</b>",
        "",
        f"Cartas selecionadas: <b>{len(cards)}/{max_cards}</b>",
        "",
    ]
    if cards:
        for i, c in enumerate(cards):
            pos = "invertida" if c.get("rev") else "normal"
            lines.append(f"{i + 1}. {html.escape(str(c.get('name') or '—'))} — {pos}")
    else:
        lines.append("Nenhuma carta selecionada ainda.")
    lines.extend([
        "",
        "Você pode adicionar outra carta, remover a última ou finalizar a leitura.",
    ])
    return "\n".join(lines)


def tiragens_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn(TIRAGENS["dia"]["label"], "tir:dia")],
        [btn(TIRAGENS["pf"]["label"], "tir:pf")],
        [btn(TIRAGENS["sc"]["label"], "tir:sc")],
        [btn(TIRAGENS["peladan"]["label"], "tir:peladan")],
        [btn(TIRAGENS["ferradura"]["label"], "tir:ferradura")],
        [btn(TIRAGENS["cruz"]["label"], "tir:cruz")],
        [btn("🪐 Mandala Astrológica", "tir:mandala")],
        [btn("🏠 Menu inicial", "home", "primary")],
    ])


def tiragem_mandala_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn(TIRAGENS["mandala12"]["label"], "tir:mandala12")],
        [btn(TIRAGENS["mandala13"]["label"], "tir:mandala13")],
        [btn("🔙 Voltar às tiragens", "tirback", "danger")],
    ])


def tiragem_confirm_kb(tir_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("✅ Confirmar tiragem", f"tirrun:{tir_id}", "success")],
        [btn("🔙 Cancelar", f"tircancel:{tir_id}", "danger")],
    ])


def tiragem_preview_text(tir_id: str) -> str:
    info = TIRAGENS[tir_id]
    return (
        f"🎲 <b>{html.escape(info['label'])}</b>\n\n"
        f"{html.escape(info['description'])}\n\n"
        f"🃏 Cartas: <b>{info['count']}</b>\n"
        "🔀 Sorteio: imprevisível e sem repetição na mesma tiragem.\n"
        "🖼️ Resultado: cartas individuais, mosaico-resumo e leitura em partes.\n\n"
        "Confirma para iniciar?"
    )
# ---------------- BOT ----------------

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    log_user_action("/start", update)
    session = await refresh_busy_state(update.effective_user.id, await load_session(update.effective_user.id))
    if is_flow_busy(update.effective_user.id, session):
        await update.message.reply_text(
            "⏳ Uma leitura já está em andamento. Aguarde a conclusão antes de iniciar outra ação.",
            parse_mode="HTML",
        )
        return
    await update.message.reply_text(
        "🔮 <b>TIGROT Tarot</b>\n\n"
        "Escolha como quer começar:\n\n"
        "🎲 <b>Tiragem automática</b> — o bot sorteia as cartas e monta o resumo visual.\n"
        "🃏 <b>Escolha manual</b> — você seleciona carta por carta e define normal/invertida.\n"
        "🔎 <b>Buscar carta</b> — use <code>/buscar torre</code>, por exemplo.\n\n"
        "Depois da tiragem, eu envio as cartas individuais, um mosaico-resumo e a leitura em partes como resposta ao mosaico.",
        reply_markup=main_menu(),
        parse_mode="HTML"
    )
async def ler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    log_user_action("/ler", update)
    session = await refresh_busy_state(update.effective_user.id, await load_session(update.effective_user.id))
    if is_flow_busy(update.effective_user.id, session):
        await update.message.reply_text(
            "⏳ Uma leitura já está em andamento. Aguarde a conclusão antes de montar outra tiragem.",
            parse_mode="HTML",
        )
        return
    await update.message.reply_text(
        manual_groups_text(len(session.get("cards", []))),
        reply_markup=menu_grupos(),
        parse_mode="HTML"
    )


async def reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    log_user_action("/reset", update)
    session = await refresh_busy_state(update.effective_user.id, await load_session(update.effective_user.id))
    if is_flow_busy(update.effective_user.id, session):
        await update.message.reply_text(
            "⏳ Não resetei porque uma leitura está em andamento. Aguarde a conclusão para evitar mistura de respostas.",
            parse_mode="HTML",
        )
        return
    await delete_session(update.effective_user.id)
    await update.message.reply_text(
        "♻️ Sessão resetada.\n\nEscolha uma nova ação no menu inicial.",
        reply_markup=main_menu(),
        parse_mode="HTML",
    )


async def buscar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    log_user_action("/buscar", update, data=" ".join(ctx.args or []))
    if not ctx.args:
        await update.message.reply_text(
            "🔎 <b>Buscar carta</b>\n\n"
            "Use assim: <code>/buscar torre</code>\n"
            "Também funciona com parte do nome: <code>/buscar copas</code>.",
            parse_mode="HTML",
        )
        return

    termo = " ".join(ctx.args).strip().lower()
    matches = [c for c in ALL_CARDS if termo in c.lower()]

    if not matches:
        await update.message.reply_text(
            f"🔎 Nenhuma carta encontrada para <b>{html.escape(termo)}</b>.\n\n"
            "Tente uma parte do nome, como <code>sol</code>, <code>copas</code> ou <code>morte</code>.",
            parse_mode="HTML",
        )
        return

    text = (
        f"🔎 <b>Resultados para {html.escape(termo)}</b>\n\n"
        + "\n".join(f"• {html.escape(c)}" for c in matches[:20])
    )
    await update.message.reply_text(text, parse_mode="HTML")




async def tirar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    log_user_action("/tirar", update)
    session = await refresh_busy_state(update.effective_user.id, await load_session(update.effective_user.id))
    if is_flow_busy(update.effective_user.id, session):
        await update.message.reply_text(
            "⏳ Uma leitura já está em andamento. Aguarde a conclusão antes de sortear outra tiragem.",
            parse_mode="HTML",
        )
        return
    await update.message.reply_text(
        "🎲 <b>Tiragem automática</b>\n\n"
        "Escolha o método. Depois da confirmação, eu sorteio as cartas sem repetição, envio as imagens individuais, monto o mosaico-resumo e entrego a leitura em partes.",
        reply_markup=tiragens_menu(),
        parse_mode="HTML",
    )
async def _run_tiragem(chat_id: int, uid: int, tiragem_id: str, tiragem_name: str, count: int, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cards = RNG.sample(ALL_CARDS, count)
    result = [{"name": c, "rev": RNG.choice([True, False])} for c in cards]

    current_session = await load_session(uid)
    busy_since = current_session.get("busy_since") or time.time()
    await save_session(uid, {
        "cards": result,
        "group": None,
        "page": 0,
        "pending": None,
        "busy": True,
        "busy_since": busy_since,
        "updated_at": time.time(),
    })

    await ctx.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🎴 <b>{html.escape(tiragem_name)}</b>\n\n"
            f"📌 Tiragem confirmada: <b>{html.escape(TIRAGENS[tiragem_id]['label'])}</b>\n"
            f"🃏 Cartas sorteadas: <b>{count}</b>\n"
            "🖼️ Primeiro envio as cartas individuais; depois envio o mosaico-resumo e a interpretação em replies."
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

    anchor_id = await send_tiragem_mosaic(chat_id, ctx, result, tiragem_name)
    await enviar_interpretacao(chat_id, ctx, result, tiragem_name, reply_to_message_id=anchor_id)

    final_kwargs: Dict[str, Any] = {}
    if anchor_id is not None:
        final_kwargs["reply_to_message_id"] = anchor_id
    await ctx.bot.send_message(
        chat_id=chat_id,
        text="✨ <b>Leitura concluída.</b>\n\nUse /tirar para uma nova tiragem automática ou /ler para escolher cartas manualmente.",
        parse_mode="HTML",
        **final_kwargs,
    )


async def cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return

    uid = q.from_user.id
    data = q.data or ""
    log_callback_action(update, data)
    await q.answer()
    s = await refresh_busy_state(uid, await load_session(uid))

    if is_flow_busy(uid, s) and data not in {"tirrun:" + k for k in TIRAGENS} and data != "fim":
        await q.answer("Uma leitura já está em andamento. Aguarde a conclusão antes de usar outro botão.", show_alert=True)
        return

    if data == "home":
        s["pending"] = None
        await save_session(uid, s)
        await q.edit_message_text(
            "🔮 <b>TIGROT Tarot</b>\n\n"
            "Escolha como quer começar:\n\n"
            "🎲 <b>Tiragem automática</b> — sorteio completo com mosaico e interpretação.\n"
            "🃏 <b>Escolha manual</b> — você monta a tiragem carta por carta.\n"
            "🔎 <b>Buscar carta</b> — use <code>/buscar torre</code>.",
            reply_markup=main_menu(),
            parse_mode="HTML",
        )
        return

    if data == "home:tirar":
        await q.edit_message_text(
            "🎲 <b>Tiragem automática</b>\n\n"
            "Escolha o método. Depois da confirmação, eu sorteio as cartas, envio as imagens individuais, monto o mosaico-resumo e entrego a leitura em partes.",
            reply_markup=tiragens_menu(),
            parse_mode="HTML",
        )
        return

    if data == "home:manual":
        s["pending"] = None
        await save_session(uid, s)
        await q.edit_message_text(
            manual_groups_text(len(s.get("cards", []))),
            reply_markup=menu_grupos(),
            parse_mode="HTML",
        )
        return

    if data == "home:buscar":
        await q.edit_message_text(
            "🔎 <b>Buscar carta</b>\n\n"
            "Envie o comando com parte do nome da carta.\n\n"
            "Exemplos:\n"
            "<code>/buscar torre</code>\n"
            "<code>/buscar copas</code>\n"
            "<code>/buscar sol</code>",
            reply_markup=main_menu(),
            parse_mode="HTML",
        )
        return

    if data == "home:reset":
        await delete_session(uid)
        await q.edit_message_text(
            "♻️ <b>Sessão resetada.</b>\n\nEscolha uma nova ação no menu inicial.",
            reply_markup=main_menu(),
            parse_mode="HTML",
        )
        return

    if data == "tirback":
        await q.edit_message_text(
            "🎲 <b>Tiragem automática</b>\n\nEscolha o método da tiragem.",
            reply_markup=tiragens_menu(),
            parse_mode="HTML",
        )
        return

    if data == "tir:mandala":
        await q.edit_message_text(
            "🪐 <b>Mandala Astrológica</b>\n\nEscolha 12 cartas para leitura das casas ou 13 cartas para incluir síntese central.",
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
        if not await begin_user_run(uid, s):
            await q.answer("Uma leitura já está em andamento. Aguarde a conclusão.", show_alert=True)
            return

        info = TIRAGENS[tir_id]
        try:
            await _run_tiragem(
                q.message.chat.id,
                uid,
                tir_id,
                info["prompt_name"],
                info["count"],
                ctx,
            )
        except Exception:
            logger.exception("AUTO_TIRAGEM_FAILED | uid=%s | tiragem=%s", uid, tir_id)
            await send_flow_error(q.message.chat.id, ctx)
            await end_user_run(uid)
            return
        await end_user_run(uid, delete=True)
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
                "🎲 <b>Tiragem automática</b>\n\nEscolha outro método ou volte ao menu inicial.",
                reply_markup=tiragens_menu(),
                parse_mode="HTML",
            )
        return

    if data.startswith("g:"):
        g = data.split(":", 1)[1]
        if not valid_group(g):
            await q.answer("Grupo de cartas inválido ou expirado.", show_alert=True)
            await safe_edit_message_text(q, manual_groups_text(len(s.get("cards", []))), reply_markup=menu_grupos(), parse_mode="HTML")
            return
        s["group"] = g
        s["page"] = 0
        s["pending"] = None
        await save_session(uid, s)
        await q.edit_message_text(
            manual_cards_text(g, 0, len(s.get("cards", []))),
            reply_markup=menu_cartas(g, 0),
            parse_mode="HTML",
        )
        return

    if data.startswith("nav:"):
        try:
            _, g, p_raw = data.split(":")
            p = int(p_raw)
        except Exception:
            await q.answer("Página inválida.", show_alert=True)
            return
        normalized = normalize_page(g, p)
        if normalized is None:
            await q.answer("Página ou grupo inválido.", show_alert=True)
            await safe_edit_message_text(q, manual_groups_text(len(s.get("cards", []))), reply_markup=menu_grupos(), parse_mode="HTML")
            return

        s["group"] = g
        s["page"] = normalized
        await save_session(uid, s)
        await q.edit_message_text(
            manual_cards_text(g, normalized, len(s.get("cards", []))),
            reply_markup=menu_cartas(g, normalized),
            parse_mode="HTML",
        )
        return

    if data == "back":
        s["pending"] = None
        await save_session(uid, s)
        await q.edit_message_text(
            manual_groups_text(len(s.get("cards", []))),
            reply_markup=menu_grupos(),
            parse_mode="HTML",
        )
        return

    if data.startswith("c:"):
        card = data.split(":", 1)[1]
        if card not in ALL_CARDS:
            await q.answer("Carta inválida ou botão expirado.", show_alert=True)
            group = s.get("group") if valid_group(s.get("group")) else "major"
            page = normalize_page(group, int(s.get("page", 0) or 0)) or 0
            await safe_edit_message_text(q, manual_cards_text(group, page, len(s.get("cards", []))), reply_markup=menu_cartas(group, page), parse_mode="HTML")
            return
        s["pending"] = card
        await save_session(uid, s)
        await q.edit_message_text(
            f"🃏 <b>{html.escape(card)}</b>\n\nEtapa 3 — escolha a posição desta carta.",
            reply_markup=pos_kb(),
            parse_mode="HTML",
        )
        return

    if data.startswith("p:"):
        if data not in {"p:n", "p:r"}:
            await q.answer("Posição inválida.", show_alert=True)
            return
        if not s.get("pending"):
            await q.answer("Escolha uma carta primeiro", show_alert=True)
            return

        cards = s.get("cards", [])
        if len(cards) >= MAX_CARDS:
            await q.answer("Limite de cartas atingido", show_alert=True)
            return

        rev = data == "p:r"
        card = s["pending"]
        if card not in ALL_CARDS:
            await q.answer("Carta pendente inválida. Escolha a carta novamente.", show_alert=True)
            s["pending"] = None
            await save_session(uid, s)
            return

        cards.append({"name": card, "rev": rev})
        s["cards"] = cards
        s["pending"] = None
        s["updated_at"] = time.time()
        await save_session(uid, s)

        txt = selected_cards_text(s["cards"], MAX_CARDS)
        kb = [
            [btn("➕ Adicionar outra", "cont", "primary")],
            [btn("🗑️ Remover última", "undo:last", "danger")],
            [btn("✅ Finalizar leitura", "fim", "success")],
        ]
        await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
        return

    if data == "cont":
        s["pending"] = None
        await save_session(uid, s)
        await q.edit_message_text(
            manual_groups_text(len(s.get("cards", []))),
            reply_markup=menu_grupos(),
            parse_mode="HTML",
        )
        return

    if data == "posback":
        s["pending"] = None
        await save_session(uid, s)
        group = s.get("group") if valid_group(s.get("group")) else "major"
        page = normalize_page(group, int(s.get("page", 0) or 0)) or 0
        await q.edit_message_text(
            manual_cards_text(group, page, len(s.get("cards", []))),
            reply_markup=menu_cartas(group, page),
            parse_mode="HTML",
        )
        return

    if data == "undo:last":
        cards = s.get("cards", [])
        if not cards:
            await q.answer("Não há carta para remover.", show_alert=True)
            return
        cards.pop()
        s["cards"] = cards
        s["pending"] = None
        await save_session(uid, s)
        txt = selected_cards_text(cards, MAX_CARDS)
        kb = [
            [btn("➕ Adicionar outra", "cont", "primary")],
            [btn("🗑️ Remover última", "undo:last", "danger")],
            [btn("✅ Finalizar leitura", "fim", "success")],
        ]
        await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
        return

    if data == "fim":
        cards = s.get("cards", [])
        if not cards:
            await q.answer("Selecione ao menos uma carta.", show_alert=True)
            return
        if not await begin_user_run(uid, s):
            await q.answer("Uma leitura já está em andamento. Aguarde a conclusão.", show_alert=True)
            return

        anchor_id: Optional[int] = None
        try:
            for c in cards:
                img = find_image(c["name"])
                if img:
                    b = render_image(img, c["rev"])
                    await ctx.bot.send_photo(
                        chat_id=q.message.chat.id,
                        photo=InputFile(b),
                        caption=f"🃏 {html.escape(c['name'])} ({'invertida' if c['rev'] else 'normal'})",
                        parse_mode="HTML",
                    )
                else:
                    await ctx.bot.send_message(
                        chat_id=q.message.chat.id,
                        text=f"🃏 <b>{html.escape(c['name'])}</b> ({'invertida' if c['rev'] else 'normal'})",
                        parse_mode="HTML",
                    )

            anchor_id = await send_tiragem_mosaic(q.message.chat.id, ctx, cards, "Tiragem manual")
            await enviar_interpretacao(q.message.chat.id, ctx, cards, None, reply_to_message_id=anchor_id)

            final_kwargs: Dict[str, Any] = {}
            if anchor_id is not None:
                final_kwargs["reply_to_message_id"] = anchor_id
            await ctx.bot.send_message(
                chat_id=q.message.chat.id,
                text="✨ <b>Leitura concluída.</b>\n\nUse /tirar para uma nova tiragem automática ou /ler para escolher cartas manualmente.",
                parse_mode="HTML",
                **final_kwargs,
            )
        except Exception:
            logger.exception("MANUAL_TIRAGEM_FAILED | uid=%s | cards=%s", uid, len(cards))
            await send_flow_error(q.message.chat.id, ctx, reply_to_message_id=anchor_id)
            await end_user_run(uid)
            return

        await end_user_run(uid, delete=True)
        return

    await q.answer("Ação expirada ou inválida. Use o menu inicial.", show_alert=True)
    await safe_edit_message_text(
        q,
        "🔮 <b>TIGROT Tarot</b>\n\nEssa ação não está mais disponível. Escolha uma nova ação no menu inicial.",
        reply_markup=main_menu(),
        parse_mode="HTML",
    )
    return

# ---------------- MAIN ----------------

async def post_init(app: Application):
    app.bot_data["cleanup_task"] = asyncio.create_task(cleanup_sessions_task())


def main():
    # concurrent_updates: essencial. Sem isso (padrão = 1 update por vez), o
    # ask_mira() bloqueia o único slot enquanto aguarda, e o mira_router nunca
    # roda até dar timeout — a resposta da Mira jamais é reconhecida.
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .concurrent_updates(True)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ler", ler))
    app.add_handler(CommandHandler("tirar", tirar))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("buscar", buscar))
    app.add_handler(CallbackQueryHandler(cb))
    app.add_handler(
        MessageHandler(
            filters.Chat(chat_id=TIGROT_GROUP_ID) & (filters.TEXT | filters.CAPTION),
            mira_router,
        )
    )

    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "edited_message", "callback_query"],
    )


if __name__ == "__main__":
    main()