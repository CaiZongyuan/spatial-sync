import type { BrowserContext } from '@playwright/test';

export async function cleanupObserver(context: Pick<BrowserContext, 'close'> | undefined, routes: readonly string[], priorFailures: readonly unknown[] = []) {
  // Stop owned sessions while Playwright finishes tracing and closes the observer.
  const closing = (async () => {
    try { await context?.close(); return []; } catch (error) { return [error]; }
  })();
  const deletionFailures: unknown[] = [];
  for (const route of routes) {
    try {
      // Node HTTP remains usable after Playwright cancels the browser operation.
      const response = await fetch(route, { method: 'DELETE', signal: AbortSignal.timeout(5000) });
      if (!response.ok) throw new Error(`Owned session cleanup failed: HTTP ${response.status}`);
    } catch (error) { deletionFailures.push(error); }
  }
  const failures = [...priorFailures, ...(await closing), ...deletionFailures];
  if (failures.length === 1) throw failures[0];
  if (failures.length > 1) throw new AggregateError(failures, 'Journey and cleanup failures', { cause: failures[0] });
}
