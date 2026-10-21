import { test as base, type Page } from '@playwright/test';
import { createWriteStream } from 'node:fs';
import { monitorEventLoopDelay } from 'node:perf_hooks';

type Viewport = { width: number; height: number };
let active: { resize(page: Page, viewport: Viewport): Promise<void>; finish(): Promise<void>;
  marks: Record<string, unknown>[] } | undefined;

export async function diagnosticResize(page: Page, viewport: Viewport) {
  if (active) return active.resize(page, viewport);
  return page.setViewportSize(viewport);
}
export async function diagnosticWidth(page: Page) {
  const marks = active?.marks;
  marks?.push({ kind: 'width-send', node: performance.now(), date: Date.now() });
  try {
    const width = await page.evaluate(() => {
      performance.mark('diagnostic-width-getter-enter');
      const value = document.documentElement.scrollWidth;
      performance.mark('diagnostic-width-getter-return', { detail: value });
      return value;
    });
    marks?.push({ kind: 'width-return', node: performance.now(), date: Date.now(), width });
    return width;
  } finally { marks?.push({ kind: 'width-receipt', node: performance.now(), date: Date.now() }); }
}
export async function diagnosticBeforeBlank() {
  try { await active?.finish(); }
  catch (error) { active?.marks.push({ kind: 'telemetry-error', error: String(error) }); }
}

export const test = base.extend<{ liveDiagnostic: void }>({
  liveDiagnostic: [async ({ browser, context, page }, use, info) => {
    const target = 'real simulated devices raise, acknowledge, retain actual history and expose stale readings in both themes';
    if (info.title !== target) { await use(); return; }
    const marks: Record<string, unknown>[] = [];
    const cdp = await browser.newBrowserCDPSession();
    const stream = createWriteStream(info.outputPath('chromium-timeline.jsonl'));
    const histogram = monitorEventLoopDelay({ resolution: 20 });
    let timer: ReturnType<typeof setInterval> | undefined;
    let finished = false, started = false, complete = false, events = 0, stoppedWriting = false;
    cdp.on('Tracing.dataCollected', payload => {
      if (stoppedWriting) return;
      for (const event of payload.value) {
        if (++events <= 150000) stream.write(JSON.stringify(event) + '\n');
      }
    });
    cdp.on('Tracing.tracingComplete', () => { complete = true; });
    await context.addInitScript(() => {
      const keep = (kind: string, value: unknown) => {
        const win = window as Window & { __liveDiagnostic?: unknown[] };
        const ring = win.__liveDiagnostic ??= [];
        if (ring.length < 64) ring.push({ kind, now: performance.now(), origin: performance.timeOrigin, value });
      };
      new PerformanceObserver(list => {
        for (const e of list.getEntries()) keep('longtask', { start: e.startTime, duration: e.duration });
      }).observe({ type: 'longtask', buffered: true });
      window.addEventListener('resize', () => keep('resize', { innerWidth, canvasWidth: document.querySelector('canvas')?.width, canvasHeight: document.querySelector('canvas')?.height }));
    });
    const ping = async () => {
      if (marks.length > 256) return;
      const before = { node: performance.now(), date: Date.now() };
      try {
        await cdp.send('Browser.getVersion');
        marks.push({ kind: 'browser-ping', before, after: { node: performance.now(), date: Date.now() } });
      } catch (error) { marks.push({ kind: 'browser-ping-error', error: String(error) }); }
    };
    const finish = async () => {
      if (finished) return;
      finished = true;
      marks.push({ kind: 'before-cleanup-navigation', node: performance.now(), date: Date.now() });
      clearInterval(timer);
      histogram.disable();
      if (started) {
        const sync = { stage: 'end', date: Date.now(), node: performance.now() };
        await cdp.send('Tracing.recordClockSyncMarker', { syncId: JSON.stringify(sync) });
        marks.push({ kind: 'clock-sync', sync, after: { date: Date.now(), node: performance.now() } });
        await cdp.send('Tracing.end');
        const end = performance.now() + 2000;
        while (!complete && performance.now() < end) await new Promise(resolve => setTimeout(resolve, 20));
      }
      stoppedWriting = true;
      stream.end();
      await new Promise<void>(resolve => stream.once('finish', resolve));
      const state: Record<string, unknown> = { marks, events, complete, truncated: events > 150000,
        eventLoopDelayMs: { max: histogram.max / 1e6, mean: histogram.mean / 1e6, p99: histogram.percentile(99) / 1e6 } };
      // This optional read never controls the oracle or blocks teardown/navigation.
      state.ring = await Promise.race([
        page.evaluate(() => (window as Window & { __liveDiagnostic?: unknown[] }).__liveDiagnostic).catch(String),
        new Promise(resolve => setTimeout(() => resolve('export-unavailable-before-navigation'), 500)),
      ]);
      await info.attach('live-diagnostic', { body: Buffer.from(JSON.stringify(state)), contentType: 'application/json' });
      await info.attach('chromium-timeline', { path: info.outputPath('chromium-timeline.jsonl'), contentType: 'application/x-ndjson' });
    };
    active = { marks, finish, resize: async (ownedPage, viewport) => {
      marks.push({ kind: 'window-start', node: performance.now(), date: Date.now() });
      await cdp.send('Tracing.start', { transferMode: 'ReportEvents',
        categories: 'devtools.timeline,disabled-by-default-devtools.timeline,blink.user_timing,v8,gpu,cc' });
      started = true;
      const sync = { stage: 'start', date: Date.now(), node: performance.now() };
      await cdp.send('Tracing.recordClockSyncMarker', { syncId: JSON.stringify(sync) });
      marks.push({ kind: 'clock-sync', sync, after: { date: Date.now(), node: performance.now() } });
      histogram.enable();
      timer = setInterval(() => { void ping(); }, 500);
      await ping();
      marks.push({ kind: 'resize-send', node: performance.now(), date: Date.now() });
      await ownedPage.setViewportSize(viewport);
      marks.push({ kind: 'resize-receipt', node: performance.now(), date: Date.now() });
    } };
    try { await use(); }
    finally {
      try { await diagnosticBeforeBlank(); }
      finally { active = undefined; await cdp.detach(); }
    }
  }, { auto: true }],
});
