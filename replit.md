# TIGROT — Bot de Tarô

## Visão geral
Bot de Telegram (python-telegram-bot 21.6) que opera no grupo `-5258446619`.
Sorteia/seleciona cartas do baralho Rider-Waite-Smith (78 cartas em
`assets/rws-png/`) e, em vez de chamar uma LLM diretamente, encaminha as
perguntas para o bot **@mira** no mesmo grupo. A interpretação devolvida pela
@mira é repassada ao usuário no chat original.

Para cada tiragem o bot pede DUAS leituras ao @mira:

1. **Leitura técnica** — carta a carta, significado clássico, pontos
   positivos/negativos, combinações e visão global.
2. **Leitura preditiva geral** — ousada, direta e prática (tendências,
   prazos, riscos, oportunidades e conselho acionável).

## Como funciona a ponte
- Quando precisa interpretar, o bot envia no grupo `TIGROT_GROUP_ID` uma
  mensagem prefixada com `@mira` contendo o prompt da leitura.
- Um `MessageHandler` escuta o grupo e, quando vê uma mensagem em **reply**
  à nossa pergunta cujo `from_user.username` é `mira`, resolve um
  `asyncio.Future` registrado em `_pending_by_relay[message_id]`.
- O texto resolvido vira a resposta entregue ao usuário no chat original.
- Tempo limite por pergunta: `MIRA_WAIT_TIMEOUT` segundos (padrão 240).
  Em caso de timeout, o usuário recebe um aviso amigável e a tiragem segue.

Inspirado em `romastefale/TR3/app/bot/tigraoresponde.py` (sem modificar
aquele projeto).

## Stack
- Python 3.11+
- `python-telegram-bot==21.6`
- `Pillow` (rotação de cartas invertidas)
- `redis>=5.0.0` (sessões persistentes opcionais)

## Comandos
- `/start` — boas-vindas + menu de grupos.
- `/ler` — seleção manual de cartas (com posição normal/invertida).
- `/tirar` — menu de tiragens (Carta do Dia, P/P/F, S/D/C, Péladan,
  Ferradura, Cruz Celta, Mandala 12/13).
- `/buscar <termo>` — procura cartas pelo nome.
- `/reset` — limpa a sessão do usuário.

## Variáveis de ambiente
Obrigatórias:
- `BOT_TOKEN` (ou `TELEGRAM_BOT_TOKEN`) — token do BotFather.

Opcionais:
- `TIGROT_GROUP_ID` — padrão `-5258446619`.
- `MIRA_PREFIX` — padrão `@mira`.
- `MIRA_USERNAME` — padrão `mira` (sem `@`). Identifica quem é a @mira.
- `MIRA_WAIT_TIMEOUT` — padrão `240` segundos.
- `RWS_IMAGE_DIR` — padrão `assets/rws-png`.
- `REDIS_URL` — se vazio, sessões ficam só em memória.
- `REDIS_TTL_SECONDS` (padrão 3600).
- `SESSION_CLEANUP_SECONDS` (padrão 600).
- `SESSION_MAX_AGE_SECONDS` (padrão 1800).

## Estrutura
- `main.py` — bot completo (sessões, handlers, ponte @mira, sorteio).
- `assets/rws-png/` — 78 cartas PNG (RWS).
- `assets/rws/` — versões JPG (não usadas por padrão).
- `requirements.txt` — usado pelo Railway.

## Deploy
- Produção: Railway (rodando hoje).
- Replit: workflow `Start application` executa `python main.py`.

## User preferences
- Idioma: Português do Brasil em todas as mensagens.
- Prefixo da ponte: exatamente `@mira`.
- Sorteio aleatório (`random` padrão da lib).
- UX minimalista: poucos comandos, fluxo automático após a tiragem.
- Esta é uma ATUALIZAÇÃO do código que já roda em produção, não um
  rewrite — mudanças devem ser cirúrgicas.
