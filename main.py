import asyncio
import logging
import os
import random
import re
import unicodedata
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List

from google import genai
from PIL import Image, ImageOps
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tarot_bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = "gemini-2.5-flash"

MAX_CARDS = 12
CARDS_PER_PAGE = 8

SESSIONS: Dict[int, Dict[str, Any]] = {}

IMAGE_ROOT = Path("assets/rws")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

# ---------------- CARTAS ----------------

TAROT_MAJOR = [
    "O Louco","O Mago","A Sacerdotisa","A Imperatriz","O Imperador",
    "O Hierofante","Os Enamorados","O Carro","A Força","O Eremita",
    "A Roda da Fortuna","A Justiça","O Enforcado","A Morte",
    "A Temperança","O Diabo","A Torre","A Estrela","A Lua",
    "O Sol","O Julgamento","O Mundo"
]

RANKS = ["Ás","Dois","Três","Quatro","Cinco","Seis","Sete","Oito","Nove","Dez","Valete","Cavaleiro","Rainha","Rei"]
SUITS = ["Copas","Paus","Espadas","Ouros"]

MINOR = {s: [f"{r} de {s}" for r in RANKS] for s in SUITS}
ALL_CARDS = TAROT_MAJOR + [c for s in SUITS for c in MINOR[s]]

# ---------------- UTILS ----------------

def slugify(v):
    v = unicodedata.normalize("NFKD", v)
    v = "".join(c for c in v if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+","-",v.lower()).strip("-")

def build_index():
    idx={}
    if not IMAGE_ROOT.exists():
        return idx
    for f in IMAGE_ROOT.rglob("*"):
        if f.suffix.lower() in IMAGE_EXTENSIONS:
            idx[slugify(f.stem)] = f
    return idx

IMAGE_INDEX = build_index()

def find_image(card):
    for k,p in IMAGE_INDEX.items():
        if slugify(card) in k:
            return p
    return None

def render_image(path, rev):
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        if rev:
            img = img.rotate(180, expand=True)
        b=BytesIO()
        img.save(b,"PNG")
        b.seek(0)
        return b

# ---------------- SESSION ----------------

def get_session(uid):
    if uid not in SESSIONS:
        SESSIONS[uid] = {"cards": [],"group": None,"page": 0,"pending": None}
    return SESSIONS[uid]

# ---------------- GEMINI ----------------

client = genai.Client(api_key=GEMINI_API_KEY)

def build_prompt(cards):
    txt="\n".join([f"{i+1}. {c['name']} ({'invertida' if c['rev'] else 'normal'})" for i,c in enumerate(cards)])
    return f"""
Você é um intérprete didático de tarot.

Para cada carta:
- significado
- ponto positivo
- ponto negativo

Depois:
- combinações entre cartas
- visão global

Não omita aspectos negativos.
Não afirme certezas absolutas.

Cartas:
{txt}
"""

def ai(cards):
    r = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=build_prompt(cards)
    )
    return r.text or ""

# ---------------- UI ----------------

def menu_grupos():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🃏 Arcanos Maiores", callback_data="g:major")],
        [InlineKeyboardButton("❤️ Copas", callback_data="g:Copas")],
        [InlineKeyboardButton("🔥 Paus", callback_data="g:Paus")],
        [InlineKeyboardButton("⚔️ Espadas", callback_data="g:Espadas")],
        [InlineKeyboardButton("💰 Ouros", callback_data="g:Ouros")],
    ])

def menu_cartas(group, page):
    cards = TAROT_MAJOR if group=="major" else MINOR[group]
    start = page*CARDS_PER_PAGE
    subset = cards[start:start+CARDS_PER_PAGE]

    kb = [[InlineKeyboardButton(c, callback_data=f"c:{c}")] for c in subset]

    nav=[]
    if page>0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"nav:{group}:{page-1}"))
    if start+CARDS_PER_PAGE < len(cards):
        nav.append(InlineKeyboardButton("➡️", callback_data=f"nav:{group}:{page+1}"))
    if nav:
        kb.append(nav)

    kb.append([InlineKeyboardButton("🔙 Voltar", callback_data="back")])
    return InlineKeyboardMarkup(kb)

