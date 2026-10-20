import { chromium } from '@playwright/test';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const args = process.argv.slice(2);
if (args.length && (args.length !== 2 || args[0] !== '--report')) {
  throw new Error('Usage: node tools/bake_paper_textures.mjs [--report .scratch/paper-textures.json]');
}
const folder = fileURLToPath(new URL('../apps/web/public/textures/paper/', import.meta.url));
const hash = value => createHash('sha256').update(value).digest('hex');
const browser = await chromium.launch({ headless: true });
const report = { browser: browser.version(), tiles: [] };
try {
  const page = await browser.newPage({ deviceScaleFactor: 1 });
  for (const name of ['warm-grain', 'white-grain', 'cloud']) {
    const svg = await readFile(resolve(folder, `${name}.svg`), 'utf8');
    const tile = await page.evaluate(async svg => {
      const decode = async src => { const image = new Image(); image.src = src; await image.decode(); return image; };
      const image = await decode(`data:image/svg+xml,${encodeURIComponent(svg)}`);
      const canvas = document.createElement('canvas');
      canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
      const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
      const before = context.getImageData(0, 0, canvas.width, canvas.height).data;
      const png = canvas.toDataURL('image/png');
      const copy = await decode(png); context.clearRect(0, 0, canvas.width, canvas.height); context.drawImage(copy, 0, 0);
      const after = context.getImageData(0, 0, canvas.width, canvas.height).data;
      let differingChannels = 0, maxChannelError = 0;
      for (let i = 0; i < before.length; i++) if (before[i] !== after[i]) {
        differingChannels++; maxChannelError = Math.max(maxChannelError, Math.abs(before[i] - after[i]));
      }
      return { png, width: canvas.width, height: canvas.height, differingChannels, maxChannelError };
    }, svg);
    const { png, ...pixels } = tile;
    const bytes = Buffer.from(png.split(',')[1], 'base64');
    await writeFile(resolve(folder, `${name}.png`), bytes);
    // Transparent color roundtrips can round channels; report the measured result.
    report.tiles.push({ name, ...pixels, svgSha256: hash(svg), pngSha256: hash(bytes), pngBytes: bytes.length });
  }
} finally {
  await browser.close();
}
if (args.length) {
  const output = resolve(args[1]); await mkdir(dirname(output), { recursive: true });
  await writeFile(output, JSON.stringify(report, null, 2));
}
console.log(JSON.stringify(report, null, 2));
