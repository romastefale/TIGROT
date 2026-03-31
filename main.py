import asyncio
import html
import json
import logging
import os
import random
import re
import shutil
import tempfile
import zipfile
import time
import unicodedata
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import redis.asyncio as redis
from google import genai
from PIL import Image, ImageOps
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, InputSticker, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
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

IMAGE_ROOT = Path(os.getenv("RWS_IMAGE_DIR", "/rss/rws"))
STICKER_ROOT = Path(os.getenv("TIGROT_STICKER_DIR", "assets/rws-png"))
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
TIGROT_PACK_NAME = os.getenv("TIGROT_PACK_NAME", "TIGROT").strip() or "TIGROT"
TIGROT_PACK_DISPLAY_NAME = os.getenv("TIGROT_PACK_DISPLAY_NAME", "/TIGROT").strip() or "/TIGROT"
TIGROT_PACK_URL = os.getenv("TIGROT_PACK_URL", "").strip()

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

MAJOR_NUMBERS = {name: i for i, name in enumerate(TAROT_MAJOR)}
MAJOR_ALIASES: Dict[str, List[str]] = {
    "A Sacerdotisa": ["alta-sacerdotisa", "sacerdotisa-alta"],
    "A Roda da Fortuna": ["roda", "roda-da-fortuna"],
    "O Enforcado": ["pendurado", "enforcado-pendurado"],
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


def build_index(root: Path) -> Dict[str, Path]:
    idx: Dict[str, Path] = {}
    if not root.exists():
        logger.warning("Pasta de imagens não encontrada: %s", root)
        return idx

    for f in root.rglob("*"):
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS:
            idx[slugify(f.stem)] = f

    logger.info("Índice de imagens carregado: %d arquivos em %s", len(idx), root)
    return idx


PHOTO_INDEX = build_index(IMAGE_ROOT)
STICKER_INDEX = build_index(STICKER_ROOT)
IMAGE_INDEX = PHOTO_INDEX


def _strip_articles(name: str) -> str:
    return re.sub(r"^(?:o|a|os|as)\s+", "", name.strip(), flags=re.I)


def card_stem_candidates(card_name: str) -> List[str]:
    name = card_name.strip()
    candidates: List[str] = []

    if name in MAJOR_NUMBERS:
        num = MAJOR_NUMBERS[name]
        clean = _strip_articles(name)
        slug_clean = slugify(clean)
        slug_full = slugify(name)
        candidates.extend([
            f"{num:02d}-{slug_clean}",
            f"{num:02d}-{slug_full}",
            f"TarotRWS-{num:02d}-{slug_clean}",
            f"TarotRWS-{num:02d}-{slug_full}",
        ])
        for alias in MAJOR_ALIASES.get(name, []):
            candidates.extend([
                f"{num:02d}-{alias}",
                f"TarotRWS-{num:02d}-{alias}",
            ])
    else:
        if " de " in name:
            rank, suit = name.split(" de ", 1)
            rank = rank.strip()
            suit = suit.strip()
            rank_slug = slugify(rank)
            suit_slug = slugify(suit)
            if rank in RANKS and suit in SUITS:
                idx = RANKS.index(rank) + 1
                candidates.extend([
                    f"{suit}-{idx:02d}",
                    f"{suit}-{idx}",
                    f"{suit}-{rank}",
                    f"{suit}-{rank_slug}",
                    f"{suit_slug}-{idx:02d}",
                    f"{suit_slug}-{rank_slug}",
                    f"TarotRWS-{suit}-{idx:02d}",
                ])

    candidates.extend([
        name,
        slugify(name),
        f"TarotRWS-{slugify(name)}",
    ])

    ordered: List[str] = []
    seen = set()
    for candidate in candidates:
        key = slugify(candidate)
        if key and key not in seen:
            seen.add(key)
            ordered.append(candidate)
    return ordered


def find_media(card_name: str, prefer_sticker: bool = True) -> Optional[Path]:
    indexes = (STICKER_INDEX, PHOTO_INDEX) if prefer_sticker else (PHOTO_INDEX, STICKER_INDEX)
    for stem in card_stem_candidates(card_name):
        key = slugify(stem)
        for idx in indexes:
            path = idx.get(key)
            if path:
                return path
    return None


def find_sticker(card_name: str) -> Optional[Path]:
    return find_media(card_name, prefer_sticker=True)


def find_image(card_name: str) -> Optional[Path]:
    return find_media(card_name, prefer_sticker=False)


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



def pack_sticker_emoji(card_name: str) -> str:
    if card_name in TAROT_MAJOR:
        return "🔮"
    if "de Copas" in card_name:
        return "❤️"
    if "de Paus" in card_name:
        return "🔥"
    if "de Espadas" in card_name:
        return "⚔️"
    if "de Ouros" in card_name:
        return "💰"
    return "🃏"


def build_tigrot_pack_zip() -> tuple[Path, Path]:
    if not STICKER_ROOT.exists():
        raise FileNotFoundError(f"Pasta de stickers não encontrada: {STICKER_ROOT}")

    tmpdir = Path(tempfile.mkdtemp(prefix="tigrot_pack_"))
    zip_path = tmpdir / f"{TIGROT_PACK_NAME}.zip"
    manifest: List[Dict[str, Any]] = []

    readme = f"""Pacote de stickers {TIGROT_PACK_DISPLAY_NAME}

Como usar:
1. Abra o bot @Stickers no Telegram.
2. Crie um novo pacote.
3. Envie as imagens deste pacote na ordem que preferir.
4. Associe um emoji por sticker, usando o padrão sugerido no manifesto.

Origem: {STICKER_ROOT.as_posix()}
"""

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for card in ALL_CARDS:
            src = find_sticker(card)
            if not src:
                continue
            zf.write(src, arcname=src.name)
            manifest.append({
                "card": card,
                "file": src.name,
                "emoji": pack_sticker_emoji(card),
            })
        zf.writestr("README.txt", readme)
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "pack_display_name": TIGROT_PACK_DISPLAY_NAME,
                    "pack_name": TIGROT_PACK_NAME,
                    "source": STICKER_ROOT.as_posix(),
                    "items": manifest,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

    return zip_path, tmpdir


def tigrot_help_text() -> str:
    return (
        f"🎨 <b>{html.escape(TIGROT_PACK_DISPLAY_NAME)}</b>\n\n"
        "Este pacote foi preparado com as artes de <b>/assets/rws-png</b> e pode ser criado automaticamente como sticker set do Telegram.\n\n"
        "Fluxo recomendado:\n"
        "1. Baixe o ZIP.\n"
        "2. Abra o @Stickers.\n"
        "3. Crie um novo pacote.\n"
        "4. Ou use /criarpack para gerar o pacote automaticamente.\n"
        "5. Atribua os emojis sugeridos no manifesto, se preferir ajustar manualmente.\n\n"
        "Melhorias úteis para o fluxo atual:\n"
        "• usar um emoji padrão por naipe nos arcanos menores;\n"
        "• destacar invertidas com um sticker espelhado;\n"
        "• criar respostas rápidas com sticker para finalização positiva, alerta e conselho;\n"
        "• mostrar uma prévia em sticker antes da leitura final."
    )


def tigrot_menu_kb() -> InlineKeyboardMarkup:
    rows = []
    if TIGROT_PACK_URL:
        rows.append([InlineKeyboardButton("➕ Abrir pacote", url=TIGROT_PACK_URL)])
    rows.extend(
        [
            [InlineKeyboardButton("📦 Baixar ZIP", callback_data="tigrot:zip")],
            [InlineKeyboardButton("🧾 Como adicionar", callback_data="tigrot:help")],
            [InlineKeyboardButton("🖼️ Ver amostras", callback_data="tigrot:sample")],
            [InlineKeyboardButton("🧱 Criar pack", callback_data="tigrot:create")],
            [InlineKeyboardButton("🔙 Voltar", callback_data="tigrot:back")],
        ]
    )
    return InlineKeyboardMarkup(rows)


async def send_tigrot_zip(chat_id: int, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    zip_path, tmpdir = build_tigrot_pack_zip()
    try:
        with zip_path.open("rb") as fp:
            await ctx.bot.send_document(
                chat_id=chat_id,
                document=InputFile(fp, filename=zip_path.name),
                caption=(
                    f"📦 Pacote {html.escape(TIGROT_PACK_DISPLAY_NAME)} pronto.\n"
                    "Use o arquivo como base para criar o pacote no @Stickers."
                ),
                parse_mode="HTML",
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _sticker_set_name(bot_username: str) -> str:
    safe = re.sub(r"[^a-z0-9_]+", "", (bot_username or "").lower())
    prefix = re.sub(r"[^a-z0-9_]+", "", slugify(TIGROT_PACK_NAME).replace("-", "_"))
    return f"{prefix}_by_{safe}" if safe else prefix


async def _build_input_stickers() -> tuple[List[InputSticker], List[InputSticker], List[Path]]:
    stickers: List[InputSticker] = []
    deferred: List[InputSticker] = []
    paths_used: List[Path] = []

    for card in ALL_CARDS:
        src = find_sticker(card)
        if not src or src.suffix.lower() not in {".png", ".webp"}:
            continue

        paths_used.append(src)
        emoji = pack_sticker_emoji(card)
        sticker = InputSticker(sticker=src.read_bytes(), emoji_list=[emoji], format="static")
        if len(stickers) < 50:
            stickers.append(sticker)
        else:
            deferred.append(sticker)

    return stickers, deferred, paths_used


async def create_tigrot_pack(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    me = await ctx.bot.get_me()
    set_name = _sticker_set_name(me.username or "")
    title = TIGROT_PACK_DISPLAY_NAME.strip("/") or TIGROT_PACK_NAME

    try:
        initial_stickers, remaining_stickers, used_paths = await _build_input_stickers()

        if not initial_stickers:
            await update.message.reply_text(
                "⚠️ Não encontrei arquivos PNG/WebP suficientes em /assets/rws-png para criar o pacote."
            )
            return

        await ctx.bot.create_new_sticker_set(
            user_id=update.effective_user.id,
            name=set_name,
            title=title,
            stickers=initial_stickers,
        )

        for sticker in remaining_stickers:
            await ctx.bot.add_sticker_to_set(
                user_id=update.effective_user.id,
                name=set_name,
                sticker=sticker,
            )

        await update.message.reply_text(
            f"✅ Pack criado com sucesso: https://t.me/addstickers/{set_name}\n"
            f"🃏 Cartas incluídas: {len(used_paths)}"
        )
    except Exception as e:
        logger.exception("Falha ao criar o pack TIGROT")
        await update.message.reply_text(f"Erro ao criar o pack TIGROT: {e}")


async def send_tigrot_samples(chat_id: int, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    sample_cards = [
        "O Louco",
        "A Sacerdotisa",
        "O Carro",
        "O Sol",
        "Ás de Copas",
        "Dez de Espadas",
        "Rei de Ouros",
    ]
    for card in sample_cards:
        img = find_sticker(card) or find_image(card)
        if not img:
            continue
        if img.suffix.lower() in {".png", ".webp"}:
            await ctx.bot.send_sticker(chat_id=chat_id, sticker=img)
        else:
            b = render_image(img, False)
            await ctx.bot.send_photo(
                chat_id=chat_id,
                photo=InputFile(b),
                caption=f"🧩 {html.escape(card)}",
                parse_mode="HTML",
            )


async def send_card(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, card: Dict[str, Any]) -> None:
    path = find_media(card["name"], prefer_sticker=True)
    if not path:
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=f"🃏 <b>{html.escape(card['name'])}</b> ({'invertida' if card['rev'] else 'normal'})",
            parse_mode="HTML",
        )
        return

    try:
        if path.suffix.lower() in {".png", ".webp"}:
            if card["rev"]:
                with Image.open(path) as img:
                    img = ImageOps.exif_transpose(img)
                    img = img.rotate(180, expand=True)
                    b = BytesIO()
                    img.save(b, "PNG")
                    b.seek(0)
                    await ctx.bot.send_sticker(
                        chat_id=chat_id,
                        sticker=InputFile(b, filename=f"{slugify(card['name'])}.png"),
                    )
            else:
                await ctx.bot.send_sticker(chat_id=chat_id, sticker=path)
        else:
            b = render_image(path, card["rev"])
            await ctx.bot.send_photo(
                chat_id=chat_id,
                photo=Input
