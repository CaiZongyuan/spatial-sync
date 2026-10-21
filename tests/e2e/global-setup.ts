import type { FullConfig } from '@playwright/test';
import { isAbsolute, relative, resolve, sep } from 'node:path';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';

// Browser cases import packages and write approvals and recordings through the shared API.
// Refuse one whose actual roots are outside this checkout's ignored .scratch directory.
export default async function requireOwnedRoots(config: FullConfig) {
  const scratch = resolve(config.rootDir, '../../.scratch');
  const response = await fetch(`${api}/e2e/storage-roots`, { signal: AbortSignal.timeout(5000) }).catch((error: Error) => {
    throw new Error(`E2E API ${api} is unreachable: ${error.cause ?? error.message}`);
  });
  if (!response.ok)
    throw new Error(`E2E needs the approval_gateway test API at ${api} (docs/VERIFICATION.md); /e2e/storage-roots returned HTTP ${response.status}`);
  const roots: Record<string, string> = await response.json();
  const shared = Object.entries(roots).filter(([, root]) => {
    const path = relative(scratch, root);
    return path === '..' || path.startsWith(`..${sep}`) || isAbsolute(path);
  });
  if (shared.length)
    throw new Error(`E2E API ${api} must write only under ${scratch}: ${shared.map(([name, root]) => `${name}=${root}`).join(', ')}`);
}
