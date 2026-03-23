import os
import re
import requests
from bs4 import BeautifulSoup

GENIUS_API_KEY = os.getenv("GENIUS_API_KEY")

HEADERS = {
    "Authorization": f"Bearer {GENIUS_API_KEY}"
}


# ==============================
# 🔎 BUSCAR MÚSICA NO GENIUS
# ==============================
def search_song_on_genius(query):
    url = "https://api.genius.com/search"
    params = {"q": query}

    res = requests.get(url, headers=HEADERS, params=params)

    if res.status_code != 200:
        return None

    data = res.json()
    hits = data.get("response", {}).get("hits", [])

    if not hits:
        return None

    return hits[0]["result"]["url"]


# ==============================
# 📄 EXTRAIR LETRA DA PÁGINA
# ==============================
def scrape_lyrics(song_url):
    try:
        page = requests.get(song_url)
        soup = BeautifulSoup(page.text, "html.parser")

        lyrics_divs = soup.find_all("div", {"data-lyrics-container": "true"})

        lyrics = "\n".join([div.get_text(separator="\n") for div in lyrics_divs])

        return lyrics.strip()
    except:
        return None


# ==============================
# 🎯 EXTRAIR REFRÃO (INTELIGENTE)
# ==============================
def extract_chorus(lyrics: str) -> str:
    if not lyrics:
        return None

    text = lyrics.replace("\r", "").strip()

    # 1. PRIORIDADE: [Chorus] ou [Refrain]
    pattern = re.compile(
        r"\[(Chorus|Refrain).*?\](.*?)\n(?=\[|$)",
        re.IGNORECASE | re.DOTALL
    )

    matches = pattern.findall(text)

    if matches:
        chorus = matches[0][1].strip()
        if len(chorus) > 20:
            return chorus

    # 2. FALLBACK: bloco mais repetido
    parts = [
        p.strip() for p in text.split("\n\n")
        if len(p.strip()) > 30
    ]

    freq = {}
    for p in parts:
        freq[p] = freq.get(p, 0) + 1

    repeated = sorted(freq.items(), key=lambda x: x[1], reverse=True)

    if repeated and repeated[0][1] > 1:
        return repeated[0][0]

    # 3. FALLBACK FINAL: início da música
    return parts[0] if parts else None


# ==============================
# 💬 FORMATAR COMO QUOTE TELEGRAM
# ==============================
def format_telegram_quote(text: str) -> str:
    lines = text.split("\n")
    quoted = "\n".join([f"> {line}" for line in lines])
    return quoted


# ==============================
# 🎵 FUNÇÃO PRINCIPAL
# ==============================
def get_song_chorus(query):
    url = search_song_on_genius(query)

    if not url:
        return "❌ Música não encontrada."

    lyrics = scrape_lyrics(url)

    if not lyrics:
        return "❌ Não consegui obter a letra."

    chorus = extract_chorus(lyrics)

    if not chorus:
        return "❌ Não encontrei o refrão."

    # Limite seguro Telegram
    chorus = chorus[:1200]

    return format_telegram_quote(chorus)


# ==============================
# 🔧 TESTE LOCAL (OPCIONAL)
# ==============================
if __name__ == "__main__":
    musica = input("Digite música: ")
    resultado = get_song_chorus(musica)
    print("\n")
    print(resultado)