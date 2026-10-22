import { test as base, type Page, type BrowserContext } from '@playwright/test';

const target = 'real SyntheticSource observers share pause, heartbeat, Step, Reset and Resume';
type Entry = Record<string, unknown>;
type Observed = { pageId: string; entries: Entry[]; constantStarted: boolean };
const observed = new WeakMap<Page, Observed>();
const stamp = () => ({ nodeMs: performance.now(), dateMs: Date.now() });
function note(record: Observed, kind: string, value: Entry = {}) {
  if (record.entries.length < 256) record.entries.push({ kind, ...stamp(), ...value });
}

// The diagnostic deadline labels responsiveness; it never replaces the business predicate.
export async function diagnosticEvaluate<T>(page: Page, callback: () => T): Promise<T> {
  const record = observed.get(page);
  if (!record) return page.evaluate(callback);
  note(record, 'sample-request');
  const sample = page.evaluate(callback);
  if (!record.constantStarted) {
    record.constantStarted = true;
    note(record, 'constant-request');
    let expired = false;
    const timer = setTimeout(() => { expired = true; note(record, 'constant-node-deadline', { deadlineMs: 1500 }); }, 1500);
    // Exactly one constant request per page; both success and late rejection are consumed.
    void page.evaluate(() => 1).then(
      value => { clearTimeout(timer); note(record, 'constant-receipt', { value, late: expired }); },
      error => { clearTimeout(timer); note(record, 'constant-rejection', { error: String(error), late: expired }); },
    );
  }
  try {
    const value = await sample;
    note(record, 'sample-receipt', { value });
    return value;
  } catch (error) { note(record, 'sample-rejection', { error: String(error) }); throw error; }
}

async function bounded<T>(promise: Promise<T>, ms = 750): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([promise, new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error(`diagnostic export deadline ${ms}ms`)), ms);
    })]);
  } finally { clearTimeout(timer); }
}

