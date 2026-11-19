import type { Page } from '@playwright/test';

/** Temporary #224 timing evidence; excludes HTTP headers, bodies and credentials. */
export async function observeCatalog(page: Page, rows: unknown[]) {
  let dropped = 0;
  const append = (value: unknown) => { if (rows.length < 600) rows.push(value); else dropped++; };
  page.on('crash', () => append({ event: 'page-crash', receivedWall: Date.now() }));
  page.on('pageerror', error => append({ event: 'page-error', receivedWall: Date.now(), name: error.name }));
  page.on('console', message => {
    const text = message.text();
    if (text.startsWith('[DEBUG-issue224]')) append({ receivedWall: Date.now(), ...JSON.parse(text.slice('[DEBUG-issue224]'.length)) });
  });
  await page.addInitScript(() => {
    const observed = (url: string) => /\/library\/search\?|\/maintenance\/evidence\/|\/estimate\?/.test(url);
    const path = (url: string) => { const parsed = new URL(url, location.href); return parsed.pathname + parsed.search; };
    let recorded = 0;
    const record = (event: string, details: Record<string, unknown> = {}) => {
      if (recorded++ >= 500) {
        if (recorded === 501) console.debug('[DEBUG-issue224]' + JSON.stringify({ event: 'capture-truncated', wall: Date.now(), limit: 500 }));
        return;
      }
      console.debug('[DEBUG-issue224]' + JSON.stringify({ event, at: performance.now(), wall: Date.now(),
        document: location.pathname + location.search, visibility: document.visibilityState, ...details }));
    };
    const fetchOriginal = window.fetch;
    window.fetch = (...args: Parameters<typeof fetch>) => {
      const url = args[0] instanceof Request ? args[0].url : String(args[0]);
      if (!observed(url)) return fetchOriginal(...args);
      record('fetch-start', { path: path(url) });
      return fetchOriginal(...args).then(response => {
        record('fetch-headers', { path: path(url), status: response.status });
        return response;
      });
    };
    const jsonOriginal = Response.prototype.json;
    Response.prototype.json = function () {
      if (!observed(this.url)) return jsonOriginal.call(this);
      record('json-start', { path: path(this.url) });
      return jsonOriginal.call(this).then(value => { record('json-complete', { path: path(this.url) }); return value; });
    };
    let last = '';
    new MutationObserver(() => {
      const state = {
        detail: !!document.querySelector('.catalog-detail'), evidence: !!document.querySelector('.catalog-detail pre'),
        estimate: !!document.querySelector('.catalog-estimate h2'), empty: !!document.querySelector('.asset-empty h2'),
      };
      const key = JSON.stringify(state) + location.href;
      if (key !== last) { last = key; record('dom', state); }
    }).observe(document, { childList: true, subtree: true });
    new PerformanceObserver(list => {
      for (const entry of list.getEntries()) record('long-task', { start: entry.startTime, duration: entry.duration });
    }).observe({ type: 'longtask', buffered: true });
    let tick = performance.now();
    setInterval(() => { const now = performance.now(); record('heartbeat', { elapsed: now - tick }); tick = now; }, 1000);
  });
  let driverTick = performance.now();
  const driverTimer = setInterval(() => {
    const now = performance.now();
    append({ event: 'driver-heartbeat', receivedWall: Date.now(), elapsed: now - driverTick });
    driverTick = now;
  }, 1000);
  return () => { clearInterval(driverTimer); rows.push({ event: 'capture-end', dropped }); };
}
