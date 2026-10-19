import type { SceneVersion } from '../lib/library';

export function LaunchStatus({ scene }: { scene: SceneVersion }) {
  return <span className={`scene-status ${scene.launchable ? 'is-ready' : 'is-blocked'}`}><i />{scene.launchable ? '可启动' : '不可启动'}</span>;
}
