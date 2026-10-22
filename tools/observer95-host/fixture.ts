import { test as base } from '@playwright/test';
import { createRequire } from 'node:module';
import { createHash, randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';

type Obj = Record<string, any>;
const target = 'real SyntheticSource observers share pause, heartbeat, Step, Reset and Resume';
const sha = (value: string | Buffer) => createHash('sha256').update(value).digest('hex');
const fnSha = (value: Function) => sha(Function.prototype.toString.call(value));
async function bounded<T>(promise: Promise<T>, ms: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try { return await Promise.race([promise, new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error('host metadata export deadline')), ms);
  })]); } finally { clearTimeout(timer); }
}

export const test = base.extend<{ observer95Host: void }>({
  observer95Host: [async ({ browser, context, page }, use, info) => {
    if (info.title !== target) { await use(); return; }
    const state: Obj = { nonce: randomUUID(), rows: [], identities: [], errors: [], counts: {}, overflow: false,
      installed: false, restored: false, boundary: 'prewrite / Node-writable-return / parsed-CDP-reply' };
    const pending = new Map<string, Obj>();
    let core: Obj | undefined, impl: Obj | undefined, conn: Obj | undefined, transport: Obj | undefined;
    let originalLogger: Function | undefined, originalSend: Function | undefined;
    let loggerWrapper: Function | undefined, sendWrapper: Function | undefined;
    let client: Obj | undefined, primaryContext: Obj | undefined, primaryPage: Obj | undefined;
    let observerContext: Obj | undefined, observerPage: Obj | undefined;
    let originalNewContext: Function | undefined, contextWrapper: Function | undefined;
    const stamp = () => ({ hrtimeNs: process.hrtime.bigint().toString(), nodeMs: performance.now(),
      nodeOriginMs: performance.timeOrigin, dateMs: Date.now(), pid: process.pid,
      pwMs: core?.iso.monotonicTime(), pwOriginMs: core?.iso.timeOrigin() });
    const emit = (stage: string, fields: Obj) => {
      if (state.rows.length >= 4096) { state.overflow = true; return; }
      state.rows.push({ stage, ...stamp(), ...fields });
    };
    const safe = (callback: () => void) => { try { callback(); } catch { state.recorderError = true; } };
    const key = (message: Obj) => `${state.nonce}:${typeof message.sessionId === 'string' ? message.sessionId : ''}:${message.id}`;
    const refresh = () => {
      for (const role of ['A', 'B']) {
        const exactContext = role === 'A' ? primaryContext : observerContext;
        if (!exactContext) continue;
        const candidates = [...impl!._crPages.values()].filter((candidate: Obj) => candidate._browserContext === exactContext);
        if (candidates.length > 1) { state.identityAmbiguous = true; continue; }
        if (!candidates.length) continue; // A previously verified B identity survives its original close.
        const candidate: Obj = candidates[0];
        const exactPage = role === 'A' ? primaryPage : observerPage;
        if (exactPage && candidate._page !== exactPage) { state.identityAmbiguous = true; continue; }
        if (role === 'B' && !observerPage) observerPage = candidate._page;
        const sessionId = candidate._mainFrameSession?._client?._sessionId;
        if (typeof candidate._targetId !== 'string' || typeof candidate._page?.guid !== 'string' || typeof sessionId !== 'string') continue;
        const identity = { role, targetId: candidate._targetId, pageId: candidate._page.guid, contextId: exactContext.guid, sessionId };
        const previous = state.identities.find((row: Obj) => row.role === role);
        if (previous && (previous.pageId !== identity.pageId || previous.sessionId !== sessionId)) state.identityAmbiguous = true;
        else if (!previous) state.identities.push(identity);
      }
    };
    try {
      const require = createRequire(resolve(process.cwd(), 'package.json'));
      const testEntry = require.resolve('@playwright/test/package.json');
      const pwEntry = require.resolve('playwright/package.json', { paths: [dirname(testEntry)] });
      const corePath = require.resolve('playwright-core/lib/coreBundle', { paths: [dirname(pwEntry)] });
      const corePackage = require.resolve('playwright-core/package.json', { paths: [dirname(pwEntry)] });
      const pins: Obj = JSON.parse(readFileSync(resolve(__dirname, 'observer95-host-pins.json'), 'utf8'));
      if (JSON.parse(readFileSync(corePackage, 'utf8')).version !== pins.version || sha(readFileSync(corePath)) !== pins.coreSha256)
        throw new Error('Pinned Playwright version/source mismatch');
      core = require(corePath);
      client = (browser as any)._connection;
      if (client !== core!.inprocess.playwright._connection || typeof client.toImpl !== 'function'
        || typeof core!.iso.monotonicTime !== 'function' || typeof core!.iso.timeOrigin !== 'function')
        throw new Error('Collocated module/clock/connection provenance mismatch');
      impl = client!.toImpl(browser); primaryContext = client!.toImpl(context); primaryPage = client!.toImpl(page);
      conn = impl!._connection; transport = conn!._transport;
      if (!(impl!._crPages instanceof Map) || !(conn!._sessions instanceof Map)
        || typeof conn!._protocolLogger !== 'function' || typeof transport!.send !== 'function'
        || typeof transport!._pipeWrite?.write !== 'function') throw new Error('Private shape mismatch');
      originalLogger = conn!._protocolLogger; originalSend = transport!.send;
      if (fnSha(originalLogger!) !== pins.loggerFunctionSha256 || fnSha(originalSend!) !== pins.sendFunctionSha256
        || core!.utils.debugLogger.isEnabled('protocol')) throw new Error('Original hook/DEBUG ownership mismatch');
      if (Object.prototype.hasOwnProperty.call(browser, 'newContext')) throw new Error('Existing newContext hook; not overwritten');
      originalNewContext = browser.newContext;
      contextWrapper = async function(this: unknown, ...args: unknown[]) {
        const result = await Reflect.apply(originalNewContext!, this, args);
        safe(() => {
          if (observerContext || (result as any)._connection !== client) { state.identityAmbiguous = true; return; }
          observerContext = client!.toImpl(result);
        });
        return result;
      };
      state.source = { version: pins.version, coreSha256: pins.coreSha256 }; emit('installed-clock', {});
      loggerWrapper = function(this: unknown, direction: string, message: Obj) {
        safe(() => {
          if (direction === 'send') {
            const method = message.method;
            if (!['Runtime.callFunctionOn', 'Runtime.evaluate', 'Page.captureScreenshot'].includes(method)) return;
            refresh();
            const identity = state.identities.find((row: Obj) => row.sessionId === (message.sessionId ?? ''));
            const args = message.params?.arguments;
            const exactExpression = method === 'Runtime.callFunctionOn' && Array.isArray(args) && args.length >= 4
              && typeof args[0]?.objectId === 'string'
              && args[1]?.value === true && args[2]?.value === true && typeof args[3]?.value === 'string'
              && sha(args[3].value) === pins.sampleExpressionSha256;
            const snapshot = method === 'Runtime.evaluate' && typeof message.params?.expression === 'string'
              && message.params.expression.startsWith('window["__playwright_snapshot_streamer_')
              && message.params.expression.includes('].captureSnapshot(');
            state.counts[method] = (state.counts[method] ?? 0) + 1;
            if (!exactExpression && !snapshot && method !== 'Page.captureScreenshot') return;
            if (pending.size >= 128) { state.pendingOverflow = true; return; }
            const metadata = { id: message.id, sessionId: message.sessionId ?? '', method,
              role: identity?.role, targetId: identity?.targetId, pageId: identity?.pageId,
              isSample: Boolean(exactExpression && identity?.role === 'B'),
              exactExpression: Boolean(exactExpression), isSnapshot: snapshot };
            if (exactExpression && identity?.role === 'A') state.controlMatchObserved = true;
            pending.set(key(message), metadata); emit('prewrite', metadata);
          } else if (direction === 'receive') {
            const metadata = pending.get(key(message));
            if (!metadata) return;
            emit('parsed-reply', { ...metadata, cdpError: Boolean(message.error), jsException: Boolean(message.result?.exceptionDetails) });
            pending.delete(key(message));
          }
        });
        return Reflect.apply(originalLogger!, this, [direction, message]);
      };
      sendWrapper = function(this: unknown, message: Obj) {
        try {
          const result = Reflect.apply(originalSend!, this, [message]);
          safe(() => { const metadata = pending.get(key(message)); if (metadata) emit('node-writable-return', metadata); });
          return result;
        } catch (error) {
          safe(() => { const metadata = pending.get(key(message)); if (metadata) emit('node-write-throw', metadata); });
          throw error;
        }
      };
      conn!._protocolLogger = loggerWrapper; transport!.send = sendWrapper;
      (browser as any).newContext = contextWrapper;
      if (conn!._protocolLogger !== loggerWrapper || transport!.send !== sendWrapper || browser.newContext !== contextWrapper) throw new Error('Hook installation mismatch');
      state.installed = true;
    } catch (error) { state.errors.push(String(error)); }
    try { await use(); }
    finally {
      state.business = { status: info.status, expectedStatus: info.expectedStatus, durationMs: info.duration };
      safe(() => { if (impl) refresh(); });
      if (contextWrapper) {
        try {
          if (browser.newContext === contextWrapper) delete (browser as any).newContext;
          else state.errors.push('Context restore conflict; not overwritten');
        } catch { state.errors.push('Context restore failed'); }
      }
      if (transport && sendWrapper) {
        try {
          if (transport.send === sendWrapper) transport.send = originalSend;
          else state.errors.push('Transport restore conflict; not overwritten');
        } catch { state.errors.push('Transport restore failed'); }
      }
      if (conn && loggerWrapper) {
        try {
          if (conn._protocolLogger === loggerWrapper) conn._protocolLogger = originalLogger;
          else state.errors.push('Logger restore conflict; not overwritten');
        } catch { state.errors.push('Logger restore failed'); }
      }
      state.restored = Boolean(state.installed && conn?._protocolLogger === originalLogger && transport?.send === originalSend
        && browser.newContext === originalNewContext);
      state.pending = [...pending.values()];
      state.identityComplete = state.identities.length === 2 && !state.identityAmbiguous
        && new Set(state.identities.map((row: Obj) => row.sessionId)).size === 2;
      state.packetComplete = Boolean(state.installed && state.restored && state.identityComplete && !state.errors.length
        && state.controlMatchObserved && !state.overflow && !state.pendingOverflow && !state.recorderError);
      const text = JSON.stringify(state);
      const output = resolve(info.config.rootDir, '../../.scratch/observer95-host');
      try {
        await bounded(mkdir(output, { recursive: true }), 250);
        if (Buffer.byteLength(text) > 2 * 1024 * 1024) throw new Error('Host metadata byte budget');
        await bounded(writeFile(resolve(output, 'host-state.json'), text), 1000);
      } catch (error) { console.error('[observer95-host] metadata unavailable:', String(error)); }
    }
  }, { auto: true }],
});
