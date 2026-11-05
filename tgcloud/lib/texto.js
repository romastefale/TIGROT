import { api, BotApiError } from 'sdk';
import { LIMITE_TEXTO } from './config.js';

export const escapar = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

function markdownParaHtml(texto) {
  return texto
    .replace(/\*\*\*(.+?)\*\*\*/g, '<b><i>$1</i></b>')
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/__(.+?)__/g, '<u>$1</u>')
    .replace(/(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)/g, '<i>$1</i>');
}

function prepararHtml(texto) {
  const normal = texto.replace(/<(\/?)tg-b>/g, '<$1b>').replace(/<(\/?)tg-i>/g, '<$1i>');
  return escapar(markdownParaHtml(normal)).replace(/&lt;(\/?)(b|i|u)&gt;/g, '<$1$2>');
}

export function dividir(texto, limite = LIMITE_TEXTO) {
  let resto = (texto || '').trim();
  const partes = [];
  while (resto.length > limite) {
    let corte = resto.lastIndexOf('\n', limite - 1);
    if (corte < 100) {
      const ponto = resto.lastIndexOf('. ', limite - 2);
      corte = ponto < 0 ? -1 : ponto + 1;
    }
    if (corte < 100) corte = limite;
    partes.push(resto.slice(0, corte).trim());
    resto = resto.slice(corte).trim();
  }
  if (resto) partes.push(resto);
  return partes;
}

async function comFallback(metodo, params, texto) {
  const base = { ...params, link_preview_options: { is_disabled: true } };
  try {
    return await api[metodo]({ ...base, text: prepararHtml(texto), parse_mode: 'HTML' });
  } catch (e) {
    if (!(e instanceof BotApiError) || e.code !== 400) throw e;
    console.warn('HTML recusado, repetindo como texto simples:', e.description);
    return api[metodo]({ ...base, text: texto });
  }
}

export const enviarTexto = (chat_id, texto) => comFallback('sendMessage', { chat_id }, texto);

export const editarTexto = (chat_id, message_id, texto) => comFallback('editMessageText', { chat_id, message_id }, texto);
