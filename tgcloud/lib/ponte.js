import { api, db, BotApiError } from 'sdk';
import { and, asc, eq, gt, inArray, lt, sql } from 'sdk/db';
import { leituras, partes } from '../schema.js';
import { ESPERA_SEGUNDOS, LIMPEZA_SEGUNDOS, MIRA_PREFIXO, MIRA_USUARIO } from './config.js';
import { GRUPO_ID } from './grupo.js';
import { posicao } from './cartas.js';
import { agora } from './sessao.js';
import { dividir, editarTexto, enviarTexto } from './texto.js';

const ROTULOS = { tecnica: 'técnica', preditiva: 'preditiva' };

const CABECALHOS = {
  tecnica: '🧩 <b>Leitura técnica</b>',
  preditiva: '🌠 <b>Leitura preditiva geral</b>',
};

const blocoDeCartas = (cartas) => cartas
  .map((c, i) => `${i + 1}. ${c.nome} (${posicao(c.rev)})`)
  .join('\n');

const linhaDaTiragem = (tiragem) => (tiragem ? `Tiragem: ${tiragem}\n` : '');

const PEDIDOS = {
  tecnica: (cartas, tiragem) => (
    `${MIRA_PREFIXO} faz a LEITURA TÉCNICA dessas cartas do Tarot Rider-Waite-Smith.\n`
    + linhaDaTiragem(tiragem)
    + 'Vá carta por carta na ordem informada. Para cada uma traga: significado clássico, '
    + 'ponto positivo, ponto negativo e, se invertida, interprete a inversão explicitamente. '
    + 'No fim, comente combinações e dê uma visão global coerente com a tiragem.\n'
    + 'Responda em PORTUGUÊS DO BRASIL.\n\n'
    + `Cartas:\n${blocoDeCartas(cartas)}`
  ),
  preditiva: (cartas, tiragem) => (
    `${MIRA_PREFIXO} agora faz a LEITURA PREDITIVA GERAL dessas mesmas cartas.\n`
    + linhaDaTiragem(tiragem)
    + 'Seja ousada, direta e prática: o que tende a acontecer, prazos aproximados quando '
    + 'fizer sentido, riscos concretos, oportunidades e conselho final acionável. '
    + 'Não suavize o que for difícil, mas não afirme certezas absolutas. '
    + 'Responda em PORTUGUÊS DO BRASIL.\n\n'
    + `Cartas:\n${blocoDeCartas(cartas)}`
  ),
};

export async function perguntar(chatId, tipo, cartas, tiragem) {
  if (!GRUPO_ID) throw new Error('grupo da Mira não configurado (variável GROUP do deploy)');
  const enviada = await api.sendMessage({
    chat_id: GRUPO_ID,
    text: PEDIDOS[tipo](cartas, tiragem),
    link_preview_options: { is_disabled: true },
  });
  await db.insert(leituras).values({
    chatId,
    tipo,
    tiragem: tiragem || null,
    cartas,
    mensagemNoGrupo: enviada.message_id,
    estado: 'aguardando',
    criada: agora(),
  }).run();
  console.log('pedido enviado à Mira', { tipo, mensagem: enviada.message_id, chatId });
}

export const ehMira = (mensagem) => (
  mensagem.chat.id === GRUPO_ID
  && !!mensagem.from
  && String(mensagem.from.username || '').replace(/^@/, '').toLowerCase() === MIRA_USUARIO
);

const textoDe = (mensagem) => String(mensagem.text || mensagem.caption || '').trim();

async function leituraDaMensagem(mensagem) {
  const abertas = await db.select().from(leituras)
    .where(and(
      inArray(leituras.estado, ['aguardando', 'respondida']),
      gt(leituras.criada, agora() - ESPERA_SEGUNDOS),
    ))
    .orderBy(asc(leituras.id))
    .all();
  const alvo = mensagem.reply_to_message && mensagem.reply_to_message.message_id;
  const formal = abertas.find((l) => l.mensagemNoGrupo === alvo);
  if (formal) return formal;
  const aguardando = abertas.filter((l) => l.estado === 'aguardando');
  if (aguardando.length === 1) return aguardando[0];
  if (abertas.length === 1) return abertas[0];
  return null;
}

async function apagarSilencioso(chat_id, message_id) {
  try {
    await api.deleteMessage({ chat_id, message_id });
  } catch (e) {
    if (!(e instanceof BotApiError)) throw e;
    console.warn('mensagem não apagada:', e.code, e.description);
  }
}

