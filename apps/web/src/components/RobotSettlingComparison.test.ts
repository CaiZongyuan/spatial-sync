import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { ValidationReport } from '../lib/library';
import { RobotSettlingComparison } from './RobotSettlingComparison';

const asset = { kind: 'robot', validationStatus: 'stale', files: { 'source/robot.json': '/indexed' } } as const;
const report: ValidationReport = { subject: 'asset:robot/browser-archive-probe@1', launchable: false, checks: [] };

describe('robot settling readback', () => {
  it('preserves measured zero, marked report age, and per-engine joint details', () => {
    const validation: ValidationReport = { ...report, checks: [{
      id: 'P8', tier: 'physics', status: 'warn', message: 'Solver comparison',
      measurements: {
        durationSeconds: 2, physicsDt: 0.002,
        newton: { baseHeightM: 0.3, baseTiltDeg: 0, maxSelfPenetrationM: 0, jointLimitViolations: {} },
        mujoco: { baseHeightM: 0.31, baseTiltDeg: 2, maxSelfPenetrationM: 0.001,
          jointLimitViolations: { '/Asset/joints/knee': { excess: 0.05, unit: 'rad' } } },
      },
    }] };
    const html = renderToStaticMarkup(createElement(RobotSettlingComparison, { asset: { ...asset, validation } }));
    expect(html).toContain('报告已过期');
    expect(html).toContain('Newton');
    expect(html).toContain('MuJoCo');
    expect(html).toContain('<td>0</td><td>2</td>');
    expect(html).toContain('/Asset/joints/knee');
    expect(html).toContain('0.05 rad');
  });

  it('shows the skip reason without a result table when the runtime never measured P8', () => {
    const validation: ValidationReport = { ...report, physics: { status: 'skipped', reason: 'Newton unavailable' } };
    const html = renderToStaticMarkup(createElement(RobotSettlingComparison, { asset: { ...asset, validation } }));
    expect(html).toContain('物理检查已跳过');
    expect(html).toContain('Newton unavailable');
    expect(html).not.toContain('<table>');
  });
});
import { createElement } from 'react';
