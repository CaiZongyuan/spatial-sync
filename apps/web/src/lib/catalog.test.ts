import { expect, it } from 'vitest';
import {
  catalogEntryPath, catalogEstimatePath, catalogSearchPath, formatAge, formatBytes,
  shortRevision, usageLabel,
} from './catalog';

it('builds unified search URLs with explicit scope and no empty filters', () => {
  expect(catalogSearchPath({ q: '纸箱', scope: 'online' }))
    .toBe('/library/search?q=%E7%BA%B8%E7%AE%B1&scope=online');
  expect(catalogSearchPath({ scope: 'all', kind: 'environment', usage: 'staticCollision', approximate: true, limit: 20 }))
    .toBe('/library/search?scope=all&kind=environment&usage=staticCollision&approximate=true&limit=20');
  expect(catalogSearchPath({ q: '', scope: 'local', approximate: false }))
    .toBe('/library/search?scope=local');
  expect(catalogSearchPath({ scope: 'online', cursor: 'abc-_' }))
    .toBe('/library/search?scope=online&cursor=abc-_');
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
