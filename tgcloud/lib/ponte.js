import { api, db, BotApiError } from 'sdk';
import { and, asc, eq, gt, inArray, lt, sql } from 'sdk/db';
import { leituras, partes } from '../schema.js';
import { ESPERA_SEGUNDOS, LIMPEZA_SEGUNDOS, MIRA_PREFIXO, MIRA_USUARIO } from './config.js';
import { GRUPO_ID } from './grupo.js';
import { posicao } from './cartas.js';
import { agora } from './sessao.js';

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
  if (!GRUPO_ID) throw Object.assign(new Error('grupo da Mira não configurado neste deploy'), { semGrupo: true });
  const enviada = await api.sendMessage({
    chat_id: GRUPO_ID,
    text: PEDIDOS[tipo](cartas, tiragem),
    link_preview_options: { is_disabled: true },
  });
  try {
    await db.insert(leituras).values({
      chatId: Number(chatId),
      tipo,
      tiragem: tiragem || null,
      cartas,
      mensagemNoGrupo: enviada.message_id,
      estado: 'aguardando',
      criada: agora(),
    }).run();
  } catch (e) {
    throw Object.assign(new Error('pedido enviado, mas não registrado'), { semRegistro: true, causa: e });
  }
  console.log('pedido enviado à Mira', { tipo, mensagem: enviada.message_id, chatId });
}

export const detalheDaFalha = (e) => (e && (e.description || (e.causa && e.causa.message) || e.message)) || String(e);

function motivoDaFalha(e) {
  if (e && e.semGrupo) return 'O grupo da Mira não está configurado neste deploy.';
  if (e instanceof BotApiError) {
    if (e.code === 403) return 'O bot não tem acesso ao grupo da Mira.';
    if (e.code === 429) {
      const espera = e.parameters && e.parameters.retry_after;
      return espera ? `O Telegram pediu para esperar ${espera} s.` : 'O Telegram pediu para esperar um pouco.';
    }
    if (e.code === 400) return 'O Telegram não aceitou o grupo configurado.';
  }
  return 'Erro inesperado ao falar com o grupo.';
}

export function avisoDaFalha(tipo, e) {
  if (e && e.semRegistro) {
    return `⚠️ O pedido da leitura ${ROTULOS[tipo]} foi enviado à Mira, mas não consegui registrá-lo; a resposta pode não ser repassada.`;
  }
  return `⚠️ Não consegui enviar o pedido da leitura ${ROTULOS[tipo]} à Mira. ${motivoDaFalha(e)}`;
}

export const mesmoId = (a, b) => a !== undefined && a !== null && b !== undefined && b !== null && String(a) === String(b);

export const ehMira = (mensagem) => (
  !!GRUPO_ID
  && mesmoId(mensagem.chat.id, GRUPO_ID)
  && !!mensagem.from
  && String(mensagem.from.username || '').replace(/^@/, '').toLowerCase() === MIRA_USUARIO
);

const textoDe = (mensagem) => String(mensagem.text || mensagem.caption || '');

async function leituraDaMensagem(mensagem) {
  const abertas = await db.select().from(leituras)
    .where(and(
      inArray(leituras.estado, ['aguardando', 'respondida']),
      gt(leituras.criada, agora() - ESPERA_SEGUNDOS),
    ))
    .orderBy(asc(leituras.id))
    .all();
  const alvo = mensagem.reply_to_message && mensagem.reply_to_message.message_id;
  const formal = abertas.find((l) => mesmoId(l.mensagemNoGrupo, alvo));
  if (formal) return formal;
  const aguardando = abertas.filter((l) => l.estado === 'aguardando');
  if (aguardando.length === 1) return aguardando[0];
  if (abertas.length === 1) return abertas[0];
  return null;
}

