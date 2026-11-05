import { table, integer, text, json, index, uniqueIndex } from 'sdk/db';

export const sessoes = table('sessoes', {
  userId:     integer('user_id').primaryKey(),
  cartas:     json('cartas').notNull(),
  pendente:   text('pendente'),
  atualizado: integer('atualizado').notNull(),
});

export const leituras = table('leituras', {
  id:              integer('id').primaryKey({ autoIncrement: true }),
  chatId:          integer('chat_id').notNull(),
  tipo:            text('tipo').notNull(),
  tiragem:         text('tiragem'),
  cartas:          json('cartas').notNull(),
  mensagemNoGrupo: integer('mensagem_no_grupo').notNull(),
  estado:          text('estado').notNull(),
  criada:          integer('criada').notNull(),
}, (t) => ({
  grupoIdx: uniqueIndex('uidx_leituras_mensagem_no_grupo').on(t.mensagemNoGrupo),
  chatIdx:  index('idx_leituras_chat_estado').on(t.chatId, t.estado),
}));

export const partes = table('partes', {
  mensagemDaMira: integer('mensagem_da_mira').primaryKey(),
  leituraId:      integer('leitura_id').notNull(),
  texto:          text('texto').notNull(),
  entregues:      json('entregues').notNull(),
}, (t) => ({
  leituraIdx: index('idx_partes_leitura').on(t.leituraId),
}));

export const fotos = table('fotos', {
  chave:   text('chave').primaryKey(),
  arquivo: text('arquivo').notNull(),
});
