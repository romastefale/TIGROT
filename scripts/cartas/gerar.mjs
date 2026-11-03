import { readdirSync, mkdirSync } from 'node:fs';
import { join, parse } from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const origem = fileURLToPath(new URL('../../assets/rws/', import.meta.url));
const destino = fileURLToPath(new URL('../../web/cartas/', import.meta.url));
const LARGURA = 512;
const QUALIDADE = 80;

const semAcento = (s) => s.normalize('NFD').replace(/[̀-ͯ]/g, '');
const stemDe = (arquivo) => semAcento(parse(arquivo).name.replace(/^TarotRWS-/, '')).toLowerCase().replace(/^([a-z]+)-(\d+)$/, (_, naipe, n) => `${naipe[0].toUpperCase()}${naipe.slice(1)}-${n}`);

mkdirSync(destino, { recursive: true });
const arquivos = readdirSync(origem).filter((f) => /\.jpe?g$/i.test(f)).sort();
const stems = [];
for (const arquivo of arquivos) {
  const stem = stemDe(arquivo);
  const base = sharp(join(origem, arquivo)).rotate().resize({ width: LARGURA });
  await base.clone().jpeg({ quality: QUALIDADE, mozjpeg: true }).toFile(join(destino, `${stem}.jpg`));
  await base.clone().rotate(180).jpeg({ quality: QUALIDADE, mozjpeg: true }).toFile(join(destino, `${stem}-i.jpg`));
  stems.push(stem);
}
console.log(`${stems.length} cartas, ${stems.length * 2} arquivos em ${destino}`);
