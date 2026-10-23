import { expect, test } from '@playwright/test';
import type { AvailablePreview, PlanDocument } from '../../apps/web/src/lib/plans/model';
import { api, centrifuge, headers, condition, move, close, json, createSession, submit, approve, closeLid, stopSession, geometry } from './plan-fixture';

test('real operation plan detail preserves revised geometry, commands, themes and responsive layout', async ({ page, request, context }, info) => {
  const { sessionId } = await createSession(request);
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  try {
    const steps = [close, { id: 2, wait: condition, timeoutSeconds: 2 }, { id: 3, check: condition }, move(4)];
    const plan = await submit(request, 'Browser: independent revised plan detail', steps);
    expect(plan.revision).toBe(1);
    await closeLid(request, sessionId, plan);
    const ref = { planId: plan.id, planRevision: 1 };
    const waited = await json<{ satisfied: boolean }>(await request.get(`${api}/api/v1/sessions/${sessionId}/wait`, {
      headers, params: { key: centrifuge, property: 'lidOpen', op: 'equals', value: 'false', ...ref, stepId: 2 },
    }));
    expect(waited.satisfied).toBe(true);
    const checked = await json<{ satisfied: boolean }>(await request.post(`${api}/api/v1/sessions/${sessionId}/check`, {
      headers, data: { key: centrifuge, property: 'lidOpen', op: 'equals', value: false, ...ref, stepId: 3 },
    }));
    expect(checked.satisfied).toBe(true);
    const added = await approve(request, await json<PlanDocument>(await request.post(`${api}/api/v1/plans/${plan.id}/revisions`, {
      headers, data: { title: plan.title, target: 'session', steps: [...steps, { id: 5, check: condition }] },
    })));
    expect(added.revision).toBe(2);
    const revised = await approve(request, await json<PlanDocument>(await request.post(`${api}/api/v1/plans/${plan.id}/revisions`, {
      headers, data: { title: plan.title, target: 'session', steps: [...steps.slice(0, 3), move(4, [.2, .4, -.5, 0, 0, 0, 0]), { id: 5, check: condition }] },
    })));
    expect(revised.revision).toBe(3);
    expect(revised.steps.filter(step => step.status === 'done').map(step => step.id)).toEqual([1, 2, 3]);
    const updated = await json<AvailablePreview>(await request.get(`${api}/api/v1/plans/${plan.id}/steps/4/preview?revision=3`));
    await page.goto(`/plans/${plan.id}?step=4`);
    await expect(page.getByRole('table')).toContainText(plan.id);
    await expect(page.getByRole('table')).toContainText('issue26-e2e');
    const detailGeometry = await geometry(page, updated, true);
    await info.attach('detail-rendered-preview.json', { body: JSON.stringify({ preview: updated, rendered: detailGeometry }), contentType: 'application/json' });
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await page.getByRole('button', { name: '复制', exact: true }).click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(await page.getByTestId('plan-command').textContent());
    for (const theme of ['dark', 'light']) {
      await page.getByLabel('Color theme').selectOption(theme);
      await page.screenshot({ path: info.outputPath(`plans-${theme}-1440.png`), fullPage: true });
    }
    await page.setViewportSize({ width: 400, height: 960 });
    await page.screenshot({ path: info.outputPath('plans-light-400.png'), fullPage: true });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(400);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const reduced = await geometry(page, updated, true);
    const offsets = await page.evaluate(async () => {
      const values = [];
      for (let frame = 0; frame < 4; frame++) {
        await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
        values.push(window.__planViewDebug?.()?.dashOffset);
      }
      return values;
    });
    expect(offsets).toEqual(Array(4).fill(reduced.dashOffset));
    expect(errors).toEqual([]);
    await info.attach('rendered-preview.json', { body: JSON.stringify({ preview: updated, rendered: reduced }), contentType: 'application/json' });
  } finally { await stopSession(request, sessionId, info); }
});
