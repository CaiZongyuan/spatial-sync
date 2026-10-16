import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { tanstackRouter } from '@tanstack/router-plugin/vite';

export default defineConfig({
  plugins: [
    tanstackRouter({ target: 'react', autoCodeSplitting: true }),
    react(),
  ],
  server: {
    strictPort: true,
    proxy: {
      '/api': {
        target: process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000',
        ws: true,
      },
    },
    fs: { allow: ['../..'] },
  },
  test: { include: ['src/**/*.test.ts'], environment: 'node' },
});
