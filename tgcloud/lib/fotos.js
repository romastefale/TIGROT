import { api, db, BotApiError } from 'sdk';
import { inArray } from 'sdk/db';
import { fotos } from '../schema.js';
import { URL_CARTAS } from './config.js';
import { arquivoDaCarta, posicao } from './cartas.js';
import { escapar } from './texto.js';

const POR_ALBUM = 10;

const chaveDe = (carta) => `${arquivoDaCarta(carta.nome)}${carta.rev ? '-i' : ''}`;
const legendaDe = (carta) => `🃏 ${escapar(carta.nome)} (${posicao(carta.rev)})`;

function fotoDe(resposta) {
  const tamanhos = resposta && resposta.photo;
  return tamanhos && tamanhos.length ? tamanhos[tamanhos.length - 1].file_id : null;
}

async function arquivosSalvos(chaves) {
  const linhas = await db.select().from(fotos).where(inArray(fotos.chave, chaves)).all();
  return new Map(linhas.map((linha) => [linha.chave, linha.arquivo]));
}

async function guardar(chaves, respostas, salvos) {
  const novos = new Map();
  chaves.forEach((chave, i) => {
    const arquivo = fotoDe(respostas[i]);
    if (arquivo && !salvos.has(chave)) novos.set(chave, arquivo);
  });
  for (const [chave, arquivo] of novos) {
    await db.insert(fotos).values({ chave, arquivo })
      .onConflictDoUpdate({ target: fotos.chave, set: { arquivo } }).run();
  }
}

async function enviarAlbum(chat_id, lote, chaves, salvos) {
  const midia = lote.map((carta, i) => ({
    type: 'photo',
    media: salvos.get(chaves[i]) || `${URL_CARTAS}${chaves[i]}.jpg`,
    caption: legendaDe(carta),
    parse_mode: 'HTML',
  }));
  if (midia.length === 1) {
    const { media, caption, parse_mode } = midia[0];
    return [await api.sendPhoto({ chat_id, photo: media, caption, parse_mode })];
  }
  return api.sendMediaGroup({ chat_id, media: midia });
}

async function enviarLote(chat_id, lote) {
  const chaves = lote.map(chaveDe);
  const salvos = await arquivosSalvos(chaves);
  let respostas;
  let usados = salvos;
  try {
    respostas = await enviarAlbum(chat_id, lote, chaves, usados);
  } catch (e) {
    if (!(e instanceof BotApiError)) throw e;
    console.warn('álbum recusado, tentando só com os endereços:', e.code, e.description);
    usados = new Map();
    try {
      respostas = await enviarAlbum(chat_id, lote, chaves, usados);
    } catch (e2) {
      if (!(e2 instanceof BotApiError)) throw e2;
      console.warn('fotos recusadas, enviando só o nome das cartas:', e2.code, e2.description);
      for (const carta of lote) await api.sendMessage({ chat_id, text: legendaDe(carta), parse_mode: 'HTML' });
      return;
    }
  }
  try {
    await guardar(chaves, respostas, usados);
  } catch (e) {
    console.warn('file_id das cartas não foi guardado:', (e && e.message) || String(e));
  }
}

export async function enviarCartas(chat_id, cartas) {
  if (!URL_CARTAS) {
    for (const carta of cartas) await api.sendMessage({ chat_id, text: legendaDe(carta), parse_mode: 'HTML' });
    return;
  }
  for (let i = 0; i < cartas.length; i += POR_ALBUM) {
    await enviarLote(chat_id, cartas.slice(i, i + POR_ALBUM));
  }
}
