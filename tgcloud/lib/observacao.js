import { db } from 'sdk';
import { sql } from 'sdk/db';
import { vistos } from '../schema.js';
import { GRUPO_ID } from './grupo.js';
import { ehMira } from './ponte.js';
import { agora } from './sessao.js';

export async function registrarVisto(mensagem, via) {
  try {
    if (!GRUPO_ID || mensagem.chat.id !== GRUPO_ID) return;
    const bot = Boolean(mensagem.from && mensagem.from.is_bot);
    await db.insert(vistos).values({
      quando: agora(),
      via,
      ehBot: bot,
      remetenteId: bot ? mensagem.from.id : null,
      usuario: bot ? String(mensagem.from.username || '') : null,
      temTexto: Boolean(mensagem.text || mensagem.caption),
      eResposta: Boolean(mensagem.reply_to_message),
      casouComMira: ehMira(mensagem),
    }).run();
    await db.run(sql`DELETE FROM vistos WHERE id NOT IN (SELECT id FROM vistos ORDER BY id DESC LIMIT 50)`);
  } catch (e) {
    console.warn('registro de observação falhou:', (e && e.message) || String(e));
  }
}
