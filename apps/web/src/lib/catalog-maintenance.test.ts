import { expect, it } from 'vitest';
import { maintenanceRunsAvailable, type MetadataState } from './catalog-maintenance';

const state = (capabilities?: MetadataState['capabilities']) => ({
  collections: [], runs: [], runtime: { configured: false }, capabilities,
});

it('hides maintenance runs when the lightweight host explicitly has none', () => {
  expect(maintenanceRunsAvailable(state({ maintenance: false, externalOrganization: false, localThumbnails: true, nativeQueries: true }))).toBe(false);
});

it('keeps the full maintenance panel for hosts that omit the capability flag', () => {
  expect(maintenanceRunsAvailable(state({ externalOrganization: true, localThumbnails: true, nativeQueries: true }))).toBe(true);
  expect(maintenanceRunsAvailable(state())).toBe(true);
  expect(maintenanceRunsAvailable(undefined)).toBe(true);
});
