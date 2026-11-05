import { ehMira, receberDaMira } from '../lib/ponte.js';
import { registrarVisto } from '../lib/observacao.js';

export default async function (message) {
  await registrarVisto(message, 'edited');
  if (ehMira(message)) await receberDaMira(message);
}
