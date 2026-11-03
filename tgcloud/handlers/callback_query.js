import { tratarCallback } from '../lib/callbacks.js';

export default async function (consulta) {
  await tratarCallback(consulta);
}
