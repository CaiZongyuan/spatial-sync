import { useRef } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import { Box3, Vector3 } from 'three';
import { placeLabels, type PlacedLabel } from '../../lib/scene/label-layout';

export interface DeviceAnnotation { key: string; label: string; detail: string; bodyKeys: string[] }

export function DeviceAnnotations({ annotations, layout, boundsForKeys }: {
  annotations: DeviceAnnotation[]; layout: (labels: PlacedLabel[]) => void; boundsForKeys: (keys: string[]) => Box3;
}) {
  const { size } = useThree();
  const previous = useRef('');
  const elapsed = useRef(0);
  useFrame((state, delta) => {
    elapsed.current += delta;
    if (previous.current && elapsed.current < .1) return;
    elapsed.current = 0;
    const root = state.scene.getObjectByName('physicsRoot');
    if (!root) return;
    root.updateWorldMatrix(true, true);
    const anchors = annotations.flatMap(annotation => {
      const bounds = boundsForKeys(annotation.bodyKeys);
      if (bounds.isEmpty()) return [];
      const point = bounds.getCenter(new Vector3());
      point.y = bounds.max.y;
      point.project(state.camera);
      if (point.z < -1 || point.z > 1) return [];
      return [{ key: annotation.key, x: (point.x + 1) * size.width / 2, y: (1 - point.y) * size.height / 2,
        width: Math.min(250, 24 + (annotation.label.length + annotation.detail.length) * 7), height: 28 }];
    });
    const next = placeLabels(anchors, size.width, size.height);
    const signature = JSON.stringify(next.map(label => [label.key, Math.round(label.x), Math.round(label.y), label.width]));
    if (signature !== previous.current) { previous.current = signature; layout(next); }
  });
  return null;
}

export function DeviceAnnotationLayer({ labels, annotations, selected, select }: {
  labels: PlacedLabel[]; annotations: DeviceAnnotation[]; selected?: string; select: (key: string) => void;
}) {
  return <div className="device-annotations">
      <svg width="100%" height="100%" aria-hidden="true">{labels.map(label => <g key={label.key}>
        <path d={`M${label.x + label.width / 2},${label.y + label.height} L${label.anchorX},${label.anchorY}`} />
        <circle cx={label.anchorX} cy={label.anchorY} r="3" />
      </g>)}</svg>
      {labels.map(label => {
        const annotation = annotations.find(annotation => annotation.key === label.key);
        if (!annotation) return null;
        return <button key={label.key} className={`device-annotation ${selected === label.key ? 'is-selected' : ''}`}
          data-testid={`device-label-${label.key}`} aria-pressed={selected === label.key}
          aria-label={annotation.label} onClick={() => select(label.key)}
          style={{ left: label.x, top: label.y, width: label.width, height: label.height }}>
          <strong>{annotation.label}</strong>{annotation.detail && <span>{annotation.detail}</span>}
        </button>;
      })}
    </div>;
}
