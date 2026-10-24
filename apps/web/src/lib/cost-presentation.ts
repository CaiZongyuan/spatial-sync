export function knownSegments(values: readonly (number | null | undefined)[]) {
  const segments: { index: number; value: number }[][] = [];
  let current: { index: number; value: number }[] = [];
  values.forEach((value, index) => {
    if (value == null || !Number.isFinite(value)) { current = []; return; }
    if (!current.length) segments.push(current);
    current.push({ index, value });
  });
  return segments;
}

export function costExtrema(values: readonly (number | null | undefined)[]) {
  const known = values.filter((value): value is number => value != null && Number.isFinite(value));
  return { peak: values.length && known.length === values.length ? Math.max(...known) : null,
    last: values.length && Number.isFinite(values.at(-1)) ? values.at(-1)! : null };
}
export const ratioLabel = (value: number | null | undefined) => value == null || !Number.isFinite(value)
  ? '未知' : `${(value * 100).toLocaleString('zh-CN', { maximumFractionDigits: 1 })}%`;
