import { api, db } from 'sdk';
import { and, asc, eq, gt, inArray, lt } from 'sdk/db';
import { pedidos } from '../schema.js';
import { ESPERA_SEGUNDOS, LIMPEZA_SEGUNDOS, MIRA_PREFIXO, MIRA_USUARIO } from './config.js';
import { posicao } from './cartas.js';
import { agora } from './sessao.js';

const ROTULOS = { tecnica: 'técnica', preditiva: 'preditiva' };

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
  const enviado = await api.sendMessage({
    chat_id: chatId,
    text: PEDIDOS[tipo](cartas, tiragem),
    link_preview_options: { is_disabled: true },
  });
  await db.insert(pedidos).values({
    chatId,
    tipo,
    tiragem: tiragem || null,
    cartas,
    mensagemId: enviado.message_id,
    estado: 'aguardando',
    resposta: null,
    criada: agora(),
  }).run();
  console.log('pedido enviado à Mira', { tipo, mensagem: enviado.message_id, chatId });
}

export const ehMira = (mensagem) => (
  !!mensagem.from
  && String(mensagem.from.username || '').replace(/^@/, '').toLowerCase() === MIRA_USUARIO
);

async function seguir(pedido) {
  if (pedido.tipo === 'tecnica') {
    try {
      await perguntar(pedido.chatId, 'preditiva', pedido.cartas, pedido.tiragem);
    } catch (e) {
      console.error('pedido da leitura preditiva falhou:', (e && e.message) || String(e));
      await api.sendMessage({ chat_id: pedido.chatId, text: '⚠️ Não consegui pedir a leitura preditiva à Mira.' });
    }
    return;
  }
  await api.sendMessage({ chat_id: pedido.chatId, text: '✨ Para uma nova leitura, use /ler ou /tirar.' });
}

export async function receberDaMira(mensagem) {
  const abertos = await db.select().from(pedidos)
    .where(and(
      eq(pedidos.chatId, mensagem.chat.id),
      inArray(pedidos.estado, ['aguardando', 'respondida']),
      gt(pedidos.criada, agora() - ESPERA_SEGUNDOS),
    ))
    .orderBy(asc(pedidos.id))
    .all();
  if (abertos.some((p) => p.resposta === mensagem.message_id)) return;
  const alvo = mensagem.reply_to_message && mensagem.reply_to_message.message_id;
  const pedido = abertos.find((p) => p.mensagemId === alvo) || abertos.find((p) => p.estado === 'aguardando');
  if (!pedido) {
    console.warn('mensagem da Mira sem pedido aguardando', { mensagem: mensagem.message_id, chat: mensagem.chat.id });
    return;
  }
  const marcado = await db.update(pedidos).set({ estado: 'respondida', resposta: mensagem.message_id })
    .where(and(eq(pedidos.id, pedido.id), eq(pedidos.estado, 'aguardando')))
    .run();
  if (Number(marcado.rowsAffected) === 1) await seguir(pedido);
}

export async function avisarAtrasadas(chatId) {
  const agoraS = agora();
  const vencidos = await db.select().from(pedidos)
    .where(and(
      eq(pedidos.chatId, chatId),
      eq(pedidos.estado, 'aguardando'),
      lt(pedidos.criada, agoraS - ESPERA_SEGUNDOS),
    ))
    .all();
  for (const pedido of vencidos) {
    const marcado = await db.update(pedidos).set({ estado: 'expirada' })
      .where(and(eq(pedidos.id, pedido.id), eq(pedidos.estado, 'aguardando')))
      .run();
    if (Number(marcado.rowsAffected) === 1) {
      await api.sendMessage({ chat_id: chatId, text: `⚠️ A leitura ${ROTULOS[pedido.tipo]} não chegou a tempo.` });
    }
  }
  await db.delete(pedidos).where(lt(pedidos.criada, agoraS - LIMPEZA_SEGUNDOS)).run();
}

export async function cancelarLeituras(chatId) {
  await db.update(pedidos).set({ estado: 'cancelada' })
    .where(and(eq(pedidos.chatId, chatId), inArray(pedidos.estado, ['aguardando', 'respondida'])))
    .run();
}
