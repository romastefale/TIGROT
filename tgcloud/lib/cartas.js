export const MAIORES = [
  'O Louco', 'O Mago', 'A Sacerdotisa', 'A Imperatriz', 'O Imperador',
  'O Hierofante', 'Os Enamorados', 'O Carro', 'A Força', 'O Eremita',
  'A Roda da Fortuna', 'A Justiça', 'O Enforcado', 'A Morte',
  'A Temperança', 'O Diabo', 'A Torre', 'A Estrela', 'A Lua',
  'O Sol', 'O Julgamento', 'O Mundo',
];

export const NAIPES = ['Copas', 'Paus', 'Espadas', 'Ouros'];

const NUMEROS = [
  'Ás', 'Dois', 'Três', 'Quatro', 'Cinco', 'Seis', 'Sete', 'Oito',
  'Nove', 'Dez', 'Valete', 'Cavaleiro', 'Rainha', 'Rei',
];

export const MENORES = Object.fromEntries(
  NAIPES.map((naipe) => [naipe, NUMEROS.map((numero) => `${numero} de ${naipe}`)]),
);

export const TODAS = [...MAIORES, ...NAIPES.flatMap((naipe) => MENORES[naipe])];

const ARQUIVOS_MAIORES = [
  '00-louco', '01-mago', '02-alta-sacerdotisa', '03-imperatriz', '04-imperador',
  '05-hierofante', '06-enamorados', '07-carro', '08-forca', '09-eremita',
  '10-roda', '11-justica', '12-pendurado', '13-morte', '14-temperanca',
  '15-diabo', '16-torre', '17-estrela', '18-lua', '19-sol',
  '20-julgamento', '21-mundo',
];

const ARQUIVOS = new Map([
  ...MAIORES.map((nome, i) => [nome, ARQUIVOS_MAIORES[i]]),
  ...NAIPES.flatMap((naipe) => NUMEROS.map((numero, i) => [
    `${numero} de ${naipe}`,
    `${naipe}-${String(i + 1).padStart(2, '0')}`,
  ])),
]);

export const arquivoDaCarta = (nome) => ARQUIVOS.get(nome) || null;

export const posicao = (invertida) => (invertida ? 'invertida' : 'normal');

export const TIRAGENS = {
  dia: {
    label: '🌞 Carta do Dia / Sim ou Não',
    count: 1,
    nome: 'Carta do Dia / Sim ou Não',
    descricao: 'Resposta direta e rápida.',
  },
  pf: {
    label: '🕰️ Passado, Presente e Futuro',
    count: 3,
    nome: 'Passado, Presente e Futuro',
    descricao: 'Foca na evolução de uma situação.',
  },
  sc: {
    label: '🧭 Situação, Desafio e Conselho',
    count: 3,
    nome: 'Situação, Desafio e Conselho',
    descricao: 'Auxilia na tomada de decisão.',
  },
  peladan: {
    label: '✳️ Tiragem Péladan',
    count: 5,
    nome: 'Tiragem Péladan',
    descricao: 'Analisa influências, obstáculos e desfecho.',
  },
  ferradura: {
    label: '🐎 Ferradura',
    count: 7,
    nome: 'Ferradura',
    descricao: 'Análise detalhada sobre amor, trabalho ou questões gerais.',
  },
  cruz: {
    label: '✝️ Cruz Celta',
    count: 10,
    nome: 'Cruz Celta',
    descricao: 'Uma das tiragens mais completas para previsões profundas.',
  },
  mandala12: {
    label: '🪐 Mandala Astrológica (12 cartas)',
    count: 12,
    nome: 'Mandala Astrológica',
    descricao: 'Analisa 12 áreas da vida baseada nas casas astrológicas.',
  },
  mandala13: {
    label: '🪐 Mandala Astrológica (13 cartas)',
    count: 13,
    nome: 'Mandala Astrológica',
    descricao: 'Analisa 13 posições da Mandala Astrológica.',
  },
};

export function sortear(quantidade) {
  const baralho = [...TODAS];
  for (let i = baralho.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [baralho[i], baralho[j]] = [baralho[j], baralho[i]];
  }
  return baralho.slice(0, quantidade).map((nome) => ({ nome, rev: Math.random() < 0.5 }));
}

export const semAcento = (s) => String(s).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
