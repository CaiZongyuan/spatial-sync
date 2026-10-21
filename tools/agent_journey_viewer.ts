/** I5 browser observer started by tools/agent_journey.py: the live viewer must agree with the public API. */
import { chromium, type Browser } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { parseArgs } from 'node:util';
import type { SceneManifest, Snapshot } from '../packages/protocol/types';
import { observeTube } from './agent_journey_checks';

type Debug = {
  getRenderedPose: (key: string) => { p: number[] } | undefined;
  getEntityState: (key: string) => { values: Record<string, unknown> } | undefined;
};
type State = { entities: Record<string, { values: Record<string, unknown> }> };

const { values } = parseArgs({
  options: {
    web: { type: 'string' },
    api: { type: 'string' },
    session: { type: 'string' },
    output: { type: 'string' },
  },
});
const output = values.output!;
const route = `${values.api}/api/v1/sessions/${values.session}`;
const tubes = [1, 2, 3, 4].map((index) => `/World/Lab/Tubes/Tube${String(index).padStart(2, '0')}`);
const report = {
  sessionId: values.session,
  locationComparisons: 0,
  poseComparisons: 0,
  poseComparisonsByTube: Object.fromEntries(tubes.map((key) => [key, 0])),
  maxPoseDelta: 0,
  failures: [] as unknown[],
  checkpoints: [] as { file: string; reason: string; at: string }[],
};
let stopping = false;
process.on('SIGTERM', () => {
  stopping = true;
});
mkdirSync(output, { recursive: true });

async function get<T>(suffix: string): Promise<T> {
  const response = await fetch(route + suffix);
  if (!response.ok) throw new Error(`${suffix} returned ${response.status}`);
  return (await response.json()) as T;
}

async function main() {
  let browser: Browser | undefined;
  try {
    // The runner's SIGTERM asks for a final screenshot, so Playwright must not close the browser on it.
    browser = await chromium.launch({
      args: ['--enable-webgl', '--use-gl=angle', '--use-angle=swiftshader'],
      handleSIGTERM: false,
    });
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const checkpoint = async (reason: string) => {
      const file = `${String(report.checkpoints.length).padStart(2, '0')}-${reason.replace(/[^a-zA-Z0-9.-]+/g, '_')}.png`;
      await page.screenshot({ path: path.join(output, file) });
      report.checkpoints.push({ file, reason, at: new Date().toISOString() });
    };
    await page.goto(`${values.web}/sessions/${values.session}`);
    await page.getByTestId('connection-status').filter({ hasText: '已连接' }).waitFor({ timeout: 60_000 });
    const manifest = await get<SceneManifest>('/scene');
    const streams = new Map(manifest.nodes.flatMap((node) => (node.kind === 'body' ? [[node.entityKey, node.streamId]] : [])));
    await checkpoint('connected');
    const strikes = new Map<string, number>();
    const seen = new Map<string, unknown>();
    const shots = new Map<string, number>();
    let previous: Snapshot | undefined;
    while (!stopping) {
      const [snapshot, state] = await Promise.all([get<Snapshot>('/snapshot'), get<State>('/state')]);
      const browserView = await page.evaluate((keys) => {
        const debug = (window as unknown as { __spatialDebug?: Debug }).__spatialDebug;
        return keys.map((key) => ({
          location: debug?.getEntityState(key)?.values.location,
          rendered: debug?.getRenderedPose(key)?.p,
        }));
      }, tubes);
      for (const [index, key] of tubes.entries()) {
        const location = state.entities[key].values.location;
        const { location: shown, rendered } = browserView[index];
        const pose = snapshot.poses.find((row) => row[0] === streams.get(key))?.slice(1, 4);
        const before = previous?.poses.find((row) => row[0] === streams.get(key))?.slice(1, 4);
        observeTube(report, strikes, { key, simTime: snapshot.simTime, location, shown, pose, before, rendered });
        if (location !== seen.get(key)) {
          const initial = !seen.has(key);
          seen.set(key, location);
          if (!initial && typeof location === 'string' && location.startsWith('/World/'))
            shots.set(key, Date.now() + 800);
        }
        const due = shots.get(key);
        if (due && Date.now() >= due) {
          shots.delete(key);
          await checkpoint(`${key.split('/').at(-1)}-${String(location).split('/').slice(-2).join('-')}`);
        }
      }
      previous = snapshot;
      await page.waitForTimeout(400);
    }
    await checkpoint('final');
  } catch (error) {
    report.failures.push({ error: String(error) });
  } finally {
    writeFileSync(path.join(output, 'viewer.json'), JSON.stringify(report, null, 2));
    await browser?.close();
  }
}

void main();
