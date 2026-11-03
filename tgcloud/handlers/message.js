import { ehMira, receberDaMira } from '../lib/ponte.js';
import { executarComando } from '../lib/comandos.js';

export default async function (message) {
  if (ehMira(message)) {
    await receberDaMira(message);
    return;
  }
  await executarComando(message);
}
