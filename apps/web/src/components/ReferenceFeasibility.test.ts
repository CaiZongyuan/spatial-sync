import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import { ReferenceFeasibility } from './ReferenceFeasibility';

it('keeps measured zeros and distinguishes optional target provenance and speed limits', () => {
  const html = renderToStaticMarkup(createElement(ReferenceFeasibility, { report: {
    keypointErrorM: { head: 0, foot: 0.002 },
    keypointTargets: { head: { path: '/Asset/head', source: 'asset_nominal' } },
    maxRootSpeedMps: 0,
    maxRootAngularSpeedRadps: .25,
    maxJointSpeed: { '/Asset/joints/knee': .3 },
    bounds: { rootSpeedMps: .3, rootAngularSpeedRadps: 4, jointSpeedRadps: 2, slideSpeedMps: .1 },
  } }));
  expect(html).toContain('/Asset/head');
  expect(html).toContain('素材名义位姿');
  expect(html).toContain('来源未记录');
  expect(html).toContain('<td>0 m</td>');
  expect(html).toContain('0 m/s / 0.3 m/s');
  expect(html).toContain('0.25 rad/s / 4 rad/s');
  expect(html).toContain('2 rad/s');
  expect(html).toContain('0.1 m/s');
  expect(html).toContain('/Asset/joints/knee');
});
