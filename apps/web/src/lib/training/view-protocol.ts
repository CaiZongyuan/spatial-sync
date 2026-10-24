import Ajv from 'ajv';
import spatialSchema from '../../../../../packages/protocol/types/protocol.schema.json';
import type { SceneManifest, SourceInfo, TransformFrame } from '../protocol';
import type { JobStatus } from '../jobs';
import { validateManifest } from '../validate-manifest';

export const TRAINING_VIEW_SCHEMA = 'lw.training-view/1';
export const trainingStreamIdentity = (viewId: string, envIndex: number) => `training-view:${viewId}:env:${envIndex}`;
export interface TrainingHelloPayload {
  task: string; taskContentHash: string; scene: string; sceneContentHash: string;
  assets: { id: string; contentHash: string }[];
  descriptorDigest: string; mappingDigest: string; environments: number[];
  source: SourceInfo; maxRateHz: number; capture?: { source: SourceInfo } | null;
}
interface Envelope { schema: typeof TRAINING_VIEW_SCHEMA; jobId: string; attemptId: string | null; viewId: string | null }
interface ProducerEnvelope extends Envelope { attemptId: string; viewId: string }
export type TrainingViewMessage =
  | (ProducerEnvelope & { type: 'hello'; payload: TrainingHelloPayload })
  | (ProducerEnvelope & { type: 'manifest'; envIndex: number; payload: SceneManifest })
  | (ProducerEnvelope & { type: 'frame'; envIndex: number; payload: TransformFrame })
  | (Envelope & { type: 'status'; payload: { state: 'waiting' | 'live' | 'stale'; message?: string | null } })
  | (Envelope & { type: 'error'; envIndex?: number | null; payload: { code: string; message: string } })
  | (Envelope & { type: 'ended'; payload: { status: JobStatus; reason?: string | null } });

const ajv = new Ajv({ strict: false });
ajv.addSchema(spatialSchema);
const sourceSchema = ajv.compile<SourceInfo>({ $ref: `${spatialSchema.$id}#/$defs/SourceInfo` });
const frameSchema = ajv.compile<TransformFrame>({ $ref: `${spatialSchema.$id}#/$defs/TransformFrame` });
const text = (value: unknown): value is string => typeof value === 'string' && value.length <= 4000 && value.trim().length > 0;
const digest = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const environment = (value: unknown): value is number => Number.isInteger(value) && Number(value) >= 0 && Number(value) <= 3;
function ensure(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`训练视图消息无效：${message}`);
}
function object(value: unknown): Record<string, unknown> {
  ensure(value != null && typeof value === 'object' && !Array.isArray(value), '需要对象');
  return value as Record<string, unknown>;
}
function fields(value: Record<string, unknown>, allowed: string[], required: string[]) {
  ensure(Object.keys(value).every(key => allowed.includes(key)) && required.every(key => Object.hasOwn(value, key)), '字段不匹配');
}
function source(value: unknown): SourceInfo {
  if (!sourceSchema(value)) throw new Error(`训练视图 Source 无效：${ajv.errorsText(sourceSchema.errors)}`);
  return value;
}
function hello(value: unknown): TrainingHelloPayload {
  const data = object(value);
  const required = ['task', 'taskContentHash', 'scene', 'sceneContentHash', 'assets', 'descriptorDigest', 'mappingDigest', 'environments', 'source', 'maxRateHz'];
  fields(data, [...required, 'capture'], required);
  ensure(text(data.task) && text(data.scene), 'Task 或场景身份缺失');
  ensure(['taskContentHash', 'sceneContentHash', 'descriptorDigest', 'mappingDigest'].every(key => digest(data[key])), '内容摘要无效');
  ensure(Array.isArray(data.environments) && data.environments.length > 0 && data.environments.length <= 4
    && data.environments.every(environment) && new Set(data.environments).size === data.environments.length, '环境身份无效');
  ensure(typeof data.maxRateHz === 'number' && Number.isFinite(data.maxRateHz) && data.maxRateHz > 0 && data.maxRateHz <= 10, '发布频率超出范围');
  ensure(Array.isArray(data.assets), '素材身份缺失');
  const ids = new Set<string>();
  for (const item of data.assets) {
    const asset = object(item); fields(asset, ['id', 'contentHash'], ['id', 'contentHash']);
    ensure(text(asset.id) && digest(asset.contentHash) && !ids.has(asset.id), '素材身份无效或重复'); ids.add(asset.id);
  }
  const producer = source(data.source);
  ensure(producer.kind === 'newton' && text(producer.newtonVersion) && text(producer.solver)
    && producer.basePublishHz === data.maxRateHz, 'Source 必须描述实际 Newton 发布者');
  if (data.capture != null) {
    const capture = object(data.capture); fields(capture, ['source'], ['source']); source(capture.source);
  }
  return data as unknown as TrainingHelloPayload;
}

