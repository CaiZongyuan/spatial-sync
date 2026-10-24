import { expect, test } from '@playwright/test';
import { execFile, spawn } from 'node:child_process';
import { writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { resolve } from 'node:path';
import { promisify } from 'node:util';
import { cleanupOwnedProcesses, finishCleanup, type OwnedProcess } from './owned-cleanup';

async function freePort() {
  return new Promise<number>((resolve, reject) => {
    const server = createServer(); server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const port = (server.address() as { port: number }).port;
      server.close(error => error ? reject(error) : resolve(port));
    });
  });
}

test('scene-first results keep platform v2 0.00 m separate from an unsubmitted 0.20 m draft and validate a new v3', async ({ page, playwright }, info) => {
  const [apiPort, webPort] = await Promise.all([freePort(), freePort()]);
  const api = `http://127.0.0.1:${apiPort}`, web = `http://127.0.0.1:${webPort}`;
  const python = process.env.SPATIAL_PYTHON ?? resolve(process.platform === 'win32' ? '../../.venv/Scripts/python.exe' : '../../.venv/bin/python');
  const owned = resolve('../../tests/fixtures/owned_processes.py'), execute = promisify(execFile);
  const processes: OwnedProcess[] = [], original: unknown[] = [], remote: string[] = [], errors: string[] = [];
  const http = await playwright.request.newContext();
  const start = (label: string, executable: string, args: string[], cwd: string, env: NodeJS.ProcessEnv) => {
    const child = spawn(executable, args, { cwd, env, windowsHide: true });
    const record: OwnedProcess = { child, label, output: '', exited: new Promise<void>(resolve => child.once('exit', () => resolve())),
      identity: execute(python, [owned, 'snapshot', String(child.pid)]).then(value => ({ identity: value.stdout }), error => ({ error })) };
    child.stdout?.on('data', chunk => { record.output += chunk; }); child.stderr?.on('data', chunk => { record.output += chunk; });
    child.once('error', error => { record.error = error; }); processes.push(record);
  };
  const env = { ...process.env, SPATIAL_ALLOWED_ORIGINS: web, SPATIAL_API_URL: api, SPATIAL_LIVE_BINDINGS: '',
    LW_LIBRARY: info.outputPath('library'), SPATIAL_STATE_ROOT: info.outputPath('state'),
    SPATIAL_RECORDINGS_ROOT: info.outputPath('recordings'), SPATIAL_SCENE_PREVIEWS_ROOT: info.outputPath('previews') };
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (/huggingface\.co|hf\.co|cdn-lfs/.test(request.url())) remote.push(request.url()); });
  const v2 = `${api}/api/v1/library/scenes/cart-isolation/2`;
  let elapsed = 0;
  try {
    start('sf4-api', python, ['-m', 'uvicorn', 'sf4_gateway:app', '--app-dir', 'tests/fixtures',
      '--host', '127.0.0.1', '--port', String(apiPort)], resolve('../..'), env);
    start('sf4-web', process.execPath, [resolve('node_modules/vite/bin/vite.js'),
      ...(process.env.SPATIAL_E2E_WEB_MODE === 'preview' ? ['preview'] : []), '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], process.cwd(), env);
    await expect.poll(async () => {
      for (const process of processes) if (process.error || process.child.exitCode !== null) throw process.error ?? new Error(process.output);
      try { return (await http.get(`${api}/api/v1/health`, { timeout: 500 })).ok() && (await http.get(web, { timeout: 500 })).ok(); }
      catch { return false; }
    }, { timeout: 20_000 }).toBe(true);
    const started = Date.now();
    const roots = await (await http.get(`${api}/e2e/storage-roots`)).json();
    for (const path of Object.values(roots) as string[]) expect(resolve(path).startsWith(resolve(info.outputDir))).toBe(true);
    const scene = await (await http.get(v2)).json();
    expect(scene.launchable).toBe(false);
    expect(scene.validation.checks).toContainEqual(expect.objectContaining({ id: 'P3', status: 'fail' }));
    expect(scene.validation.checks[0].message).toContain('0.00 m');
    expect(scene.versions.map((version: { id: string }) => version.id)).toEqual(['cart-isolation@2']);

    const report = await http.post(`${api}/api/v1/library/authoring-reports`, { data: {
      submissionId: 'sub-cart-draft', source: 'claude-code/scene-director', sourceVersion: '1.4.0',
      reportedAt: '2026-10-24T12:00:00+00:00', draftId: 'blender-cart-draft',
      object: { kind: 'scene', id: 'cart-isolation@2', contentHash: scene.contentHash },
      summary: 'Blender 工作文件把台车移到 0.20 m；尚未导出或提交。',
      gaps: ['新包未导出', '未提交新的 Scene Version'],
      artifacts: [{ name: 'preview', reference: 'artifact://cart-draft/preview.png' }],
    } });
    expect(report.ok(), await report.text()).toBe(true);

    await page.goto(`${web}/scenes/cart-isolation/2`);
    await expect(page.locator('h1 code')).toHaveText('cart-isolation@2');
    const validation = page.getByRole('region', { name: '验证报告' });
    await expect(validation).toContainText('0.00 m');
    await expect(validation).toContainText('P3');
    const authoring = page.getByTestId('authoring-reports');
    await expect(authoring).toContainText('claude-code/scene-director');
    await expect(authoring).toContainText('0.20 m');
    await expect(authoring).toContainText('blender-cart-draft');
    await expect(authoring).toContainText('新包未导出');
    await expect(authoring).toContainText('外部汇报');
    await expect(page.getByTestId('scene-versions')).toContainText('cart-isolation@2');
    await expect(page.getByTestId('scene-versions')).not.toContainText('cart-isolation@3');
    await expect(page.getByTestId('scene-dependencies')).toBeVisible();

    await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
    await page.getByTestId('copy-failure-summary').click();
    await expect(page.getByTestId('copy-failure-summary')).toHaveText('已复制');
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    expect(copied).toContain('cart-isolation@2');
    expect(copied).toContain('P3');
    expect(copied).toContain('0.00 m');
    expect(copied).not.toContain('0.20 m');
    expect(await page.getByTestId('copy-failure-summary').textContent()).not.toContain('发送');

    const received = await http.post(`${api}/e2e/sf4/receive-v3`);
    expect(received.ok(), await received.text()).toBe(true);
    expect((await received.json()).sceneVersion).toBe('cart-isolation@3');

    await page.reload();
    await expect(page.getByTestId('scene-versions')).toContainText('cart-isolation@3');
    await expect(authoring).toContainText('0.20 m');
    await expect(validation).toContainText('0.00 m');

    await page.getByTestId('scene-versions').getByRole('link', { name: /cart-isolation@3/ }).click();
    await expect(page.locator('h1 code')).toHaveText('cart-isolation@3');
    await expect(page.getByTestId('authoring-empty')).toContainText('未收到更新');
    await expect(page.getByTestId('scene-versions')).toContainText('待验证');
    await expect(page.locator('.scene-validation')).toHaveCount(0);

    const revalidated = await http.post(`${api}/api/v1/library/scenes/cart-isolation/3/validate`, { timeout: 60_000 });
    expect(revalidated.ok(), await revalidated.text()).toBe(true);
    await page.reload();
    await expect(page.getByRole('region', { name: '验证报告' })).toContainText('结构检查');
    await expect(page.getByTestId('scene-versions')).not.toContainText('待验证');
    await expect(page.getByTestId('authoring-empty')).toContainText('未收到更新');

    await page.getByTestId('scene-versions').getByRole('link', { name: /cart-isolation@2/ }).click();
    await expect(page.locator('h1 code')).toHaveText('cart-isolation@2');
    await expect(page.getByTestId('authoring-reports')).toContainText('0.20 m');
    await expect(page.getByRole('region', { name: '验证报告' })).toContainText('0.00 m');
    const recordings = page.getByRole('region', { name: '相关录制' }).or(page.locator('.scene-related-recordings'));
    await expect(page.getByText('session-cart-v2')).toBeVisible();

    await page.setViewportSize({ width: 400, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(400);
    await page.setViewportSize({ width: 1440, height: 960 });
    elapsed = Date.now() - started;
    expect(elapsed).toBeLessThan(15_000);
    expect(remote).toEqual([]); expect(errors).toEqual([]);
    await writeFile(info.outputPath('scene-authoring-receipt.json'), JSON.stringify({
      scene: 'cart-isolation@2', reportId: 'sub-cart-draft', elapsedMs: elapsed,
      isolation: 'platform v2 keeps 0.00 m while the draft says 0.20 m',
      v3: 'received without validation, then validated through the real endpoint',
      remote, errors, recordings: recordings ? 'session-cart-v2' : undefined,
    }));
  } catch (error) { original.push(error); }
  finally {
    await cleanupOwnedProcesses(processes, { python, script: owned, original,
      outputPath: name => info.outputPath(name), context: { source: resolve('../..'), api, web }, ports: [apiPort, webPort], prepare: [
        { label: 'page:close', run: () => page.goto('about:blank') },
        { label: 'http:dispose', run: () => http.dispose() },
      ] }).then(cleanup => finishCleanup(cleanup, original));
  }
});
