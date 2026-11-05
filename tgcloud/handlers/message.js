import { ehMira, receberDaMira } from '../lib/ponte.js';
import { registrarVisto } from '../lib/observacao.js';
import { executarComando } from '../lib/comandos.js';

export default async function (message) {
  await registrarVisto(message, 'message');
  if (ehMira(message)) {
    await receberDaMira(message);
    return;
  }
  await executarComando(message);
}
