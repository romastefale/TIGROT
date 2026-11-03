import { api } from 'sdk';
import { TODAS, semAcento } from './cartas.js';
import { apagarSessao } from './sessao.js';
import { menuGrupos, menuTiragens } from './teclados.js';
import { avisarAtrasadas, cancelarLeituras } from './ponte.js';

const COMANDO = /^\/([A-Za-z0-9_]+)(?:@([A-Za-z0-9_]+))?(?:\s+([\s\S]*))?$/;

const html = (chat_id, text, reply_markup) => api.sendMessage({ chat_id, text, parse_mode: 'HTML', reply_markup });

const COMANDOS = {
  start: (chatId) => html(
    chatId,
    '🔮 <b>Bem-vindo ao Tarot Interativo</b>\n\n'
    + 'Aqui você pode explorar o <b>Tarot Rider-Waite</b> de forma clara, intuitiva e completa.\n\n'
    + '<b>Comandos disponíveis:</b>\n'
    + '/ler - 🃏 <b>Escolher cartas manualmente:</b> Selecione você mesmo as cartas e suas posições (normal ou invertida) para uma leitura personalizada.\n'
    + '/tirar - 🎲 <b>Fazer tiragem automática guiada:</b> Escolha um método de tiragem e o bot sorteará e interpretará as cartas para você.\n'
    + '/buscar - 🔎 <b>Buscar carta pelo nome:</b> Encontre rapidamente uma carta específica no baralho (ex: /buscar torre).\n'
    + '/reset - ♻️ <b>Limpar sessão atual:</b> Apaga suas seleções atuais e reinicia a leitura do zero.\n\n'
    + '👇 <i>Ou use os botões abaixo para iniciar sua interpretação manualmente</i>',
    menuGrupos(),
  ),
  ler: (chatId) => html(
    chatId,
    '🃏 <b>Escolher cartas manualmente</b>\n\n'
    + 'Neste modo, você mesmo escolhe as cartas e suas posições (normal ou invertida) para criar uma leitura personalizada.\n\n'
    + '👇 <i>Use os botões abaixo para iniciar sua seleção:</i>',
    menuGrupos(),
  ),
  tirar: (chatId) => html(
    chatId,
    '🎲 <b>Escolha a tiragem</b>\n\nSelecione um formato abaixo para eu preparar a leitura com o tipo certo de interpretação.',
    menuTiragens(),
  ),
  reset: async (chatId, args, message) => {
    await apagarSessao(message.from ? message.from.id : chatId);
    await cancelarLeituras(chatId);
    await api.sendMessage({ chat_id: chatId, text: '♻️ Sessão resetada.' });
  },
  buscar: async (chatId, args) => {
    if (!args) {
      await api.sendMessage({ chat_id: chatId, text: 'Use assim:\n/buscar torre' });
      return;
    }
    const termo = semAcento(args);
    const achadas = TODAS.filter((nome) => semAcento(nome).includes(termo));
    if (!achadas.length) {
      await api.sendMessage({ chat_id: chatId, text: 'Nenhuma carta encontrada.' });
      return;
    }
    await api.sendMessage({ chat_id: chatId, text: `🔎 Resultados:\n\n${achadas.slice(0, 20).map((nome) => `• ${nome}`).join('\n')}` });
  },
};

export async function executarComando(message) {
  const partes = COMANDO.exec(String(message.text || '').trim());
  if (!partes) return;
  const [, nome, destino, args] = partes;
  const comando = COMANDOS[nome.toLowerCase()];
  if (!comando) return;
  if (destino) {
    const eu = await api.getMe();
    if (destino.toLowerCase() !== String(eu.username).toLowerCase()) return;
  }
  const chatId = message.chat.id;
  await avisarAtrasadas(chatId);
  await comando(chatId, String(args || '').trim(), message);
}
