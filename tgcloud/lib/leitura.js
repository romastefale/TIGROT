import { api } from 'sdk';
import { enviarCartas } from './fotos.js';
import { avisoDaFalha, detalheDaFalha, perguntar } from './ponte.js';

export async function iniciarLeitura(chatId, cartas, tiragem) {
  await enviarCartas(chatId, cartas);
  try {
    await perguntar(chatId, 'tecnica', cartas, tiragem);
  } catch (e) {
    console.error('pedido da leitura técnica falhou:', detalheDaFalha(e));
    await api.sendMessage({ chat_id: chatId, text: avisoDaFalha('tecnica', e) });
    return;
  }
  await api.sendMessage({ chat_id: chatId, text: '🔮 Preparando a interpretação das cartas...', parse_mode: 'HTML' });
}
