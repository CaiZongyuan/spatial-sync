/** Run against the isolated demo_app + Vite; never changes the production application. */
import { chromium } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const web = process.env.SPATIAL_WEB_URL ?? 'http://127.0.0.1:5185';
const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8005';
const output = process.argv[2] ?? '.scratch/p2-a/browser-codecs.json';
async function main() {
  const browser = await chromium.launch({
    args: ['--use-gl=angle', '--use-angle=swiftshader'],
  });
  try {
    const page = await browser.newPage();
    await page.goto(web);
    const records = [];
    const moduleUrl = `/@fs/${resolve('tools/browser_pose_profile.ts').replaceAll('\\', '/')}`;
    for (const count of [35, 100, 500, 2000, 10000]) {
      const response = await fetch(`${api}/profile/corpus/${count}`);
      if (!response.ok)
        throw new Error(`Corpus fetch failed: ${response.status}`);
      const corpus = await response.json();
      records.push(
        ...(await page.evaluate(
          async ({ moduleUrl, corpus }) => {
            const { profileCorpus } = await import(moduleUrl);
            return profileCorpus(corpus);
          },
          { moduleUrl, corpus },
        )),
      );
    }
    const receipt = {
      browser: await browser.version(),
      userAgent: await page.evaluate(() => navigator.userAgent),
      boundary:
        'JSON.parse or binary DataView/views through identical PoseBuffer ingestion; setup, download, full pose oracle excluded',
      rendering:
        'not measured; ordinary transform frames after one untimed snapshot',
      records,
    };
    await writeFile(output, `${JSON.stringify(receipt, null, 2)}\n`);
    console.log(JSON.stringify(receipt, null, 2));
  } finally {
    await browser.close();
  }
}
main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
