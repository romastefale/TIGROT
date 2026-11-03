import { api, BotApiError } from 'sdk';
import { NAIPES, TIRAGENS, TODAS, sortear } from './cartas.js';
import { MAX_CARTAS } from './config.js';
import { apagarSessao, carregarSessao, salvarSessao } from './sessao.js';
import { iniciarLeitura } from './leitura.js';
import { escapar } from './texto.js';
import {
  TELA_MANDALA, TELA_TIRAGENS, menuCartas, menuConfirmar, menuContinuar, menuGrupos, menuMandala,
  menuPosicao, menuTiragens, previaTiragem, semBotoes, textoSelecionadas,
} from './teclados.js';

const GRUPOS = ['major', ...NAIPES];

async function tolerar(promessa) {
  try {
    return await promessa;
  } catch (e) {
    if (!(e instanceof BotApiError)) throw e;
    console.warn('chamada recusada pelo Telegram:', e.method, e.code, e.description);
    return null;
  }
}

export async function tratarCallback(consulta) {
  const dados = String(consulta.data || '');
  const chatId = consulta.message && consulta.message.chat.id;
  const mensagemId = consulta.message && consulta.message.message_id;
  const userId = consulta.from.id;

  const confirmar = () => tolerar(api.answerCallbackQuery({ callback_query_id: consulta.id }));
  const alertar = (text) => tolerar(api.answerCallbackQuery({ callback_query_id: consulta.id, text, show_alert: true }));
  const tela = (text, reply_markup) => tolerar(api.editMessageText({
    chat_id: chatId, message_id: mensagemId, text, parse_mode: 'HTML', reply_markup,
  }));
  const limparBotoes = () => tolerar(api.editMessageReplyMarkup({
    chat_id: chatId, message_id: mensagemId, reply_markup: semBotoes(),
  }));

  if (!chatId) {
    await alertar('Mensagem indisponível.');
    return;
  }

  if (dados === 'tirback') {
    await confirmar();
    await tela(TELA_TIRAGENS, menuTiragens());
    return;
  }

  if (dados === 'tir:mandala') {
    await confirmar();
    await tela(TELA_MANDALA, menuMandala());
    return;
  }

  if (dados.startsWith('tir:')) {
    const id = dados.slice(4);
    if (!TIRAGENS[id]) {
      await alertar('Tiragem inválida.');
      return;
    }
    await confirmar();
    await tela(previaTiragem(id), menuConfirmar(id));
    return;
  }

  if (dados.startsWith('tirrun:')) {
    const id = dados.slice(7);
    const info = TIRAGENS[id];
    if (!info) {
      await alertar('Tiragem inválida.');
      return;
    }
    await confirmar();
    await limparBotoes();
    const cartas = sortear(info.count);
    await api.sendMessage({
      chat_id: chatId,
      text: `🎴 <b>${escapar(info.nome)}</b>\n\n`
        + `📌 Tiragem selecionada: <b>${escapar(info.label)}</b>\n`
        + `🃏 Cartas sorteadas: <b>${info.count}</b>\n`
        + '🔎 Agora a leitura será enviada em partes.',
      parse_mode: 'HTML',
    });
    await iniciarLeitura(chatId, cartas, info.nome);
    return;
  }

  if (dados.startsWith('tircancel:')) {
    await confirmar();
    if (dados.slice(10).startsWith('mandala')) await tela(TELA_MANDALA, menuMandala());
    else await tela(TELA_TIRAGENS, menuTiragens());
    return;
  }

  if (dados.startsWith('g:')) {
    const grupo = dados.slice(2);
    if (!GRUPOS.includes(grupo)) {
      await alertar('Grupo inválido.');
      return;
    }
    await confirmar();
    const sessao = await carregarSessao(userId);
    await salvarSessao({ ...sessao, grupo, pagina: 0, pendente: null });
    await tela('Escolha:', menuCartas(grupo, 0));
    return;
  }

  if (dados.startsWith('nav:')) {
    const [, grupo, pagina] = dados.split(':');
    const numero = Number.parseInt(pagina, 10);
    if (!GRUPOS.includes(grupo) || !Number.isInteger(numero) || numero < 0) {
      await alertar('Página inválida.');
      return;
    }
    await confirmar();
    const sessao = await carregarSessao(userId);
    await salvarSessao({ ...sessao, grupo, pagina: numero });
    await tolerar(api.editMessageReplyMarkup({
      chat_id: chatId, message_id: mensagemId, reply_markup: menuCartas(grupo, numero),
    }));
    return;
  }

  if (dados === 'back' || dados === 'cont') {
    await confirmar();
    const sessao = await carregarSessao(userId);
    await salvarSessao({ ...sessao, pendente: null });
    await tela('Escolha:', menuGrupos());
    return;
  }

  if (dados.startsWith('c:')) {
    const carta = dados.slice(2);
    if (!TODAS.includes(carta)) {
      await alertar('Carta inválida.');
      return;
    }
    await confirmar();
    const sessao = await carregarSessao(userId);
    await salvarSessao({ ...sessao, pendente: carta });
    await tela(`🃏 <b>${escapar(carta)}</b>\n\nEscolha a posição:`, menuPosicao());
    return;
  }

  if (dados === 'p:n' || dados === 'p:r') {
    const sessao = await carregarSessao(userId);
    if (!sessao.pendente) {
      await alertar('Escolha uma carta primeiro');
      return;
    }
    if (sessao.cartas.length >= MAX_CARTAS) {
      await alertar('Limite de cartas atingido');
      return;
    }
    await confirmar();
    const cartas = [...sessao.cartas, { nome: sessao.pendente, rev: dados === 'p:r' }];
    await salvarSessao({ ...sessao, cartas, pendente: null });
    await tela(escapar(textoSelecionadas(cartas)), menuContinuar());
    return;
  }

  if (dados === 'fim') {
    const sessao = await carregarSessao(userId);
    if (!sessao.cartas.length) {
      await alertar('Selecione ao menos uma carta.');
      return;
    }
    await confirmar();
    await apagarSessao(userId);
    await limparBotoes();
    await iniciarLeitura(chatId, sessao.cartas, null);
    return;
  }

  await alertar('Ação desconhecida.');
}