async function atualizarParte(parte, chatId, novoTexto) {
  if (parte.texto === novoTexto) return;
  const antigos = dividir(parte.texto);
  const novos = dividir(novoTexto);
  await db.update(partes).set({ texto: novoTexto }).where(eq(partes.mensagemDaMira, parte.mensagemDaMira)).run();
  const ids = [...parte.entregues];
  if (!ids.length) return;
  for (let i = 0; i < novos.length; i++) {
    if (i >= ids.length) {
      ids.push((await enviarTexto(chatId, novos[i])).message_id);
    } else if (novos[i] !== antigos[i]) {
      await editarTexto(chatId, ids[i], novos[i]);
    }
  }
  while (ids.length > novos.length) await apagarSilencioso(chatId, ids.pop());
  await db.update(partes).set({ entregues: ids }).where(eq(partes.mensagemDaMira, parte.mensagemDaMira)).run();
}

async function entregarParte(leitura, mensagemDaMira, texto) {
  const ids = [];
  try {
    for (const pedaco of dividir(texto)) ids.push((await enviarTexto(leitura.chatId, pedaco)).message_id);
  } finally {
    await db.update(partes).set({ entregues: ids }).where(eq(partes.mensagemDaMira, mensagemDaMira)).run();
  }
  const atual = await db.select().from(partes).where(eq(partes.mensagemDaMira, mensagemDaMira)).get();
  if (atual && atual.texto !== texto) {
    await atualizarParte({ ...atual, texto, entregues: ids }, leitura.chatId, atual.texto);
  }
}

async function seguirDepoisDaPrimeiraParte(leitura) {
  if (leitura.tipo === 'tecnica') {
    try {
      await perguntar(leitura.chatId, 'preditiva', leitura.cartas, leitura.tiragem);
    } catch (e) {
      console.error('pedido da leitura preditiva falhou:', (e && e.message) || String(e));
      await api.sendMessage({ chat_id: leitura.chatId, text: '⚠️ Não consegui pedir a leitura preditiva à Mira.' });
    }
    return;
  }
  await api.sendMessage({ chat_id: leitura.chatId, text: '✨ Para uma nova leitura, use /ler ou /tirar.' });
}

export async function receberDaMira(mensagem) {
  const texto = textoDe(mensagem);
  if (!texto) return;

  const existente = await db.select().from(partes).where(eq(partes.mensagemDaMira, mensagem.message_id)).get();
  if (existente) {
    const dona = await db.select().from(leituras).where(eq(leituras.id, existente.leituraId)).get();
    if (dona) await atualizarParte(existente, dona.chatId, texto);
    return;
  }

  const leitura = await leituraDaMensagem(mensagem);
  if (!leitura) {
    console.warn('mensagem da Mira sem leitura aberta', { mensagem: mensagem.message_id });
    return;
  }

  const nova = await db.insert(partes)
    .values({ mensagemDaMira: mensagem.message_id, leituraId: leitura.id, texto, entregues: [] })
    .onConflictDoNothing()
    .run();
  if (Number(nova.rowsAffected) !== 1) return;

  const marcada = await db.update(leituras).set({ estado: 'respondida' })
    .where(and(eq(leituras.id, leitura.id), eq(leituras.estado, 'aguardando')))
    .run();
  const primeira = Number(marcada.rowsAffected) === 1;

  if (primeira) await api.sendMessage({ chat_id: leitura.chatId, text: CABECALHOS[leitura.tipo], parse_mode: 'HTML' });
  try {
    await entregarParte(leitura, mensagem.message_id, texto);
  } catch (e) {
    console.error('entrega da leitura falhou:', (e && e.description) || (e && e.message) || String(e));
  }
  if (primeira) await seguirDepoisDaPrimeiraParte(leitura);
}

export async function avisarAtrasadas(chatId) {
  const agoraS = agora();
  const vencidas = await db.select().from(leituras)
    .where(and(
      eq(leituras.chatId, chatId),
      eq(leituras.estado, 'aguardando'),
      lt(leituras.criada, agoraS - ESPERA_SEGUNDOS),
    ))
    .all();
  for (const leitura of vencidas) {
    const marcada = await db.update(leituras).set({ estado: 'expirada' })
      .where(and(eq(leituras.id, leitura.id), eq(leituras.estado, 'aguardando')))
      .run();
    if (Number(marcada.rowsAffected) === 1) {
      await api.sendMessage({ chat_id: chatId, text: `⚠️ A leitura ${ROTULOS[leitura.tipo]} não chegou a tempo.` });
    }
  }
  const limite = agoraS - LIMPEZA_SEGUNDOS;
  await db.run(sql`DELETE FROM partes WHERE leitura_id IN (SELECT id FROM leituras WHERE criada < ${limite})`);
  await db.delete(leituras).where(lt(leituras.criada, limite)).run();
}

export async function cancelarLeituras(chatId) {
  await db.update(leituras).set({ estado: 'cancelada' })
    .where(and(eq(leituras.chatId, chatId), inArray(leituras.estado, ['aguardando', 'respondida'])))
    .run();
}
