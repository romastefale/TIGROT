import { db } from 'sdk';
import { eq, lt } from 'sdk/db';
import { sessoes } from '../schema.js';
import { SESSAO_SEGUNDOS } from './config.js';

export const agora = () => Math.floor(Date.now() / 1000);

const vazia = (userId) => ({ userId, cartas: [], grupo: null, pagina: 0, pendente: null, atualizado: agora() });

export async function carregarSessao(userId) {
  const sessao = await db.select().from(sessoes).where(eq(sessoes.userId, userId)).get();
  if (!sessao || agora() - sessao.atualizado > SESSAO_SEGUNDOS) return vazia(userId);
  return sessao;
}

export async function salvarSessao(sessao) {
  const valores = { ...sessao, atualizado: agora() };
  await db.insert(sessoes).values(valores)
    .onConflictDoUpdate({
      target: sessoes.userId,
      set: {
        cartas: valores.cartas,
        grupo: valores.grupo,
        pagina: valores.pagina,
        pendente: valores.pendente,
        atualizado: valores.atualizado,
      },
    })
    .run();
  await db.delete(sessoes).where(lt(sessoes.atualizado, agora() - SESSAO_SEGUNDOS)).run();
}

export async function apagarSessao(userId) {
  await db.delete(sessoes).where(eq(sessoes.userId, userId)).run();
}
