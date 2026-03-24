# Pidro FM Bot

Bot do Telegram focado em UX simples:

1. `/music <termo>`
2. escolhe uma música na lista
3. decide entre **só música** ou **refrão**

---

## Funcionalidades

- Busca músicas na Deezer.
- Resultado paginado com botão **Load more**.
- Cartão com capa, preview e link Deezer quando disponível.
- Busca letra (Genius, com fallback lyrics.ovh).
- Extração de refrão com OpenAI (opcional) + fallback local.
- Modo inline do Telegram.
- Execução em `polling` ou `webhook`.

---

## Instalação

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Variáveis de ambiente

- `TELEGRAM_TOKEN` (**obrigatória**)
- `RUN_MODE` = `auto` (default), `polling` ou `webhook`
- `WEBHOOK_URL` (obrigatória no modo webhook)
- `WEBHOOK_SECRET` (opcional, auto-gerada se vazia)
- `PORT` (default `8443`)
- `GENIUS_API_KEY` (opcional)
- `OPENAI_API_KEY` (opcional)

> O bot carrega `.env` automaticamente se o arquivo existir.

---

## Rodar local

```bash
python main.py
```

- `RUN_MODE=auto` usa webhook se `WEBHOOK_URL` existir, senão polling.
- Em Railway, prefira webhook para evitar `409 Conflict`.

---

## Railway (seu caso)

Use no ambiente da Railway:

```env
TELEGRAM_TOKEN=SEU_TOKEN
RUN_MODE=webhook
WEBHOOK_URL=https://pidrofmbot-v2-production.up.railway.app
WEBHOOK_SECRET=seu-segredo
PORT=8443
```

URL final usada pelo bot:

```text
https://pidrofmbot-v2-production.up.railway.app/<TELEGRAM_TOKEN>
```

Registrar webhook manualmente (opcional):

```bash
curl -X POST "https://api.telegram.org/botSEU_TOKEN/setWebhook" \
  -d "url=https://pidrofmbot-v2-production.up.railway.app/SEU_TOKEN" \
  -d "secret_token=SEU_SEGREDO"
```

Conferir status webhook:

```bash
curl "https://api.telegram.org/botSEU_TOKEN/getWebhookInfo"
```

---

## Comandos do bot

- `/start`
- `/help`
- `/music <termo>`

---

## Testes

```bash
pytest -q
```