def pos_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬆️ Normal", callback_data="p:n")],
        [InlineKeyboardButton("⬇️ Invertida", callback_data="p:r")]
    ])

# ---------------- BOT ----------------

async def start(update:Update,ctx):
    await update.message.reply_text("🔮 Tarot\nEscolha:", reply_markup=menu_grupos())

async def ler(update:Update,ctx):
    return await start(update,ctx)

async def tirar(update:Update,ctx):
    args = update.message.text.split()
    try:
        n = int(args[1]) if len(args) > 1 else 3
    except:
        n = 3

    n = max(1, min(n, MAX_CARDS))

    cards = random.sample(ALL_CARDS, n)
    result = [{"name":c,"rev":random.choice([True,False])} for c in cards]

    for c in result:
        img=find_image(c["name"])
        if img:
            b=render_image(img,c["rev"])
            await ctx.bot.send_photo(update.effective_chat.id,photo=InputFile(b))
        else:
            await ctx.bot.send_message(update.effective_chat.id, c["name"])

    try:
        res = await asyncio.to_thread(ai, result)
    except Exception as e:
        logger.error(e)
        res = "Erro ao gerar interpretação."

    await ctx.bot.send_message(update.effective_chat.id,res)

async def reset(update:Update,ctx):
    SESSIONS.pop(update.effective_user.id,None)
    await update.message.reply_text("Resetado")

async def cb(update:Update,ctx):
    q=update.callback_query
    await q.answer()
    uid=q.from_user.id
    s=get_session(uid)
    data=q.data

    if data.startswith("g:"):
        g=data.split(":")[1]
        s["group"]=g
        s["page"]=0
        await q.edit_message_text("Escolha:", reply_markup=menu_cartas(g,0))

    elif data.startswith("nav:"):
        _,g,p=data.split(":")
        s["page"]=int(p)
        await q.edit_message_reply_markup(menu_cartas(g,int(p)))

    elif data=="back":
        await q.edit_message_text("Escolha:", reply_markup=menu_grupos())

    elif data.startswith("c:"):
        card=data.split(":",1)[1]
        s["pending"]=card
        await q.edit_message_text(card+"\nPosição?", reply_markup=pos_kb())

    elif data.startswith("p:"):
        if not s.get("pending"):
            await q.answer("Escolha uma carta primeiro", show_alert=True)
            return

        if len(s["cards"]) >= MAX_CARDS:
            await q.answer("Limite de cartas atingido", show_alert=True)
            return

        rev=data=="p:r"
        card=s["pending"]

        s["cards"].append({"name":card,"rev":rev})
        s["pending"]=None

        txt="\n".join([
            f"{i+1}. {c['name']} ({'invertida' if c['rev'] else 'normal'})"
            for i,c in enumerate(s["cards"])
        ])
        txt += f"\n\nTotal: {len(s['cards'])}/{MAX_CARDS}"

        kb=[
            [InlineKeyboardButton("➕ Continuar", callback_data="cont")],
            [InlineKeyboardButton("✅ Finalizar", callback_data="fim")]
        ]

        await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))

    elif data=="cont":
        await q.edit_message_text("Escolha:", reply_markup=menu_grupos())

    elif data=="fim":
        cards=s["cards"]

        for c in cards:
            img=find_image(c["name"])
            if img:
                b=render_image(img,c["rev"])
                await ctx.bot.send_photo(q.message.chat.id,photo=InputFile(b))

        try:
            res = await asyncio.to_thread(ai, cards)
        except Exception as e:
            logger.error(e)
            res = "Erro ao gerar interpretação."

        # DIVISÃO DIDÁTICA
        partes = res.split("\n\n")

        for p in partes:
            if p.strip():
                await ctx.bot.send_message(q.message.chat.id, p.strip())

        await ctx.bot.send_message(q.message.chat.id, "✨ Tiragem finalizada.\nUse /ler ou /tirar para nova.")

        SESSIONS.pop(uid,None)

# ---------------- MAIN ----------------

def main():
    app=Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("ler",ler))
    app.add_handler(CommandHandler("tirar",tirar))
    app.add_handler(CommandHandler("reset",reset))
    app.add_handler(CallbackQueryHandler(cb))

    app.run_polling()

if __name__=="__main__":
    main()