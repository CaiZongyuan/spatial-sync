export interface LlmModel {
  ref: string;
  provider: string;
  modelId: string;
  defaultThinking: string;
  credentialName: string | null;
  isDefault: boolean;
  health: 'unknown';
  healthReason: string;
  latencyP50Ms: number | null;
  users: string[] | null;
}
export interface ModelPrice {
  provider: string;
  modelId: string;
  inputUsdPerMillion: number | null;
  outputUsdPerMillion: number | null;
  cacheReadUsdPerMillion: number | null;
}
export interface GpuPrice { nodeId: string; usdPerHour: number | null }
export interface PriceAudit { revision: number; at: string; actor: { kind: string; name: string } }
export interface PriceTable {
  version: 1;
  revision: number;
  models: ModelPrice[];
  gpus: GpuPrice[];
  audit: PriceAudit[];
  canEdit: boolean;
}
export const priceFields = ['inputUsdPerMillion', 'outputUsdPerMillion', 'cacheReadUsdPerMillion'] as const;
export const priceLabels = ['输入', '输出', '缓存读'] as const;
export function dollars(value: number | null | undefined) {
  return value == null ? '*' : `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 6 })}`;
}
