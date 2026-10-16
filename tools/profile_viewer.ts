/** Real browser/real source baseline. Run with pnpm exec tsx tools/profile_viewer.ts --help. */
import { chromium, expect as baseExpect } from '@playwright/test';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { parseArgs } from 'node:util';
import { performance } from 'node:perf_hooks';
import { distribution, summarizeFrames } from './viewer_profile_stats';
import type { SceneManifest, Snapshot } from '../packages/protocol/types';
const expect = baseExpect.configure({ timeout: 60_000 });

const { values } = parseArgs({
  options: {
    help: { type: 'boolean' },
    web: { type: 'string', default: 'http://127.0.0.1:5184' },
    api: { type: 'string', default: 'http://127.0.0.1:8002' },
    scene: { type: 'string', default: 'lab-demo' },
    source: { type: 'string', default: 'synthetic' },
    duration: { type: 'string', default: '10' },
    warmup: { type: 'string', default: '5' },
    repeats: { type: 'string', default: '3' },
    renderer: { type: 'string', default: 'default' },
    output: { type: 'string', default: '.scratch/viewer-profile.json' },
    'scene-file': { type: 'string', multiple: true },
    'allow-development': { type: 'boolean' },
    format: { type: 'string', default: 'json' },
    'render-mode': { type: 'string', default: 'individual' },
    'fit-scene': { type: 'boolean' },
    'camera-file': { type: 'string' },
  },
});
if (values.help) {
  process.stdout.write(
    'Usage: pnpm exec tsx tools/profile_viewer.ts --web URL --api ISOLATED_API --scene SCENE --source synthetic|newton --duration 10 --warmup 5 --repeats 3 --renderer default|swiftshader --output report.json [--scene-file PATH ...]\nRequires a production viewer with matching API proxy and no active Session. Owns and closes only Sessions it creates.\n',
  );
  process.exit(0);
}
const durationMs = Number(values.duration) * 1000,
  warmupMs = Number(values.warmup) * 1000,
  repeats = Number(values.repeats);
if (
  !(
    durationMs >= 3000 &&
    durationMs <= 300_000 &&
    warmupMs >= 0 &&
    Number.isInteger(repeats) &&
    repeats >= 1 &&
    repeats <= 10
  )
)
  throw new Error(
    'Require duration 3–300 seconds, nonnegative warmup, and 1–10 repeats',
  );
if (!['default', 'swiftshader'].includes(values.renderer!))
  throw new Error('Renderer must be default or swiftshader');
const hash = (value: string | Buffer) =>
  createHash('sha256').update(value).digest('hex');
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const git = (...args: string[]) =>
  execFileSync('git', args, { encoding: 'utf8' }).trim();
const sourceFiles = git(
  '-c',
  'core.quotepath=false',
  'ls-files',
  '-z',
  '--cached',
  '--others',
  '--exclude-standard',
  'apps/web',
  'apps/api',
  'packages',
  'assets',
  'examples',
  'tools/profile_viewer.ts',
  'tools/viewer_profile_stats.ts',
)
  .split('\0')
  .filter(Boolean)
  .sort();
const sceneFiles = values['scene-file'] ?? [
  values.source === 'synthetic'
    ? 'apps/api/app/sources/synthetic_source.py'
    : 'packages/sim_runtime/scenes.py',
];
const sourceIdentity = {
  revision: git('rev-parse', 'HEAD'),
  uncommitted: git('status', '--porcelain'),
  treeSha256: hash(
    sourceFiles
      .map((file) => `${file}\0${hash(readFileSync(file))}`)
      .join('\n'),
  ),
  lockSha256: hash(readFileSync('pnpm-lock.yaml')),
  sceneFileHashes: Object.fromEntries(
    sceneFiles.map((file) => [file, hash(readFileSync(file))]),
  ),
};