async function copiarParaOUsuario(mensagem, chat_id) {
  try {
    const copia = await api.copyMessage({
      chat_id,
      from_chat_id: Number(mensagem.chat.id),
      message_id: Number(mensagem.message_id),
    });
    return Number(copia.message_id);
  } catch (e) {
    if (!(e instanceof BotApiError) || mensagem.text === undefined) throw e;
    console.warn('copyMessage recusado, reenviando o texto com as mesmas entidades:', e.code, e.description);
    const enviada = await api.sendMessage({ chat_id, text: mensagem.text, entities: mensagem.entities });
    return Number(enviada.message_id);
  }
}

async function atualizarParte(parte, chat_id, mensagem) {
  const novo = textoDe(mensagem);
  if (parte.texto === novo) return;
  await db.update(partes).set({ texto: novo }).where(eq(partes.mensagemDaMira, parte.mensagemDaMira)).run();
  const message_id = parte.entregues[0];
  if (!message_id) return;
  try {
    if (mensagem.text !== undefined) {
      await api.editMessageText({ chat_id, message_id, text: mensagem.text, entities: mensagem.entities });
    } else if (mensagem.caption !== undefined) {
      await api.editMessageCaption({ chat_id, message_id, caption: mensagem.caption, caption_entities: mensagem.caption_entities });
    }
  } catch (e) {
    if (!(e instanceof BotApiError)) throw e;
    console.warn('edição da cópia recusada:', e.code, e.description);
  }
}

async function entregarParte(leitura, mensagem) {
  const copia = await copiarParaOUsuario(mensagem, Number(leitura.chatId));
  await db.update(partes).set({ entregues: [copia] }).where(eq(partes.mensagemDaMira, Number(mensagem.message_id))).run();
}

async function seguirDepoisDaPrimeiraParte(leitura) {
  if (leitura.tipo === 'tecnica') {
    try {
      await perguntar(Number(leitura.chatId), 'preditiva', leitura.cartas, leitura.tiragem);
    } catch (e) {
      console.error('pedido da leitura preditiva falhou:', detalheDaFalha(e));
      await api.sendMessage({ chat_id: Number(leitura.chatId), text: avisoDaFalha('preditiva', e) });
    }
    return;
  }
  await api.sendMessage({ chat_id: Number(leitura.chatId), text: '✨ Para uma nova leitura, use /ler ou /tirar.' });
}

export async function receberDaMira(mensagem) {
  const existente = await db.select().from(partes).where(eq(partes.mensagemDaMira, Number(mensagem.message_id))).get();
  if (existente) {
    const dona = await db.select().from(leituras).where(eq(leituras.id, existente.leituraId)).get();
    if (dona) await atualizarParte(existente, Number(dona.chatId), mensagem);
    return;
  }

  const leitura = await leituraDaMensagem(mensagem);
  if (!leitura) {
    console.warn('mensagem da Mira sem leitura aberta', { mensagem: mensagem.message_id });
    return;
  }

  const nova = await db.insert(partes)
    .values({ mensagemDaMira: Number(mensagem.message_id), leituraId: leitura.id, texto: textoDe(mensagem), entregues: [] })
    .onConflictDoNothing()
    .run();
  if (Number(nova.rowsAffected) !== 1) return;

  const marcada = await db.update(leituras).set({ estado: 'respondida' })
    .where(and(eq(leituras.id, leitura.id), eq(leituras.estado, 'aguardando')))
    .run();
  const primeira = Number(marcada.rowsAffected) === 1;

  if (primeira) await api.sendMessage({ chat_id: Number(leitura.chatId), text: CABECALHOS[leitura.tipo], parse_mode: 'HTML' });
  try {
    await entregarParte(leitura, mensagem);
  } catch (e) {
    console.error('entrega da leitura falhou:', (e && e.description) || (e && e.message) || String(e));
    await api.sendMessage({ chat_id: Number(leitura.chatId), text: '⚠️ A resposta da Mira chegou, mas não consegui repassá-la.' });
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
