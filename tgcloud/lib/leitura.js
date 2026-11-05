import { api } from 'sdk';
import { enviarCartas } from './fotos.js';
import { perguntar } from './ponte.js';

export async function iniciarLeitura(chatId, cartas, tiragem) {
  await enviarCartas(chatId, cartas);
  await api.sendMessage({ chat_id: chatId, text: '🔮 Preparando a interpretação das cartas...', parse_mode: 'HTML' });
  try {
    await perguntar(chatId, 'tecnica', cartas, tiragem);
  } catch (e) {
    console.error('pedido da leitura técnica falhou:', (e && e.description) || (e && e.message) || String(e));
    await api.sendMessage({ chat_id: chatId, text: '⚠️ Não consegui enviar o pedido de leitura à Mira.' });
  }
}
