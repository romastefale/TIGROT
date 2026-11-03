import { table, integer, text, json, index } from 'sdk/db';

export const sessoes = table('sessoes', {
  userId:     integer('user_id').primaryKey(),
  cartas:     json('cartas').notNull(),
  pendente:   text('pendente'),
  atualizado: integer('atualizado').notNull(),
});

export const pedidos = table('pedidos', {
  id:         integer('id').primaryKey({ autoIncrement: true }),
  chatId:     integer('chat_id').notNull(),
  tipo:       text('tipo').notNull(),
  tiragem:    text('tiragem'),
  cartas:     json('cartas').notNull(),
  mensagemId: integer('mensagem_id').notNull(),
  estado:     text('estado').notNull(),
  resposta:   integer('resposta'),
  criada:     integer('criada').notNull(),
}, (t) => ({
  chatIdx: index('idx_pedidos_chat_estado').on(t.chatId, t.estado),
}));

export const fotos = table('fotos', {
  chave:   text('chave').primaryKey(),
  arquivo: text('arquivo').notNull(),
});
