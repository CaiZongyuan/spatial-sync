import { test as base, type Page } from '@playwright/test';

const target = 'real SyntheticSource observers share pause, heartbeat, Step, Reset and Resume';
let runner: Array<Record<string, unknown>> | undefined;
const pageIds = new WeakMap<Page, string>();
export async function diagnosticEvaluate<T>(page: Page, callback: () => T): Promise<T> {
  const start = { kind: 'runner-request', pageId: pageIds.get(page), nodeMs: performance.now(), dateMs: Date.now() };
  runner?.push(start);
  try { return await page.evaluate(callback); }
  finally { runner?.push({ kind: 'runner-receipt', pageId: pageIds.get(page), nodeMs: performance.now(), dateMs: Date.now() }); }
}

export const test = base.extend<{ observerDiagnostic: void }>({
  observerDiagnostic: [async ({ browser, context, page }, use, info) => {
    if (info.title !== target) { await use(); return; }
    const records: Array<Record<string, unknown>> = [];
    runner = [];
    let pageCount = 0;
    const setup = async (ownedContext: typeof context) => {
      await ownedContext.addInitScript(() => {
        const ring: unknown[] = [];
        const win = window as Window & {
          __observerDiagnostic?: (kind: string, value: unknown) => void;
          __observerDiagnosticRing?: unknown[];
        };
        win.__observerDiagnosticRing = ring;
        win.__observerDiagnostic = (kind, value) => {
          if (ring.length < 512) ring.push({ kind, now: performance.now(), origin: performance.timeOrigin, value });
        };
        new PerformanceObserver(list => {
          for (const entry of list.getEntries())
            win.__observerDiagnostic?.('longtask', { start: entry.startTime, duration: entry.duration });
        }).observe({ type: 'longtask', buffered: true });
      });
      const instrument = async (ownedPage: Page) => {
        const pageId = `page-${++pageCount}`;
        pageIds.set(ownedPage, pageId);
        const cdp = await ownedContext.newCDPSession(ownedPage);
        const network: unknown[] = [];
        const clocks: unknown[] = [];
        let frameCount = 0;
        await cdp.send('Network.enable');
        await cdp.send('Performance.enable');
        const calibrate = async (stage: string) => {
          const before = { date: Date.now(), node: performance.now() };
          const metrics = await cdp.send('Performance.getMetrics');
          const wall = await cdp.send('Runtime.evaluate', { expression: '({now:performance.now(),origin:performance.timeOrigin,date:Date.now()})', returnByValue: true });
          clocks.push({ stage, before, after: { date: Date.now(), node: performance.now() },
            metrics: metrics.metrics.filter(m => ['Timestamp', 'NavigationStart'].includes(m.name)), page: wall.result.value });
        };
        await calibrate('begin');
        cdp.on('Network.webSocketFrameReceived', event => {
          const frame = event.response;
          if (++frameCount > 3 && frameCount % 15 !== 0) return;
          if (network.length >= 256) return;
          const value = frame.opcode === 2 ? Buffer.from(frame.payloadData, 'base64') : undefined;
          network.push({ timestamp: event.timestamp, node: performance.now(), opcode: frame.opcode,
            seq: value && value.length >= 24 ? Number(value.readBigUInt64LE(16)) : undefined });
        });
        const originalClose = ownedContext.close.bind(ownedContext);
        let exported = false;
        const finish = async () => {
          if (exported) return;
          exported = true;
          const record: Record<string, unknown> = { pageId, url: ownedPage.url(), network, clocks };
          records.push(record);
          try {
            await calibrate('end');
            record.ring = await ownedPage.evaluate(() => (window as Window & { __observerDiagnosticRing?: unknown[] }).__observerDiagnosticRing);
          } catch (error) { record.exportError = String(error); }
        };
        ownedContext.close = async (...args) => { await finish(); return originalClose(...args); };
        return finish;
      };
      return instrument;
    };
    const instrument = await setup(context);
    const finishPrimary = await instrument(page);
    const originalNew = browser.newContext.bind(browser);
    browser.newContext = async (...args) => {
      const observerContext = await originalNew(...args);
      const observerInstrument = await setup(observerContext);
      const originalPage = observerContext.newPage.bind(observerContext);
      observerContext.newPage = async () => { const observed = await originalPage(); await observerInstrument(observed); return observed; };
      return observerContext;
    };
    try { await use(); }
    finally {
      browser.newContext = originalNew;
      await finishPrimary();
      await info.attach('observer-diagnostic', { body: Buffer.from(JSON.stringify({ runner, records })), contentType: 'application/json' });
      runner = undefined;
    }
  }, { auto: true }],
});
