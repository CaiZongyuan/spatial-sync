import { test as base, type CDPSession, type BrowserContext, type Page } from '@playwright/test';
import { mkdir, writeFile, appendFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const target = 'real SyntheticSource observers share pause, heartbeat, Step, Reset and Resume';
const stamp = () => ({ hrtimeNs: process.hrtime.bigint().toString(), nodeMs: performance.now(), nodeOriginMs: performance.timeOrigin, dateMs: Date.now(), nodePid: process.pid });
async function bounded<T>(promise: Promise<T>, ms: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([promise, new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error(`native tracing deadline ${ms}ms`)), ms);
    })]);
  } finally { clearTimeout(timer); }
}

export const test = base.extend<{ observer95Native: void }>({
  observer95Native: [async ({ browser, context, page }, use, info) => {
    if (info.title !== target) { await use(); return; }
    const setupUntil = performance.now() + 6000;
    const output = resolve(info.config.rootDir, '../../.scratch/observer95-native');
    const readiness: Record<string, unknown> = { session: false, start: false, beginClock: false };
    const exported: Record<string, unknown> = { complete: false, valid: false };
    const cleanup: Record<string, unknown> = { streamClosed: true, sessionDetached: false };
    const state: Record<string, unknown> = { readiness, exported, cleanup,
      categories: ['toplevel', 'toplevel.flow', 'devtools.timeline', 'v8', 'gpu', 'cc', 'viz', 'renderer.scheduler'],
      clocks: [], buffer: [], targets: [], identities: [], causalUsability: 'unassessed' };
    await bounded(mkdir(output, { recursive: true }), 250).then(
      () => { readiness.output = true; },
      error => { readiness.output = false; readiness.outputError = String(error);
        console.error('[observer95-native] output directory unavailable:', String(error)); },
    );
    let cdp: CDPSession | undefined;
    let startAttempted = false;
    let abandoned = false;
    let stream: string | undefined;
    let pageCount = 0;
    const identifyPage = async (ownedContext: BrowserContext, ownedPage: Page, deadline = performance.now() + 1000) => {
      const row: Record<string, unknown> = { label: `page-${++pageCount}`, before: stamp() };
      (state.identities as unknown[]).push(row);
      let session: CDPSession | undefined;
      let abandonedIdentity = false;
      const acquisition = ownedContext.newCDPSession(ownedPage);
      void acquisition.then(value => {
        if (abandonedIdentity) void bounded(value.detach(), 200).then(
          () => { row.lateDetached = true; }, error => { row.lateDetachError = String(error); },
        );
      }, error => { row.acquireError = String(error); });
      try {
        try { session = await bounded(acquisition, Math.max(1, Math.min(400, deadline - performance.now()))); }
        catch (error) { abandonedIdentity = true; throw error; }
        const [target, frame] = await bounded(Promise.all([
          session.send('Target.getTargetInfo'), session.send('Page.getFrameTree'),
        ]), Math.max(1, Math.min(400, deadline - performance.now())));
        row.targetId = target.targetInfo.targetId; row.browserContextId = target.targetInfo.browserContextId;
        row.frameId = frame.frameTree.frame.id; row.nodePageUrl = ownedPage.url();
      } catch (error) { row.error = String(error); }
      finally {
        if (session) await bounded(session.detach(), Math.max(1, Math.min(200, deadline - performance.now()))).then(
          () => { row.detached = true; }, error => { row.detachError = String(error); },
        );
        row.after = stamp();
      }
    };
    await identifyPage(context, page, Math.min(setupUntil, performance.now() + 1000));
    const originalNew = browser.newContext.bind(browser);
    browser.newContext = async (...args) => {
      const ownedContext = await originalNew(...args);
      ownedContext.on('page', ownedPage => { void identifyPage(ownedContext, ownedPage); });
      return ownedContext;
    };
    const sync = async (label: string, deadline: number) => {
      const before = stamp();
      await bounded(cdp!.send('Tracing.recordClockSyncMarker', { syncId: `observer95-${label}` }), Math.max(1, Math.min(500, deadline - performance.now())));
      (state.clocks as unknown[]).push({ label, before, after: stamp() });
    };
    try {
      const acquisition = browser.newBrowserCDPSession();
      // Consume late acquisition/rejection and detach an abandoned session independently.
      void acquisition.then(value => {
        if (abandoned) void bounded(value.detach(), 250).then(
          () => { cleanup.lateSessionDetached = true; },
          error => { cleanup.lateSessionDetachError = String(error); },
        );
      }, error => { readiness.sessionError = String(error); });
      try { cdp = await bounded(acquisition, Math.max(1, Math.min(1000, setupUntil - performance.now()))); }
      catch (error) { abandoned = true; throw error; }
      readiness.session = true;
      for (const event of ['Target.targetCreated', 'Target.targetInfoChanged', 'Target.targetDestroyed'])
        cdp.on(event, value => {
          const rows = state.targets as unknown[];
          if (rows.length < 64) rows.push({ event, ...stamp(), ...value });
        });
      await bounded(cdp.send('Target.setDiscoverTargets', { discover: true }), Math.max(1, Math.min(500, setupUntil - performance.now())));
      cdp.on('Tracing.bufferUsage', value => {
        const rows = state.buffer as unknown[];
        if (rows.length < 64) rows.push({ ...stamp(), ...value });
      });
      startAttempted = true;
      await bounded(cdp.send('Tracing.start', {
        transferMode: 'ReturnAsStream', streamFormat: 'json', streamCompression: 'gzip',
        bufferUsageReportingInterval: 1000,
        traceConfig: { recordMode: 'recordContinuously', traceBufferSizeInKb: 8192,
          enableSampling: false, enableSystrace: false, includedCategories: state.categories },
      }), Math.max(1, Math.min(2000, setupUntil - performance.now())));
      readiness.start = true;
      await sync('begin', setupUntil);
      readiness.beginClock = true;
    } catch (error) { readiness.error = String(error); }

    // Native tracing readiness never replaces or skips the unchanged business test.
    try { await use(); }
    finally {
      browser.newContext = originalNew;
      state.business = { status: info.status, expectedStatus: info.expectedStatus, durationMs: info.duration };
      const until = performance.now() + 8000;
      const exportUntil = until - 750; // Close/detach/state write share the final 750ms.
      try {
        if (cdp && startAttempted) {
          await sync('end', exportUntil).catch(error => { exported.endClockError = String(error); });
          const complete = new Promise<Record<string, unknown>>(resolve => cdp!.once('Tracing.tracingComplete', resolve));
          // Also try end after a timed-out start: no unbounded tracing is left by this packet.
          await bounded(cdp.send('Tracing.end'), Math.max(1, Math.min(1000, exportUntil - performance.now())));
          const result = await bounded(complete, Math.max(1, exportUntil - performance.now()));
          exported.complete = true; exported.result = result;
          stream = result.stream as string | undefined;
          if (!stream) throw new Error('Tracing returned no stream');
          cleanup.streamClosed = false;
          let bytes = 0;
          const path = resolve(output, 'chrome-native.json.gz');
          await bounded(writeFile(path, Buffer.alloc(0)), Math.max(1, exportUntil - performance.now()));
          while (true) {
            if (performance.now() >= exportUntil) throw new Error('Native export time budget exhausted');
            const chunk = await bounded(cdp.send('IO.read', { handle: stream, size: 262144 }), exportUntil - performance.now());
            const data = Buffer.from(chunk.data, chunk.base64Encoded ? 'base64' : 'utf8');
            bytes += data.length;
            if (bytes > 16 * 1024 * 1024) throw new Error('Native trace byte budget exhausted');
            await bounded(appendFile(path, data), Math.max(1, exportUntil - performance.now()));
            if (chunk.eof) break;
          }
          exported.bytes = bytes;
          exported.valid = !result.dataLossOccurred && !exported.endClockError;
        }
      } catch (error) { exported.error = String(error); exported.valid = false; }
      finally {
        if (cdp && stream) await bounded(cdp.send('IO.close', { handle: stream }), Math.max(1, Math.min(200, until - performance.now()))).then(
          () => { cleanup.streamClosed = true; }, error => { cleanup.streamError = String(error); },
        );
        if (cdp) await bounded(cdp.detach(), Math.max(1, Math.min(200, until - performance.now()))).then(
          () => { cleanup.sessionDetached = true; }, error => { cleanup.sessionError = String(error); },
        );
        state.teardownElapsedMs = performance.now() - (until - 8000);
        state.captureValid = Boolean(readiness.output && readiness.session && readiness.start && readiness.beginClock
          && exported.valid && cleanup.streamClosed && cleanup.sessionDetached);
        const identities = state.identities as Array<Record<string, unknown>>;
        state.identityCaptureComplete = identities.length === 2
          && identities.every(row => row.targetId && row.frameId && row.detached && !row.error)
          && new Set(identities.map(row => row.targetId)).size === 2;
        state.packetComplete = Boolean(state.captureValid && state.identityCaptureComplete);
        await bounded(writeFile(resolve(output, 'native-state.json'), JSON.stringify(state, null, 2)),
          Math.max(1, Math.min(350, until - performance.now()))).catch(error => {
          // A timed-out async file write may finish later; the owned runner still cleans its process.
          console.error('[observer95-native] state write missing or late:', String(error));
        });
      }
    }
  }, { auto: true }],
});
