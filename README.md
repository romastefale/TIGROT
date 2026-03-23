# Pidro FM Bot

Bot do Telegram para buscar músicas na Deezer e compartilhar resultados no chat ou no modo inline, com trecho relevante da letra via Genius quando o usuário escolher essa opção.

## Funcionalidades

- Busca por texto no chat
- Busca inline no Telegram
- Comando `/start` com instruções de uso
- Confirmação `♪ Lyrics?` antes de enviar a música no chat
- Botão `Yes` para enviar a música com trecho da letra do Genius
- Botão `No` para enviar a música no formato normal
- Paginação com botão `Load more`
- Envio de capa do álbum com legenda formatada
- Suporte a `polling` e `webhook`

## Requisitos

- Python 3.10+
- Token do bot do Telegram
- Token da API do Genius para usar a opção de letra

## Instalação

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuração

Configure estas variáveis de ambiente:

- `TELEGRAM_TOKEN`: token do bot
- `WEBHOOK_URL`: URL pública para webhook (opcional)
- `WEBHOOK_SECRET`: segredo do webhook (opcional)
- `PORT`: porta da aplicação (padrão `8443`)
- `GENIUS_ACCESS_TOKEN`: token da API do Genius para buscar o trecho da música

## Execução local

```bash
export TELEGRAM_TOKEN="seu-token"
export GENIUS_ACCESS_TOKEN="seu-token-genius"  # opcional, necessário para o botão Yes
python main.py
```

Se `WEBHOOK_URL` não estiver definido, o bot roda em modo polling.

## Fluxo no chat

1. Use `/start` para ver a mensagem de apresentação do bot.
2. Digite o nome de uma música ou artista no chat.
3. Escolha a música desejada na lista retornada.
4. O bot perguntará `♪ Lyrics?`.
5. Toque em `No` para enviar a música normalmente ou em `Yes` para enviar com o bloco final:

```text
♪ Lyrics

<trecho da música>
```

## Testes

Comando disponível no repositório:

```bash
pytest
```

No estado atual do repositório, a coleta falha porque `tests/test_pidrofmbot.py` contém conteúdo de diff em vez de um teste Python válido.

## Estrutura

- `main.py`: aplicação principal
- `requirements.txt`: dependências do projeto
- `tests/test_pidrofmbot.py`: arquivo de testes atualmente inválido no repositório