export const test = base.extend<{ observer95Diagnostic: void }>({
  observer95Diagnostic: [async ({ browser, context, page }, use, info) => {
    if (info.title !== target) { await use(); return; }
    const records: Array<Observed & { url?: string; ring?: unknown; clock?: unknown; exportError?: string }> = [];
    const finishers: Array<() => Promise<void>> = [];
    let pageCount = 0;
    async function setupContext(ownedContext: BrowserContext) {
      await ownedContext.addInitScript(() => {
        const ring: unknown[] = [];
        const win = window as Window & { __observer95?: (kind: string, value: unknown) => void; __observer95Ring?: unknown[] };
        win.__observer95Ring = ring;
        win.__observer95 = (kind, value) => {
          if (ring.length < 512) ring.push({ kind, now: performance.now(), origin: performance.timeOrigin, value });
        };
        new PerformanceObserver(list => {
          for (const entry of list.getEntries()) win.__observer95?.('longtask', { start: entry.startTime, duration: entry.duration });
        }).observe({ type: 'longtask', buffered: true });
        let last = performance.now(), logged = last;
        const raf = (now: number) => {
          const gap = now - last;
          if (gap > 250 || now - logged >= 1000) { win.__observer95?.('raf', { gap }); logged = now; }
          last = now; requestAnimationFrame(raf);
        };
        requestAnimationFrame(raf);
        // Same native socket objects/prototype; no third connection or subscription.
        window.WebSocket = new Proxy(window.WebSocket, {
          construct(Constructor, args) {
            const socket = Reflect.construct(Constructor, args) as WebSocket;
            let count = 0;
            socket.addEventListener('message', event => {
              if (++count <= 16 || count % 15 === 0)
                win.__observer95?.('js-ws-receive', { count, binary: event.data instanceof ArrayBuffer });
            });
            socket.addEventListener('close', event => win.__observer95?.('js-ws-close', { code: event.code, clean: event.wasClean }));
            return socket;
          },
        });
      });
    }
    async function setupPage(ownedContext: BrowserContext, ownedPage: Page) {
      const record: typeof records[number] = { pageId: `page-${++pageCount}`, entries: [], constantStarted: false };
      records.push(record); observed.set(ownedPage, record);
      const cdp = await ownedContext.newCDPSession(ownedPage);
      await cdp.send('Network.enable');
      await cdp.send('Performance.enable');
      let cdpCount = 0;
      cdp.on('Network.webSocketFrameReceived', event => {
        if (++cdpCount <= 16 || cdpCount % 15 === 0)
          note(record, 'cdp-ws-receive', { timestamp: event.timestamp, opcode: event.response.opcode });
      });
      ownedPage.on('websocket', socket => {
        note(record, 'node-browser-ws-created', { path: new URL(socket.url()).pathname });
        let count = 0;
        socket.on('framereceived', event => {
          if (++count > 16 && count % 15 !== 0) return;
          if (Buffer.isBuffer(event.payload)) {
            const bytes = event.payload;
            note(record, 'node-browser-ws-frame', { count, bytes: bytes.length,
              seq: bytes.length >= 24 ? Number(bytes.readBigUInt64LE(16)) : undefined });
          } else {
            try { const value = JSON.parse(event.payload); note(record, 'node-browser-ws-text', { count, type: value.type }); }
            catch { note(record, 'node-browser-ws-text', { count, invalidJson: true }); }
          }
        });
        socket.on('close', () => note(record, 'node-browser-ws-close'));
        socket.on('socketerror', error => note(record, 'node-browser-ws-error', { error }));
      });
      let statsTimer: ReturnType<typeof setInterval> | undefined;
      let statsBusy = false;
      let closed = false;
      ownedPage.on('framenavigated', frame => {
        if (frame !== ownedPage.mainFrame() || record.pageId !== 'page-2' || statsTimer) return;
        const match = new URL(frame.url()).pathname.match(/^\/sessions\/([^/]+)$/);
        if (!match) return;
        const sample = async () => {
          if (closed || statsBusy) return;
          statsBusy = true;
          note(record, 'node-stats-request');
          try {
            const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';
            const response = await fetch(`${api}/api/v1/sessions/${match[1]}/stats`, { signal: AbortSignal.timeout(800) });
            const value = await response.json();
            note(record, 'node-stats-receipt', { status: response.status, state: value.state, seq: value.seq,
              publishedFrames: value.publishedFrames, clients: value.clients });
          } catch (error) { note(record, 'node-stats-error', { error: String(error) }); }
          finally { statsBusy = false; }
        };
        void sample(); statsTimer = setInterval(() => void sample(), 1000);
      });
      const begin = stamp();
      const metrics = await cdp.send('Performance.getMetrics');
      note(record, 'clock-begin', { before: begin, metrics: metrics.metrics.filter(value => value.name === 'Timestamp') });
      let finished = false;
      const finish = async () => {
        if (finished) return;
        finished = true; closed = true; clearInterval(statsTimer); record.url = ownedPage.url();
        const before = stamp();
        try {
          const value = await bounded(ownedPage.evaluate(() => ({
            ring: (window as Window & { __observer95Ring?: unknown[] }).__observer95Ring,
            clock: { now: performance.now(), origin: performance.timeOrigin, date: Date.now() },
          })));
          record.ring = value.ring; record.clock = { before, page: value.clock, after: stamp() };
        } catch (error) { record.exportError = String(error); }
        try { note(record, 'clock-end-cdp', { before: stamp(), metrics: (await bounded(cdp.send('Performance.getMetrics'))).metrics.filter(value => value.name === 'Timestamp') }); }
        catch (error) { note(record, 'clock-end-error', { error: String(error) }); }
      };
      finishers.push(finish);
      const originalClose = ownedContext.close.bind(ownedContext);
      ownedContext.close = async (...args) => { await finish(); return originalClose(...args); };
    }
    await setupContext(context); await setupPage(context, page);
    const originalNew = browser.newContext.bind(browser);
    browser.newContext = async (...args) => {
      const ownedContext = await originalNew(...args);
      await setupContext(ownedContext);
      const originalPage = ownedContext.newPage.bind(ownedContext);
      ownedContext.newPage = async () => { const result = await originalPage(); await setupPage(ownedContext, result); return result; };
      return ownedContext;
    };
    try { await use(); }
    finally {
      browser.newContext = originalNew;
      for (const finish of finishers) await finish();
      await info.attach('observer95-diagnostic', { body: Buffer.from(JSON.stringify({ records })), contentType: 'application/json' });
      observed.delete(page);
    }
  }, { auto: true }],
});
