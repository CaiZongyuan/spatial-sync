import { expect, it, vi } from 'vitest';
import {
  catalogEntryPath, catalogEstimatePath, catalogSearchPath, formatAge, formatBytes,
  semanticLabel, shortRevision, usageLabel, searchCatalogCards, type CatalogSemantic,
} from './catalog';

it('rejects a legacy backend instead of silently rendering a shortened card page', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
    schema: 'lw.catalog-search/1', results: Array.from({ length: 11 }),
  }), { headers: { 'Content-Type': 'application/json' } })));
  try {
    await expect(searchCatalogCards({ scope: 'online', page: 1, limit: 24 })).rejects.toThrow('重启后端服务');
  } finally { vi.unstubAllGlobals(); }
});

it('builds unified search URLs with explicit scope and no empty filters', () => {
  expect(catalogSearchPath({ q: '纸箱', scope: 'online' }))
    .toBe('/library/search?q=%E7%BA%B8%E7%AE%B1&scope=online');
  expect(catalogSearchPath({ scope: 'all', kind: 'environment', usage: 'staticCollision', approximate: true, limit: 20 }))
    .toBe('/library/search?scope=all&kind=environment&usage=staticCollision&approximate=true&limit=20');
  expect(catalogSearchPath({ q: '', scope: 'local', approximate: false }))
    .toBe('/library/search?scope=local');
  expect(catalogSearchPath({ scope: 'online', cursor: 'abc-_' }))
    .toBe('/library/search?scope=online&cursor=abc-_');
  const restored = new URL(catalogSearchPath({ scope: 'online', category: 'container', tag: 'labware',
    page: 3, limit: 24, sort: 'name', release: 'sha256:fixture' }), 'http://localhost');
  expect(Object.fromEntries(restored.searchParams)).toEqual({ scope: 'online', category: 'container',
    tag: 'labware', page: '3', limit: '24', sort: 'name', release: 'sha256:fixture' });
});

it('addresses bounded catalog detail and estimate documents by fixed identity', () => {
  const revision = '524747d9b5e1f21b5b18b56041788c01d745f1a1';
  expect(catalogEntryPath('simready', 'box_cardboard_a01', revision, 'full'))
    .toBe(`/library/catalog/entries/simready/box_cardboard_a01/${revision}?view=full`);
  expect(catalogEntryPath('simready', 'box_cardboard_a01', revision))
    .toBe(`/library/catalog/entries/simready/box_cardboard_a01/${revision}?view=summary`);
  expect(catalogEstimatePath('simready', 'box/a01', revision))
    .toBe(`/library/catalog/entries/simready/box%2Fa01/${revision}/estimate`);
});

it('keeps unknown usage and byte values explicit', () => {
  expect(usageLabel(true)).toBe('可用');
  expect(usageLabel(false)).toBe('不可用');
  expect(usageLabel(null)).toBe('未知');
  expect(usageLabel(undefined)).toBe('未知');
  expect(formatBytes(null)).toBe('未知');
  expect(formatBytes(0)).toBe('0 B');
  expect(formatBytes(1023)).toBe('1023 B');
  expect(formatBytes(1024)).toBe('1.0 KiB');
  expect(formatBytes(21_530_693)).toBe('20.5 MiB');
  expect(formatBytes(297_991_931)).toBe('284 MiB');
  expect(formatBytes(536_870_912)).toBe('512 MiB');
  expect(formatBytes(2_147_483_648)).toBe('2.0 GiB');
});

it('labels snapshot freshness without inventing a value', () => {
  expect(formatAge(null)).toBe('新鲜度未知');
  expect(formatAge(12)).toBe('12 秒前');
  expect(formatAge(120)).toBe('2 分钟前');
  expect(formatAge(7200)).toBe('2 小时前');
  expect(formatAge(172800)).toBe('2 天前');
  expect(shortRevision(null)).toBe('未知');
  expect(shortRevision('abcdef0123456789')).toBe('abcdef012345…');
});

it('labels the deterministic semantic stand-in and never calls it real', () => {
  const base: CatalogSemantic = {
    schema: 'lw.catalog-semantic/1', enabled: true, applied: true, reason: 'semantic_recall',
    model: {
      id: 'lw-hash-concept-standin', version: '1', kind: 'deterministic-stand-in',
      real: false, runtime: 'numpy-hash', dimension: 512,
    },
    index: { version: 'sha256:' + 'a'.repeat(64), entryCount: 105 },
    threshold: 0.18, hits: 3, notice: 'stand-in',
  };
  expect(semanticLabel(base)).toContain('CI 替身（非真实模型）');
  const real: CatalogSemantic = {
    ...base,
    model: { ...base.model!, id: 'fastembed:demo', kind: 'real-cpu-multilingual', real: true },
  };
  expect(semanticLabel(real)).toContain('语义检索 fastembed:demo@1');
  expect(semanticLabel(undefined)).toBe('语义检索未启用');
});
