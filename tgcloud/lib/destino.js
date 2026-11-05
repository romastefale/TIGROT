import { db } from 'sdk';
import { eq } from 'sdk/db';
import { ajustes } from '../schema.js';
import { GRUPO_ID } from './grupo.js';

const chaveDaMigracao = () => `migracao:${GRUPO_ID}`;

export async function grupoAtual() {
  if (!GRUPO_ID) return 0;
  const linha = await db.select().from(ajustes).where(eq(ajustes.chave, chaveDaMigracao())).get();
  const novo = linha ? Number(linha.valor) : 0;
  return Number.isSafeInteger(novo) && novo < 0 ? novo : GRUPO_ID;
}

export async function registrarMigracao(novoId) {
  if (!Number.isSafeInteger(novoId) || novoId >= 0) return;
  await db.insert(ajustes).values({ chave: chaveDaMigracao(), valor: String(novoId) })
    .onConflictDoUpdate({ target: ajustes.chave, set: { valor: String(novoId) } })
    .run();
}

export async function ehGrupo(chatId) {
  if (!GRUPO_ID) return false;
  if (chatId === GRUPO_ID) return true;
  return chatId === await grupoAtual();
}
