# Pidro FM Bot

Bot do Telegram para buscar músicas na Deezer e compartilhar resultados no chat ou no modo inline, agora com trecho relevante da letra via Genius.

## Funcionalidades

- Busca por texto no chat
- Busca inline no Telegram
- Comando `/start` com instruções de uso
- Confirmação `♪ Lyrics?` antes de enviar a música no chat
- Paginação com botão `Load more`
- Envio de capa do álbum com legenda formatada
- Suporte a `polling` e `webhook`

## Requisitos

- Python 3.10+
- Token de bot do Telegram

## Instalação

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuração

Copie o arquivo `.env.example` e configure as variáveis:

```bash
cp .env.example .env
```

Variáveis:

- `TELEGRAM_TOKEN`: token do bot
- `WEBHOOK_URL`: URL pública para webhook (opcional)
- `WEBHOOK_SECRET`: segredo do webhook (opcional)
- `PORT`: porta da aplicação (padrão `8443`)
- `GENIUS_ACCESS_TOKEN`: token da API do Genius para buscar trecho relevante da letra

## Execução local

```bash
export TELEGRAM_TOKEN="seu-token"
python pidrofmbot.py
```

Se `WEBHOOK_URL` não estiver definido, o bot roda em modo polling.

## Testes

```bash
pytest
```

## Estrutura

- `pidrofmbot.py`: aplicação principal
- `tests/test_pidrofmbot.py`: testes unitários básicos
- `.env.example`: exemplo de configuração

## ZIP para distribuição

Você pode gerar um pacote ZIP com:

```bash
zip -r pidrofmbot-package.zip . -x '.git/*' '.pytest_cache/*' '__pycache__/*' '.venv/*'
```
