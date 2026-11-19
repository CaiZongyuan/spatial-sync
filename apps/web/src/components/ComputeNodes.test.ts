import { createElement, type ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { TrainingNode } from '../lib/jobs';
import { ComputeNodes } from './ComputeNodes';

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: ReactNode }) => createElement('a', null, children),
}));

describe('ComputeNodes', () => {
  it.each([false, true])('shows GPU quarantine after a node disappears or restarts (compact=%s)', compact => {
    const node = {
      id: 'worker', instanceId: null, hardware: null, state: 'offline', cpuAvailable: false,
      gpus: [], leases: {}, profiles: {}, queue: [],
      quarantinedGpuCount: 1,
      quarantinedGpus: [{
        nodeId: 'worker', instanceId: 'old', jobId: 'job-original', leaseId: '1',
        gpuId: 'GPU-original', colocated: true, releaseAt: null,
      }],
    } satisfies TrainingNode;
    const html = renderToStaticMarkup(createElement(ComputeNodes, { nodes: [node], jobs: [], compact }));
    expect(html).toContain('GPU-original');
    expect(html).toContain('job-original');
    expect(html).toContain('释放时间未知');
    expect(html).not.toContain('当前没有作业持有资源租约');
  });
});
