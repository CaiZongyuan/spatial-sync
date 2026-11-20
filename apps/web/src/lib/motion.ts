import { request } from './api';
import type { Point } from './motion-phase';

export interface MotionPin { id: string; sha256: string }
interface VideoRights { license: string; attribution: string; sourceUrl?: string }
export interface MotionVideo extends MotionPin { license: VideoRights; durationSeconds: number }
interface PoseModel { sha256: string; runtime: string; landmarkLicense: string; detectorLicense: string | null }
export interface MotionClip extends MotionPin {
  source: MotionVideo;
  model: PoseModel;
  diagnostics: { visibleFrames: number; missingFrames: number; noPersonFrames: number };
}
export interface ReferencePin extends MotionPin { asset: string; assetContentHash: string }
export interface ReferenceDetail extends MotionPin {
  motion: { asset: string; assetContentHash: string };
  source: { clip?: MotionPin; video?: MotionVideo; model?: PoseModel; keyframes?: { license: VideoRights } };
  report: { maxFootSlipM?: number; minSupportMarginM?: number; jointLimitSaturation?: number };
}
export interface GhostPreview extends ReferencePin {
  loop: boolean;
  ghost: { links: [number, number][]; samples: { time: number; positions: Point[] }[] };
}

export function motionVersionPath(id: string, kind: 'motion-clip' | 'reference-motion') {
  const prefix = `${kind}/`;
  const match = /^([0-9a-f]{64})@([1-9][0-9]*)$/.exec(id.slice(prefix.length));
  if (!id.startsWith(prefix) || !match) throw new Error('需要精确的动作版本。');
  return `/library/${kind}s/${match[1]}/${match[2]}`;
}

export const motionWrite = <T>(path: string, body: object) => request<T>(path, {
  method: 'POST', headers: { 'X-Actor': 'user:web' }, body: JSON.stringify(body),
});

export function uploadMotionVideo(file: File, rights: VideoRights) {
  const query = new URLSearchParams({ license: rights.license, attribution: rights.attribution });
  if (rights.sourceUrl) query.set('sourceUrl', rights.sourceUrl);
  return request<MotionVideo>(`/library/motion-videos?${query}`, {
    method: 'POST', headers: { 'Content-Type': 'application/octet-stream', 'X-Actor': 'user:web' }, body: file,
  });
}

export function referenceMatches(pin: ReferencePin, detail: ReferenceDetail, preview: GhostPreview) {
  return detail.id === pin.id && preview.id === pin.id && detail.sha256 === pin.sha256
    && preview.sha256 === pin.sha256 && detail.motion.asset === pin.asset && preview.asset === pin.asset
    && detail.motion.assetContentHash === pin.assetContentHash && preview.assetContentHash === pin.assetContentHash;
}
