import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '../../tests/e2e',
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [
    ['list'],
    [
      'html',
      { outputFolder: '../../.scratch/viewer-e2e/report', open: 'never' },
    ],
  ],
  outputDir: '../../.scratch/viewer-e2e/results',
  use: {
    baseURL: process.env.SPATIAL_WEB_URL ?? 'http://127.0.0.1:5184',
    viewport: { width: 1440, height: 960 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: process.env.SPATIAL_E2E_VIDEO === '1' ? 'on' : 'off',
    launchOptions: {
      args: ['--enable-webgl', '--use-gl=angle', '--use-angle=swiftshader'],
    },
  },
  webServer: process.env.SPATIAL_WEB_URL
    ? undefined
    : {
        command: 'pnpm exec vite --host 127.0.0.1 --port 5184',
        url: 'http://127.0.0.1:5184',
        reuseExistingServer: false,
        timeout: 30_000,
        env: {
          SPATIAL_API_URL:
            process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000',
        },
      },
});
