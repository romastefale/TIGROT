import { ehMira, receberDaMira } from '../lib/ponte.js';

export default async function (message) {
  if (ehMira(message)) await receberDaMira(message);
}
