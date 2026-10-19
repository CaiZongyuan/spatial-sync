export async function request<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body?.detail;
    const structuredDetail =
      detail !== null && typeof detail === 'object' ? detail : undefined;
    throw new Error(
      typeof detail === 'string'
        ? detail
        : (structuredDetail?.message ??
          structuredDetail?.code ??
          body?.message ??
          body?.error?.message ??
          `Request failed (${response.status})`),
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

// Catalog/lifecycle HTTP metadata is separate from the versioned spatial stream.
export interface SceneSummary {
  sceneId: string;
  name: string;
  sources: ('synthetic' | 'newton')[];
  description?: string;
  sceneVersion?: string;
  rev?: number;
  launchable?: boolean;
}
export interface SessionSummary {
  sessionId: string;
  sceneId: string;
  sceneVersion?: string;
  sceneContentHash?: string | null;
  source: string | { kind: string };
  state: string;
  sceneRevision: string;
  epoch: number;
  seq: number;
  simTime: number;
  actualRtf?: number;
}

// The temporary legacy form launches a bare name; explicit version selection belongs to the library UI.
export const getScenes = async () => {
  const { scenes } = await request<{ scenes: SceneSummary[] }>('/scenes');
  const choices = new Map<string, SceneSummary>();
  for (const scene of scenes) {
    if (scene.launchable === false) continue;
    const previous = choices.get(scene.sceneId);
    if (!previous || (scene.rev ?? 0) > (previous.rev ?? 0)) choices.set(scene.sceneId, scene);
  }
  return { scenes: [...choices.values()] };
};
export const createSession = (
  sceneId: string,
  source: string,
  continueWithoutObservers = true,
) =>
  request<SessionSummary>('/sessions', {
    method: 'POST',
    body: JSON.stringify({
      sceneId,
      source,
      realtimeFactor: 1,
      continueWithoutObservers,
    }),
  });