async function api<T = any>(
  suffix: string,
  method = 'GET',
  body?: unknown,
): Promise<T> {
  const response = await fetch(`${values.api}/api/v1${suffix}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok)
    throw new Error(
      `API ${method} ${suffix}: ${response.status} ${await response.text()}`,
    );
  return response.status === 204 ? (undefined as T) : response.json();
}

async function main() {
  const health = await api('/health');
  if (
    health.sessions.some(
      (session: { state: string }) =>
        !['STOPPED', 'FAILED'].includes(session.state),
    )
  )
    throw new Error(
      'Refusing to profile an API that already has an active Session; use an isolated service',
    );
  let gpu: string;
  try {
    gpu = execFileSync(
      'nvidia-smi',
      [
        '--query-gpu=name,driver_version,memory.total',
        '--format=csv,noheader,nounits',
      ],
      { encoding: 'utf8' },
    ).trim();
  } catch {
    gpu = 'Unavailable';
  }
  let pythonVersions: unknown;
  try {
    pythonVersions = JSON.parse(
      execFileSync(
        path.join(
          '.venv',
          process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
        ),
        [
          '-c',
          'import importlib.metadata as m,json,sys; print(json.dumps({"python":sys.version,"packages":{n:m.version(n) for n in ["fastapi","pydantic","numpy","newton","warp-lang"]}}))',
        ],
        { encoding: 'utf8' },
      ),
    );
  } catch {
    pythonVersions = 'Unavailable';
  }
  const webPackages = Object.fromEntries(
    ['react', 'three', 'vite', '@react-three/fiber'].map((name) => [
      name,
      JSON.parse(
        readFileSync(`apps/web/node_modules/${name}/package.json`, 'utf8'),
      ).version,
    ]),
  );
  const browser = await chromium.launch({
    headless: true,
    args: [
      '--enable-webgl',
      ...(values.renderer === 'swiftshader'
        ? ['--use-gl=angle', '--use-angle=swiftshader']
        : []),
    ],
  });
  const runs: any[] = [];
  try {
    for (let repeat = 0; repeat < repeats; repeat++) {
      const context = await browser.newContext({
        viewport: { width: 1440, height: 960 },
      });
      const page = await context.newPage();
      const pageErrors: string[] = [];
      page.on('pageerror', (error) => pageErrors.push(error.message));
      let sid: string | undefined;
      try {
        const startCreation = performance.now();
        const session = await api('/sessions', 'POST', {
          sceneId: values.scene,
          source: values.source,
          realtimeFactor: 1,
          continueWithoutObservers: true,
        });
        sid = session.sessionId;
        const startupMs = performance.now() - startCreation;
        const manifest = await api<SceneManifest>(`/sessions/${sid}/scene`);
        const response = await page.goto(
          `${values.web}/sessions/${encodeURIComponent(sid!)}?format=${values.format}&render=${values['render-mode']}`,
        );
        const buildMode = (await response!.text()).includes('/@vite/client')
          ? 'development'
          : 'production';
        if (buildMode === 'development' && !values['allow-development'])
          throw new Error(
            'Production measurements require vite preview or a production static server; use --allow-development only for harness diagnosis',
          );
        await expect(page.getByTestId('connection-status')).toHaveText(
          'connected',
        );
        // P2-B defaults to sparse deltas. Compare renderers using the same full
        // frames as the original baseline, and wait for the accepted resubscribe.
        await page.getByRole('checkbox', { name: 'Sparse deltas' }).uncheck();
        await expect
          .poll(() =>
            page.evaluate(() => {
              const subscription =
                window.__spatialDebug!.getSubscriptionMetrics();
              return !subscription.delta && !subscription.resyncPending;
            }),
          )
          .toBe(true);
        const bodies = manifest.nodes.filter((node) => node.kind === 'body');
        if (!bodies.length)
          throw new Error(
            'A transform profile requires at least one streamed Body',
          );
        await expect
          .poll(() =>
            page.evaluate(
              (keys) =>
                keys.every(
                  (key) =>
                    window.__spatialDebug?.getRenderedPose(key) &&
                    window.__spatialDebug.inspectVisuals(key),
                ),
              bodies.map((node) => node.entityKey),
            ),
          )
          .toBe(true);
        await page.getByRole('button', { name: 'Pause simulation' }).click();
        await expect(page.getByTestId('session-state')).toHaveText('PAUSED');
        const snapshot = await api<Snapshot>(`/sessions/${sid}/snapshot`);
        const wireRows = new Map(snapshot.poses.map((row) => [row[0], row]));
        const references = bodies.map((node) => ({
          key: node.entityKey,
          pose: wireRows.get(node.streamId),
        }));
        if (references.some((item) => !item.pose))
          throw new Error('Snapshot omits declared Bodies');
        await page.getByTestId(`entity-${bodies[0].entityKey}`).click();
        await expect(page.getByTestId('inspector-seq')).toHaveText(
          String(snapshot.seq),
        );
        const checkPoses = () =>
          page.evaluate((references) => {
            let positionMaxErrorM = 0,
              quaternionMaxError = 0,
              missing = 0;
            for (const item of references) {
              const actual = window.__spatialDebug!.getRenderedPose(item.key);
              if (!actual || !item.pose) {
                missing++;
                continue;
              }
              for (let i = 0; i < 3; i++)
                positionMaxErrorM = Math.max(
                  positionMaxErrorM,
                  Math.abs(actual.p[i] - item.pose[i + 1]),
                );
              let same = 0,
                opposite = 0;
              for (let i = 0; i < 4; i++) {
                same = Math.max(same, Math.abs(actual.q[i] - item.pose[i + 4]));
                opposite = Math.max(
                  opposite,
                  Math.abs(actual.q[i] + item.pose[i + 4]),
                );
              }
              quaternionMaxError = Math.max(
                quaternionMaxError,
                Math.min(same, opposite),
              );
            }
            return {
              checkedBodyCount: references.length,
              missing,
              positionMaxErrorM,
              quaternionMaxError,
            };
          }, references);
        await expect
          .poll(async () => {
            const checked = await checkPoses();
            return checked.missing
              ? 1
              : Math.max(checked.positionMaxErrorM, checked.quaternionMaxError);
          })
          .toBeLessThan(1e-4);
        const correctness = await checkPoses();
        if (correctness.missing || snapshot.poses.length !== bodies.length)
          throw new Error(
            'The rendered workload failed its authoritative-pose/count precondition',
          );
        if (values['camera-file']) {
          const saved = JSON.parse(readFileSync(values['camera-file'], 'utf8'));
          await page.evaluate(
            (camera) => window.__spatialRenderDebug!.setCamera(camera),
            saved.runs[0].camera,
          );
        } else if (values['fit-scene'])
          await page.evaluate(() => window.__spatialRenderDebug!.fitScene());
        await page.getByRole('button', { name: 'Resume simulation' }).click();
        await expect(page.getByTestId('session-state')).toHaveText('RUNNING');
        await sleep(warmupMs);
        const browserEnvironment = await page.evaluate(() => {
          const canvas = document.querySelector('canvas')!;
          const gl = canvas.getContext('webgl2')!;
          const debug = gl.getExtension('WEBGL_debug_renderer_info');
          return {
            userAgent: navigator.userAgent,
            devicePixelRatio,
            canvasCss: {
              width: canvas.clientWidth,
              height: canvas.clientHeight,
            },
            drawingBuffer: {
              width: gl.drawingBufferWidth,
              height: gl.drawingBufferHeight,
            },
            renderer: debug
              ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL)
              : gl.getParameter(gl.RENDERER),
            vendor: debug
              ? gl.getParameter(debug.UNMASKED_VENDOR_WEBGL)
              : gl.getParameter(gl.VENDOR),
          };
        });
        const streamStart = await page.evaluate(() =>
          window.__spatialDebug!.startProfile(),
        );
        const camera = await page.evaluate(() =>
          window.__spatialRenderDebug?.camera(),
        );
        const rawSource: { timeMs: number; stats: any }[] = [];
        const deadline = performance.now() + durationMs;
        while (performance.now() < deadline) {
          rawSource.push({
            timeMs: performance.now(),
            stats: await api(`/sessions/${sid}/stats`),
          });
          await sleep(
            Math.min(1000, Math.max(0, deadline - performance.now())),
          );
        }
        const stopped = await page.evaluate(() => ({
          recording: window.__spatialDebug!.stopProfile(),
          stream: window.__spatialDebug!.getStreamMetrics(),
          geometry: window.__spatialRenderDebug?.frameStats(),
        }));
        const frames = summarizeFrames(
          stopped.recording.samples,
          stopped.recording.overflow,
        );
        const physicalCount = manifest.nodes.filter(
          (node) => node.kind !== 'group',
        ).length;
        if (
          !stopped.geometry ||
          stopped.geometry.registeredBodies !== bodies.length ||
          stopped.geometry.appliedBodies !== bodies.length ||
          stopped.geometry.registeredPhysicalNodes !== physicalCount ||
          stopped.geometry.potentialVisualInstances < physicalCount
        )
          throw new Error(
            'Registered/applied/renderable workload counts differ from the Manifest',
          );
        if (
          (values['fit-scene'] || values['camera-file']) &&
          stopped.geometry.submittedTriangles <
            stopped.geometry.potentialTriangles * 0.99
        )
          throw new Error(
            'Fitted workload was unexpectedly culled; do not count empty drawing as performance',
          );
        if (
          stopped.stream.sessionState !== 'RUNNING' ||
          stopped.stream.connection !== 'connected' ||
          stopped.stream.skippedNonFinite !== streamStart.skippedNonFinite ||
          pageErrors.length
        )
          throw new Error(
            'Source/connection/nonfinite/JavaScript error invalidated the useful-work measurement',
          );
        if (
          stopped.stream.receivedFrames <= streamStart.receivedFrames ||
          frames.measuredDurationMs < durationMs * 0.9
        )
          throw new Error('The intended receive/render window did not execute');
        const sourcePolledLatest = Object.fromEntries(
          [
            'solver_ms',
            'readback_ms',
            'publish_ms',
            'stepHz',
            'actualRtf',
          ].flatMap((name) => {
            const samples = rawSource
              .map((item) => item.stats[name])
              .filter(
                (value) => typeof value === 'number' && Number.isFinite(value),
              );
            return samples.length ? [[name, distribution(samples)]] : [];
          }),
        );
        const manifestIdentity = JSON.stringify(manifest)
          .replaceAll(sid!, '<session>')
          .replaceAll(manifest.sceneRevision, '<revision>');
        runs.push({
          repeat: repeat + 1,
          sessionId: sid,
          sceneRevision: manifest.sceneRevision,
          manifestSha256: hash(JSON.stringify(manifest)),
          stableManifestSha256: hash(manifestIdentity),
          source: manifest.source,
          bodyCount: bodies.length,
          physicalNodeCount: manifest.nodes.filter(
            (node) => node.kind !== 'group',
          ).length,
          initialPoseCount: snapshot.poses.length,
          correctness: {
            ...correctness,
            sequence: snapshot.seq,
            simTime: snapshot.simTime,
            referenceRowsSha256: hash(JSON.stringify(snapshot.poses)),
          },
          camera,
          geometry: stopped.geometry,
          startupMs,
          warmupMs,
          buildMode,
          browserEnvironment,
          streamStart,
          streamEnd: stopped.stream,
          receivedFrameRateHz:
            ((stopped.stream.receivedFrames - streamStart.receivedFrames) *
              1000) /
            (stopped.stream.timeMs - streamStart.timeMs),
          render: frames,
          sourcePolledLatest,
          rawSource,
          rawFrames: stopped.recording.samples,
        });
        process.stdout.write(
          `Run ${repeat + 1}/${repeats}: ${frames.sampleCount} actual R3F samples, FPS p50 ${frames.renderFps.p50.toFixed(1)}, apply p95 ${frames.transformApplyMs.p95.toFixed(3)} ms\n`,
        );
      } finally {
        if (sid) await api(`/sessions/${sid}`, 'DELETE');
        await context.close();
      }
    }
  } finally {
    await browser.close();
  }
  const report = {
    schemaVersion: 1,
    status: 'measured',
    claim: 'Baseline evidence only; no optimization or scaling claim',
    recordedAt: new Date().toISOString(),
    sourceIdentity,
    configuration: values,
    hardware: {
      platform: os.platform(),
      release: os.release(),
      architecture: os.arch(),
      cpu: os.cpus()[0]?.model,
      logicalProcessors: os.cpus().length,
      memoryBytes: os.totalmem(),
      gpu,
    },
    versions: {
      node: process.version,
      browser: browser.version(),
      webPackages,
      pythonVersions,
    },
    methodology: {
      fps: 'Nearest-rank distribution of completed one-second RAF windows; incomplete final window excluded',
      apply:
        'performance.now around interpolation, pose writes and render receipts inside useFrame; GPU draw execution excluded',
      frameAge:
        'Local time since accepted network receipt; playout delay separate',
      source:
        'Distribution of polled latest stats at approximately 1 Hz; repeated latest values may occur, not a per-step/per-publish timing distribution',
      timing:
        'Startup, correctness pause/resume and warmup excluded from raw frame window',
      comparison:
        'Use identical source tree, scene assets, source configuration, viewport, camera, WebGL renderer and useful output counts; no candidate comparison is performed here',
      limitations: [
        'Shared workstation; background contention is not isolated',
        'Browser clock quantization may produce zero apply samples; zeros do not imply zero work',
        'Source latest-stat polling cannot establish per-operation readback/publish p95',
      ],
    },
    runs,
  };
  const output = path.resolve(values.output!);
  mkdirSync(path.dirname(output), { recursive: true });
  writeFileSync(output, JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(`Saved ${output}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error.stack ?? error}\n`);
  process.exitCode = 1;
});
