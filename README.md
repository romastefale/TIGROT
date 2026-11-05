# TIGROT — Bot de Tarô

Bot de Telegram 100% **Telegram Serverless**: o código roda nos módulos JavaScript de `tgcloud/`, na infraestrutura do Telegram, sem servidor próprio. Sorteia ou recebe cartas do baralho Rider-Waite-Smith (78 cartas) e, em vez de chamar uma LLM, pede a interpretação ao bot **@mira** no grupo privado configurado no deploy. A resposta da @mira é repassada ao usuário no chat original.

Para cada tiragem o bot pede DUAS leituras à @mira, uma depois da outra:

1. **Leitura técnica**: carta a carta, significado clássico, pontos positivos e negativos, combinações e visão global.
2. **Leitura preditiva geral**: ousada, direta e prática (tendências, prazos, riscos, oportunidades e conselho acionável).

## Como funciona

O serverless não mantém processo vivo, não tem temporizador e executa cada update isoladamente. Por isso a espera pela @mira é guiada por eventos e guardada no banco:

- `tirrun:` (tiragem) e `fim` (seleção manual) enviam as cartas ao usuário, mandam o pedido da leitura técnica ao grupo (mensagem que começa com `@mira`) e gravam a leitura na tabela `leituras` com estado `aguardando`.
- `handlers/message.js` recebe as mensagens do grupo. Quando o autor é `mira`, a mensagem é ligada à leitura por **resposta formal** ao nosso pedido; sem resposta formal, vale a única leitura `aguardando` e, na falta dela, a única leitura aberta. Se houver mais de uma possível, a mensagem é ignorada.
- A primeira mensagem da @mira para uma leitura marca a leitura como `respondida`, envia o título (🧩 ou 🌠) e o texto ao usuário e, se era a técnica, já pede a preditiva. Ao terminar a preditiva, o bot lembra os comandos `/ler` e `/tirar`.
- `handlers/edited_message.js` recebe as edições. Se a @mira edita a mensagem (resposta em streaming), as mensagens já entregues ao usuário são editadas; se o texto ficou maior, as partes novas são enviadas; se ficou menor, as sobras são apagadas.
- Não existe como saber que a @mira terminou de escrever, então o bot entrega a primeira versão e a mantém atualizada, em vez de esperar um tempo sem mudanças.
- Tempo limite: `ESPERA_SEGUNDOS` (600). Como não há temporizador, o aviso "não chegou a tempo" é enviado na próxima vez que o usuário usar um comando do bot, e mensagens da @mira para leituras com mais tempo que isso não são ligadas a nenhuma leitura. Se a técnica nunca chegar, a preditiva não é pedida.
- Textos da @mira passam por conversão de `**negrito**`, `*itálico*` e `__sublinhado__` para HTML do Telegram, são divididos em partes de até 3800 caracteres e, se o Telegram recusar o HTML, são reenviados como texto simples.

As imagens das cartas ficam em `web/cartas/` (normal e invertida, 512 px de largura) e são publicadas pelo próprio serverless em `https://app<id>.tgcloud.ai/cartas/`. O bot as envia por endereço em álbuns de até 10 fotos e guarda o `file_id` na tabela `fotos` para as próximas vezes. Se o endereço não estiver configurado ou o Telegram recusar as fotos, o bot envia só o nome e a posição das cartas.

## Falhas e o que o bot diz

O bot só diz que pediu a leitura depois de o Telegram aceitar o pedido no grupo, e só então envia "Preparando a interpretação das cartas...". Quando o pedido falha, o usuário recebe o motivo, escolhido pelo código do erro da Bot API (o texto do erro não é usado):

- sem grupo configurado no deploy (`GRUPO_ID` igual a `0`): "O grupo da Mira não está configurado neste deploy.";
- erro 403: "O bot não tem acesso ao grupo da Mira.";
- erro 400: "O Telegram não aceitou o grupo configurado.";
- erro 429: "O Telegram pediu para esperar N s.";
- outro erro: "Erro inesperado ao falar com o grupo.";
- pedido aceito pelo Telegram, mas falha ao gravar no banco: o aviso diz que o pedido foi enviado e que a resposta pode não ser repassada.

