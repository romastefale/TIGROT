import { MAIORES, MENORES, TIRAGENS, posicao } from './cartas.js';
import { CARTAS_POR_PAGINA, MAX_CARTAS } from './config.js';
import { escapar } from './texto.js';

const botao = (text, callback_data) => ({ text, callback_data });
const teclado = (linhas) => ({ inline_keyboard: linhas });

export const menuGrupos = () => teclado([
  [botao('🃏 Arcanos Maiores', 'g:major')],
  [botao('❤️ Copas', 'g:Copas')],
  [botao('🔥 Paus', 'g:Paus')],
  [botao('⚔️ Espadas', 'g:Espadas')],
  [botao('💰 Ouros', 'g:Ouros')],
]);

export function menuCartas(grupo, pagina) {
  const cartas = grupo === 'major' ? MAIORES : MENORES[grupo];
  const inicio = pagina * CARTAS_POR_PAGINA;
  const linhas = cartas.slice(inicio, inicio + CARTAS_POR_PAGINA).map((nome) => [botao(nome, `c:${nome}`)]);
  const navegacao = [];
  if (pagina > 0) navegacao.push(botao('⬅️', `nav:${grupo}:${pagina - 1}`));
  if (inicio + CARTAS_POR_PAGINA < cartas.length) navegacao.push(botao('➡️', `nav:${grupo}:${pagina + 1}`));
  if (navegacao.length) linhas.push(navegacao);
  linhas.push([botao('🔙 Voltar', 'back')]);
  return teclado(linhas);
}

export const menuPosicao = () => teclado([
  [botao('⬆️ Normal', 'p:n')],
  [botao('⬇️ Invertida', 'p:r')],
]);

export const menuContinuar = () => teclado([
  [botao('➕ Continuar', 'cont')],
  [botao('✅ Finalizar', 'fim')],
]);

export const menuTiragens = () => teclado([
  ...['dia', 'pf', 'sc', 'peladan', 'ferradura', 'cruz'].map((id) => [botao(TIRAGENS[id].label, `tir:${id}`)]),
  [botao('🪐 Mandala Astrológica', 'tir:mandala')],
  [botao('🔙 Voltar', 'tirback')],
]);

export const menuMandala = () => teclado([
  [botao(TIRAGENS.mandala12.label, 'tir:mandala12')],
  [botao(TIRAGENS.mandala13.label, 'tir:mandala13')],
  [botao('🔙 Voltar', 'tirback')],
]);

export const menuConfirmar = (id) => teclado([
  [botao('✅ Confirmar', `tirrun:${id}`)],
  [botao('🔙 Cancelar', `tircancel:${id}`)],
]);

export const semBotoes = () => teclado([]);

export function previaTiragem(id) {
  const info = TIRAGENS[id];
  return `🎲 <b>${escapar(info.label)}</b>\n\n${escapar(info.descricao)}\n\n🃏 Quantidade de cartas: <b>${info.count}</b>\n\nConfirma para iniciar a tiragem?`;
}

export function textoSelecionadas(cartas) {
  const linhas = cartas.map((c, i) => `${i + 1}. ${c.nome} (${posicao(c.rev)})`);
  return `${linhas.join('\n')}\n\nTotal: ${cartas.length}/${MAX_CARTAS}`;
}

export const TELA_TIRAGENS = '🎲 <b>Escolha a tiragem</b>\n\nSelecione um formato abaixo.';
export const TELA_MANDALA = '🪐 <b>Mandala Astrológica</b>\n\nEscolha se deseja 12 ou 13 cartas.';
