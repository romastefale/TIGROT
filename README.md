# Pidro FM Bot

Bot do Telegram focado em UX simples:

1. `/music <termo>`
2. escolhe uma música na lista
3. decide entre **só música** ou **refrão**

---

## Funcionalidades


---

## Funcionalidades
Bot do Telegram para buscar músicas, navegar entre resultados e compartilhar informações da faixa com capa, preview e refrão quando disponível.

## O que o bot faz

- Busca músicas por texto usando a API da Deezer.
- Exibe resultados paginados com botão `Load more`.
- Mostra capa do álbum no chat quando disponível.
- Oferece links rápidos para preview, Deezer e letra.
- Tenta localizar a letra no Genius e faz fallback para `lyrics.ovh`.
- Usa OpenAI para extrair um refrão melhor quando `OPENAI_API_KEY` estiver configurada.
- Funciona em modo `polling`, `webhook` e busca inline no Telegram.

- Busca músicas na Deezer.
- Resultado paginado com botão **Load more**.
- Cartão com capa, preview e link Deezer quando disponível.
- Busca letra (Genius, com fallback lyrics.ovh).
- Extração de refrão com OpenAI (opcional) + fallback local.
- Modo inline do Telegram.
- Execução em `polling` ou `webhook`.

---
- Python 3.10+
- Token do bot do Telegram

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

Variáveis principais:

- `TELEGRAM_TOKEN`: token do bot no Telegram.
- `GENIUS_API_KEY`: opcional, melhora a chance de encontrar a página da letra.
- `OPENAI_API_KEY`: opcional, melhora a qualidade da extração do refrão.
- `WEBHOOK_URL`: URL pública para rodar em modo webhook.
- `WEBHOOK_SECRET`: segredo do webhook.
- `PORT`: porta da aplicação, padrão `8443`.

> O projeto carrega automaticamente o arquivo `.env` na inicialização. Se o bot não responder comandos, verifique primeiro se `TELEGRAM_TOKEN` está preenchido corretamente no `.env`.

## Como usar

```text
/start
/help
/music Daft Punk One More Time
```

## Railway (seu caso)

Use no ambiente da Railway:

```env
TELEGRAM_TOKEN=SEU_TOKEN
RUN_MODE=webhook
WEBHOOK_URL=https://pidrofmbot-v2-production.up.railway.app
WEBHOOK_SECRET=seu-segredo
PORT=8443
```bash
python main.py
```

URL final usada pelo bot:

## Railway + webhook

Os seus logs mostraram este problema:

- o container subiu em **polling**;
- o Telegram retornou **409 Conflict** em `getUpdates`;
- isso normalmente significa **outra instância usando polling** ou que a aplicação deveria estar em **webhook** e não entrou nesse modo.

Para corrigir na Railway, configure estas variáveis:

```env
TELEGRAM_TOKEN=SEU_TOKEN
WEBHOOK_URL=https://pidrofmbot-v2-production.up.railway.app
WEBHOOK_SECRET=pidro-secret-2026
PORT=8443
```

O código agora também tenta detectar URL pública da Railway por `RAILWAY_PUBLIC_DOMAIN` e `RAILWAY_STATIC_URL` quando `WEBHOOK_URL` não estiver explícito.

### URL final do webhook

```text
https://pidrofmbot-v2-production.up.railway.app/<TELEGRAM_TOKEN>
```

Registrar webhook manualmente (opcional):
### Registrar manualmente no Telegram

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

```bash
curl "https://api.telegram.org/botSEU_TOKEN/getWebhookInfo"
```

---

## Comandos do bot

- `/start`
- `/help`
- `/music <termo>`

---
### Verificar webhook

```bash
curl "https://api.telegram.org/botSEU_TOKEN/getWebhookInfo"
```

### O que esperar no log

Quando estiver correto, você deve ver:

- `Bot conectado como @...`
- `Iniciando em modo WEBHOOK — porta ...`
- `Webhook público configurado para https://pidrofmbot-v2-production.up.railway.app/<TELEGRAM_TOKEN>`

Se aparecer `Iniciando em modo POLLING` dentro da Railway, a URL pública não foi detectada/configurada corretamente.

## Diagnóstico do erro 409 Conflict

Esse erro dos seus logs significa que o Telegram recebeu mais de um consumidor para `getUpdates`.

Causas típicas:

1. duas instâncias do bot rodando ao mesmo tempo em polling;
2. um processo antigo ainda ativo em outro servidor;
3. deploy em Railway sem `WEBHOOK_URL`, fazendo o serviço cair em polling por engano.

O código agora registra um aviso claro quando detecta Railway sem webhook e também gera log explícito em caso de `Conflict` no polling.

## Testes

```bash
pytest -q
```

## Estrutura

- `pidrofmbot.py`: implementação principal do bot.
- `main.py`: ponto de entrada simples para execução local.
- `tests/test_pidrofmbot.py`: testes unitários básicos.
- `.env.example`: exemplo de configuração.