Se o Telegram informar que o grupo virou supergrupo (`migrate_to_chat_id`), o bot reenvia o pedido ao novo id e guarda a troca na tabela `ajustes`; a troca só vale enquanto o `GRUPO_ID` do deploy for o mesmo. Mensagens da @mira são aceitas só do grupo configurado (ou do novo id guardado).

Não há como repetir um envio depois de uma espera, porque o serverless não tem temporizador. Por isso o bot não tenta de novo sozinho: avisa o usuário, que pode pedir a tiragem outra vez.

## Comandos

- `/start`: boas-vindas e menu de grupos de cartas.
- `/ler`: seleção manual de cartas, com posição normal ou invertida.
- `/tirar`: menu de tiragens (Carta do Dia, Passado/Presente/Futuro, Situação/Desafio/Conselho, Péladan, Ferradura, Cruz Celta, Mandala 12/13).
- `/buscar <termo>`: procura cartas pelo nome, sem diferenciar acentos.
- `/reset`: limpa a seleção do usuário e cancela leituras que ainda aguardam a @mira.

## Configuração

Não há variáveis de ambiente nem token no código: a Bot API é chamada pelo `api` do SDK. Os valores ficam em `tgcloud/lib/config.js`, e o id do grupo vem do secret `GROUP` do GitHub Actions (abaixo). O valor do `GROUP` nunca é impresso: o bot não o escreve em log nem em mensagem, e o workflow só o lê como secret (o Actions o mascara).

- `GRUPO_ID`, em `tgcloud/lib/grupo.js`: grupo privado onde está a @mira. O arquivo guardado no repositório traz `0` (não configurado). A cada deploy o workflow lê o secret `GROUP`, tira espaços das pontas e exige um id negativo, com o sinal e sem zeros à esquerda (por exemplo `-1001234567890`); então reescreve o arquivo. Se o secret estiver vazio ou fora desse formato, o deploy para e informa só a causa (sem mostrar o valor), para não apagar o grupo que já estava publicado.
- `MIRA_PREFIXO` (`@mira`) e `MIRA_USUARIO` (`mira`, sem `@`, sem diferenciar maiúsculas).
- `ESPERA_SEGUNDOS` (600), `SESSAO_SEGUNDOS` (1800), `LIMPEZA_SEGUNDOS` (86400, quando leituras antigas são apagadas).
- `MAX_CARTAS` (12) e `CARTAS_POR_PAGINA` (8) na seleção manual.
- `URL_CARTAS`: endereço das imagens, preenchido depois do primeiro deploy.

## Montar (uma vez)

1. No [@BotFather](https://t.me/BotFather): bot → **Serverless** → ligar. Em **CLI Access**, copie o *Access token* (`app<id>:<secret>`; não é o token da Bot API).
2. No GitHub: Settings → Secrets and variables → Actions → secret **`TGCLOUD_TOKEN`** com esse valor, e secret **`GROUP`** com o id do grupo, com o sinal. Use secret, não variável: o Actions só mascara secrets nos logs, e este repositório é público.
3. O bot precisa ser admin do grupo privado onde está a @mira (assim recebe todas as mensagens do grupo) e a comunicação entre bots precisa estar ligada nos dois bots. Esses dois pontos são configuração do grupo e do BotFather, feita por você, e o código não consegue verificá-los.
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

- `tgcloud/schema.js`: tabelas `sessoes`, `leituras`, `partes`, `ajustes` e `fotos`.
- `tgcloud/handlers/`: `message`, `edited_message` e `callback_query`.
- `tgcloud/lib/`: cartas e tiragens, teclados, comandos, callbacks, ponte com a @mira, fotos e formatação de texto.
- `web/cartas/`: 156 imagens (78 cartas, normal e invertida), geradas por `scripts/cartas/gerar.mjs`.
- `tgcloud.jsonc`: publica a pasta `web/`.
- `.github/workflows/deploy.yml`: a cada push em `main` que mude o bot, roda `fetch`, `push`, `migrate --safe` e `webhook`.

## Preferências do projeto

- Idioma: português do Brasil em todas as mensagens.
- Prefixo da ponte: exatamente `@mira`.
- Sorteio aleatório (`Math.random`).
- UX minimalista: poucos comandos e fluxo automático depois da tiragem.
- Sem comentários no código.