/** Validate the new transport; inner spatial schemas and numeric conventions stay unchanged. */
export function parseTrainingViewMessage(input: unknown, jobId: string): TrainingViewMessage {
  const data = object(typeof input === 'string' ? JSON.parse(input) : input);
  ensure(data.schema === TRAINING_VIEW_SCHEMA && data.jobId === jobId, '版本或作业身份不匹配');
  const common = ['schema', 'type', 'jobId', 'attemptId', 'viewId', 'payload'];
  const required = ['schema', 'type', 'jobId', 'payload'];
  const producer = data.type === 'hello' || data.type === 'manifest' || data.type === 'frame';
  fields(data, [...common, ...(data.type === 'manifest' || data.type === 'frame' || data.type === 'error' ? ['envIndex'] : [])], required);
  ensure(data.attemptId == null || text(data.attemptId), 'attemptId 无效');
  ensure(data.viewId == null || text(data.viewId), 'viewId 无效');
  const base: Envelope = { schema: TRAINING_VIEW_SCHEMA, jobId,
    attemptId: data.attemptId as string | null ?? null, viewId: data.viewId as string | null ?? null };
  if (producer) ensure(text(base.attemptId) && text(base.viewId), '发布者身份缺失');
  if (data.type === 'hello') return { ...base, attemptId: base.attemptId!, viewId: base.viewId!, type: 'hello', payload: hello(data.payload) };
  if (data.type === 'manifest' || data.type === 'frame') {
    ensure(environment(data.envIndex), 'envIndex 无效');
    const identity = trainingStreamIdentity(base.viewId!, data.envIndex);
    if (data.type === 'manifest') {
      const manifest = validateManifest(data.payload);
      ensure(manifest.sessionId === identity, 'Manifest 空间身份不匹配');
      return { ...base, attemptId: base.attemptId!, viewId: base.viewId!, type: 'manifest', envIndex: data.envIndex, payload: manifest };
    }
    if (!frameSchema(data.payload)) throw new Error(`训练空间帧无效：${ajv.errorsText(frameSchema.errors)}`);
    const frame = data.payload;
    ensure(frame.sessionId === identity && Number.isSafeInteger(frame.seq), '空间帧身份或序号无效');
    const ids = new Set<number>();
    for (const row of frame.poses) {
      ensure(!ids.has(row[0]) && row.slice(1).every(Number.isFinite)
        && Math.abs(row.slice(4).reduce((sum, value) => sum + value * value, 0) - 1) <= 1e-4, '位姿必须有限、单位四元数且身份不重复');
      ids.add(row[0]);
    }
    return { ...base, attemptId: base.attemptId!, viewId: base.viewId!, type: 'frame', envIndex: data.envIndex, payload: frame };
  }
  const payload = object(data.payload);
  if (data.type === 'status') {
    fields(payload, ['state', 'message'], ['state']);
    ensure(['waiting', 'live', 'stale'].includes(String(payload.state)) && (payload.message == null || text(payload.message)), '状态无效');
    return { ...base, type: 'status', payload: payload as unknown as Extract<TrainingViewMessage, { type: 'status' }>['payload'] };
  }
  if (data.type === 'error') {
    fields(payload, ['code', 'message'], ['code', 'message']);
    ensure(text(payload.code) && text(payload.message) && (data.envIndex == null || environment(data.envIndex)), '错误消息无效');
    return { ...base, type: 'error', envIndex: data.envIndex as number | null | undefined, payload: { code: payload.code, message: payload.message } };
  }
  ensure(data.type === 'ended', '未知消息类型');
  fields(payload, ['status', 'reason'], ['status']);
  ensure(['queued', 'running', 'succeeded', 'failed', 'cancelled'].includes(String(payload.status))
    && (payload.reason == null || text(payload.reason)), '结束状态无效');
  return { ...base, type: 'ended', payload: payload as unknown as Extract<TrainingViewMessage, { type: 'ended' }>['payload'] };
}
