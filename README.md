# TIGROT — Bot de Tarô

Bot de Telegram 100% **Telegram Serverless**: o código roda nos módulos JavaScript de `tgcloud/`, na infraestrutura do Telegram, sem servidor próprio. Sorteia ou recebe cartas do baralho Rider-Waite-Smith (78 cartas) e, em vez de chamar uma LLM, marca o bot **@mira** na própria conversa do usuário com o bot (a DM) pedindo a interpretação. A @mira responde nessa mesma conversa.

Para cada tiragem o bot faz DUAS marcações à @mira, uma depois da outra:

1. **Leitura técnica**: carta a carta, significado clássico, pontos positivos e negativos, combinações e visão global.
2. **Leitura preditiva geral**: ousada, direta e prática (tendências, prazos, riscos, oportunidades e conselho acionável).

## Como funciona

O serverless não mantém processo vivo, não tem temporizador e executa cada update isoladamente. Por isso a espera pela @mira é guiada por eventos e guardada no banco:

- `tirrun:` (tiragem) e `fim` (seleção manual) enviam as cartas e, no mesmo chat, uma mensagem que começa com `@mira` com o pedido da leitura técnica. O pedido é gravado na tabela `pedidos` com estado `aguardando`.
- `handlers/message.js` recebe as mensagens do chat. Quando o autor é `mira`, a mensagem é ligada a um pedido do mesmo chat: por **resposta formal** ao pedido ou, sem ela, o pedido `aguardando` mais antigo.
- A primeira mensagem da @mira para um pedido o marca como `respondida`. Se era a técnica, o bot já marca a @mira pedindo a preditiva; se era a preditiva, o bot lembra os comandos `/ler` e `/tirar`.
- O bot não copia nem repete o texto da @mira: ela escreve direto na conversa. Edições das mensagens dela não são tratadas (não há handler de `edited_message`), e entregas repetidas do mesmo update ou mensagens que respondem formalmente a um pedido já respondido não disparam nada.
- Não existe como saber que a @mira terminou de escrever, então a preditiva é pedida quando a primeira mensagem da técnica chega. Uma segunda mensagem da @mira sem resposta formal, enquanto a preditiva aguarda, é tomada como resposta dela.
- Tempo limite: `ESPERA_SEGUNDOS` (600). Como não há temporizador, o aviso "não chegou a tempo" é enviado na próxima vez que o usuário usar um comando do bot, e mensagens da @mira para pedidos com mais tempo que isso não são ligadas a nenhum. Se a técnica nunca chegar, a preditiva não é pedida.

As imagens das cartas ficam em `web/cartas/` (normal e invertida, 512 px de largura) e são publicadas pelo próprio serverless em `https://app<id>.tgcloud.ai/cartas/`. O bot as envia por endereço em álbuns de até 10 fotos e guarda o `file_id` na tabela `fotos` para as próximas vezes. Se o endereço não estiver configurado ou o Telegram recusar as fotos, o bot envia só o nome e a posição das cartas.

## Comandos

- `/start`: boas-vindas e menu de grupos de cartas.
- `/ler`: seleção manual de cartas, com posição normal ou invertida.
- `/tirar`: menu de tiragens (Carta do Dia, Passado/Presente/Futuro, Situação/Desafio/Conselho, Péladan, Ferradura, Cruz Celta, Mandala 12/13).
- `/buscar <termo>`: procura cartas pelo nome, sem diferenciar acentos.
- `/reset`: limpa a seleção do usuário e cancela pedidos que ainda aguardam a @mira.

## Configuração

Não há variáveis de ambiente nem token no código: a Bot API é chamada pelo `api` do SDK. Os valores ficam em `tgcloud/lib/config.js`:

- `MIRA_PREFIXO` (`@mira`) e `MIRA_USUARIO` (`mira`, sem `@`, sem diferenciar maiúsculas).
- `ESPERA_SEGUNDOS` (600), `SESSAO_SEGUNDOS` (1800), `LIMPEZA_SEGUNDOS` (86400, quando pedidos antigos são apagados).
- `MAX_CARTAS` (12) e `CARTAS_POR_PAGINA` (8) na seleção manual.
- `URL_CARTAS`: endereço das imagens, preenchido depois do primeiro deploy.

## Montar (uma vez)

1. No [@BotFather](https://t.me/BotFather): bot → **Serverless** → ligar. Em **CLI Access**, copie o *Access token* (`app<id>:<secret>`; não é o token da Bot API).
2. No GitHub: Settings → Secrets and variables → Actions → secret **`TGCLOUD_TOKEN`** com esse valor.
3. A @mira precisa conseguir escrever na conversa dos usuários com o bot e o bot precisa receber as mensagens dela. O changelog da Bot API cita a *bot-to-bot communication*, que precisa estar ligada nos dois bots para um bot enviar mensagem a outro por username; não verifiquei se ela também governa esse caminho.
4. Merge na `main` (ou Actions → *Deploy* → Run workflow). O passo `push` imprime o endereço `https://app<id>.tgcloud.ai/`.
5. Coloque esse endereço, terminado em `/cartas/`, em `URL_CARTAS` (`tgcloud/lib/config.js`) e faça o deploy de novo. Até lá o bot envia só o nome e a posição de cada carta.

## Desenvolvimento

```bash
npm ci
npm run status
npm run run -- handlers/message '{ chat: { id: 1, type: "private" }, from: { id: 1 }, text: "/buscar torre" }'
```

Para gerar de novo as imagens a partir dos originais em `assets/rws/`:

```bash
npm --prefix scripts/cartas ci
npm run cartas
```

## Estrutura

- `tgcloud/schema.js`: tabelas `sessoes`, `pedidos` e `fotos`.
- `tgcloud/handlers/`: `message` e `callback_query`.
- `tgcloud/lib/`: cartas e tiragens, teclados, comandos, callbacks, ponte com a @mira e fotos.
- `web/cartas/`: 156 imagens (78 cartas, normal e invertida), geradas por `scripts/cartas/gerar.mjs`.
- `tgcloud.jsonc`: publica a pasta `web/`.
- `.github/workflows/deploy.yml`: a cada push em `main` que mude o bot, roda `fetch`, `push`, `migrate --safe` e `webhook`.

## Preferências do projeto

- Idioma: português do Brasil em todas as mensagens.
- Prefixo da ponte: exatamente `@mira`.
- Sorteio aleatório (`Math.random`).
- UX minimalista: poucos comandos e fluxo automático depois da tiragem.
- Sem comentários no código.
