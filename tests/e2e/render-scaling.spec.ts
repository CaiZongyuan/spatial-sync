import { writeFile } from 'node:fs/promises';
import { expect, test, type Page } from '@playwright/test';
import type {} from '../../apps/web/src/components/viewer/SceneDiagnostics';
import type {} from '../../apps/web/src/components/Workbench';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';
const drawn = (page: Page) =>
  page.evaluate(() => window.__spatialRenderDebug!.drawnEntities().sort());
const members = (page: Page) =>
  page.evaluate(
    () => window.__spatialDebug!.getSubscriptionMetrics().memberIds,
  );
const moving = '/World/Stress/Moving/Body';

for (const mode of ['individual', 'instanced']) {
  for (const format of ['json', 'binary']) {
    test(`real ${mode} ${format}: packed selection, scope visibility and Site/Joint dependencies`, async ({
      page,
      request,
    }, info) => {
      const errors: string[] = [];
      const evidence: unknown[] = [];
      page.on('pageerror', (error) => errors.push(error.message));
      for (const scene of ['stress-100', 'lab-demo']) {
        const created = await request.post(`${api}/api/v1/sessions`, {
          data: { sceneId: scene, source: 'synthetic' },
        });
        expect(created.ok()).toBe(true);
        const { sessionId } = await created.json();
        try {
          await page.goto(
            `/sessions/${sessionId}?format=${format}&render=${mode}`,
          );
          await expect(page.getByTestId('connection-status')).toHaveText(
            'connected',
          );
          await expect
            .poll(async () => (await members(page)).length)
            .toBe(scene === 'stress-100' ? 100 : 24);
          await page.getByRole('button', { name: 'Pause simulation' }).click();
          await expect(page.getByTestId('session-state')).toHaveText('PAUSED');
          if (scene === 'stress-100') {
            await expect.poll(async () => (await drawn(page)).length).toBe(100);
            await page.evaluate(() =>
              window.__spatialRenderDebug!.setCamera({
                position: [0, 8, 0.01],
                target: [0, 0.45, 0],
                fov: 44,
                near: 0.01,
                far: 100,
              }),
            );
            const key = moving + '00017'; // streamId 151 is neither dense slot nor instanceId.
            await page.waitForTimeout(150);
            const point = await page.evaluate(
              (key) => window.__spatialRenderDebug!.projectEntity(key),
              key,
            );
            expect(point).toBeTruthy();
            const hits = await page.evaluate(
              (point) => window.__spatialRenderDebug!.pick(point!.x, point!.y),
              point,
            );
            expect(hits[0].key).toBe(key);
            if (mode === 'instanced') expect(point!.instanceId).toBeDefined();
            await page.mouse.click(point!.x, point!.y);
            await expect(
              page.locator('.tree-row.selected .tree-select'),
            ).toHaveAttribute('data-testid', `entity-${key}`);
            await page.getByLabel('Stream scope').selectOption('entities');
            await expect.poll(() => members(page)).toEqual([151]);
            await expect.poll(() => drawn(page)).toEqual([key]);
            expect(
              await page.evaluate(
                (key) => window.__spatialDebug!.getRenderedPose(key),
                moving + '00000',
              ),
            ).toBeUndefined();
            // Packing changed instanceId; a real mouse click must still resolve the same entity.
            const packed = await page.evaluate(
              (key) => window.__spatialRenderDebug!.projectEntity(key),
              key,
            );
            if (mode === 'instanced') expect(packed!.instanceId).toBe(0);
            await page.mouse.click(packed!.x, packed!.y);
            await expect(
              page.locator('.tree-row.selected .tree-select'),
            ).toHaveAttribute('data-testid', `entity-${key}`);
            await page
              .getByRole('button', { name: 'Hide Body 00017', exact: true })
              .click();
            await expect.poll(() => drawn(page)).toEqual([]);
            const hiddenHits = await page.evaluate(
              (point) => window.__spatialRenderDebug!.pick(point!.x, point!.y),
              packed,
            );
            expect(hiddenHits.map((hit) => hit.key)).not.toContain(key);
            await page.mouse.click(packed!.x, packed!.y);
            await expect(
              page.locator('.tree-row.selected .tree-select'),
            ).toHaveCount(0);
            await page
              .getByRole('button', { name: 'Show Body 00017', exact: true })
              .click();
            await page.getByLabel('Stream scope').selectOption('all');
            await expect.poll(async () => (await drawn(page)).length).toBe(100);
            await page.evaluate(() =>
              window.__spatialRenderDebug!.setCamera({
                position: [100, 8, 0.01],
                target: [100, 0.45, 0],
                fov: 44,
                near: 0.01,
                far: 100,
              }),
            );
            await page.getByLabel('Stream scope').selectOption('viewport');
            await expect.poll(() => members(page)).toEqual([]);
            await expect.poll(() => drawn(page)).toEqual([]);
            await page.evaluate(() =>
              window.__spatialRenderDebug!.setCamera({
                position: [0, 8, 0.01],
                target: [0, 0.45, 0],
                fov: 44,
                near: 0.01,
                far: 100,
              }),
            );
            await expect
              .poll(async () => (await members(page)).length)
              .toBe(100);
            await expect.poll(async () => (await drawn(page)).length).toBe(100);
            evidence.push({
              scene,
              mode,
              format,
              key,
              initialInstanceId: point!.instanceId,
              packedInstanceId: packed!.instanceId,
              hits,
              hiddenHits,
            });
          } else {
            const robot = '/World/Lab/Robot',
              link1 = robot + '/Link1',
              link2 = robot + '/Link2';
            const site = robot + '/tcp',
              joint = robot + '/Joint2';
            await page.getByLabel('Stream scope').selectOption('device');
            await page.getByLabel('Subscription group').selectOption(robot);
            await expect.poll(() => members(page)).toEqual([1, 2]);
            await expect
              .poll(() => drawn(page))
              .toEqual(
                [
                  link1,
                  link2,
                  robot + '/Joint1/anchor',
                  joint + '/anchor',
                  site,
                ].sort(),
              );
            await page.getByTestId(`entity-${site}`).click();
            await page.getByLabel('Stream scope').selectOption('entities');
            await expect.poll(() => members(page)).toEqual([2]);
            await expect
              .poll(() => drawn(page))
              .toEqual([link2, joint + '/anchor', site].sort());
            await page.getByTestId(`entity-${joint}`).click();
            await expect.poll(() => members(page)).toEqual([1, 2]);
            await expect
              .poll(() => drawn(page))
              .toEqual(
                [
                  link1,
                  link2,
                  robot + '/Joint1/anchor',
                  joint + '/anchor',
                  site,
                ].sort(),
              );
            await page
              .getByRole('button', { name: 'Hide Link2', exact: true })
              .click();
            await expect
              .poll(() => drawn(page))
              .toEqual([link1, robot + '/Joint1/anchor'].sort());
            evidence.push({
              scene,
              mode,
              format,
              members: await members(page),
              visibleAfterParentHidden: await drawn(page),
            });
          }
        } finally {
          await request.delete(`${api}/api/v1/sessions/${sessionId}`);
        }
      }
      expect(errors).toEqual([]);
      const output = info.outputPath('render-visibility-evidence.json');
      await writeFile(output, JSON.stringify(evidence, null, 2));
      await info.attach('render-visibility-evidence', {
        path: output,
        contentType: 'application/json',
      });
    });
  }
}
