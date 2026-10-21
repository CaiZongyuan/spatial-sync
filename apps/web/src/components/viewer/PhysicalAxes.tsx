import { useRef, type RefObject } from 'react';
import { useFrame } from '@react-three/fiber';
import { Matrix4, Vector3, type Group } from 'three';
import type { ViewerTheme } from '../../lib/scene/viewer-theme';

const axes = ['X', 'Y', 'Z'] as const;
const rotation = [0, 1, 2, 4, 5, 6, 8, 9, 10];
type ElementRef = RefObject<SVGSVGElement | null>;

export function PhysicalAxes({ element, theme }: { element: ElementRef; theme: ViewerTheme }) {
  return <div className="physical-axes-layer"><svg ref={element} className="physical-axes" viewBox="-40 -40 80 80" role="img" aria-label="Physical coordinate axes">
    {axes.map(axis => <g key={axis} data-axis={axis}>
      <line x1="0" y1="0" x2="0" y2="0" stroke={theme[`axis${axis}`]} strokeWidth="2" />
      <g className="physical-axis-head">
        <circle r="8" fill={theme[`axis${axis}`]} />
        <text textAnchor="middle" dominantBaseline="central" fill={theme.ground}>{axis}</text>
      </g>
    </g>)}
  </svg></div>;
}

export function PhysicalAxesProjection({ element, root }: { element: ElementRef; root: RefObject<Group | null> }) {
  const previous = useRef({ element: null as SVGSVGElement | null, root: new Matrix4(), camera: new Matrix4(), order: '' });
  const parts = useRef<{ group: SVGGElement; line: SVGLineElement; head: SVGGElement; direction: Vector3 }[]>([]);
  useFrame(({ camera }) => {
    const svg = element.current, physicsRoot = root.current;
    if (!svg || !physicsRoot) return;
    physicsRoot.updateWorldMatrix(true, false);
    camera.updateWorldMatrix(true, false);
    const view = camera.matrixWorldInverse;
    if (previous.current.element === svg && previous.current.root.equals(physicsRoot.matrixWorld)
      && rotation.every(index => previous.current.camera.elements[index] === view.elements[index])) return;
    if (previous.current.element !== svg) {
      parts.current = axes.map(axis => {
        const group = svg.querySelector<SVGGElement>(`[data-axis="${axis}"]`)!;
        return { group, line: group.querySelector('line')!, head: group.querySelector<SVGGElement>('.physical-axis-head')!, direction: new Vector3() };
      });
      previous.current.order = '';
    }
    parts.current.forEach(({ group, line, head, direction }, index) => {
      // Use the scene's actual physical basis, then the camera's rotation; no second Z-up conversion.
      direction.set(Number(index === 0), Number(index === 1), Number(index === 2))
        .transformDirection(physicsRoot.matrixWorld).transformDirection(view);
      line.setAttribute('x2', (24 * direction.x).toFixed(3));
      line.setAttribute('y2', (-24 * direction.y).toFixed(3));
      head.setAttribute('transform', `translate(${(32 * direction.x).toFixed(3)} ${(-32 * direction.y).toFixed(3)})`);
      group.dataset.depth = direction.z.toFixed(6);
    });
    const ordered = [...parts.current].sort((a, b) => a.direction.z - b.direction.z);
    const order = ordered.map(part => part.group.dataset.axis).join('');
    if (order !== previous.current.order) for (const part of ordered) svg.append(part.group);
    previous.current = { element: svg, root: previous.current.root.copy(physicsRoot.matrixWorld), camera: previous.current.camera.copy(view), order };
    svg.dataset.ready = 'true';
  });
  return null;
}
