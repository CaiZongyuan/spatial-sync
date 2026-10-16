import { appendFileSync, existsSync, mkdirSync, realpathSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { randomUUID } from 'node:crypto';

const kinds = new Set(['phase', 'command', 'wait', 'point']);
const categories = new Set(['implementation', 'review', 'validation', 'diagnosis', 'integration', 'coordination']);
const outcomes = new Set(['passed', 'failed', 'expected-red', 'skipped', 'unknown']);
const confidences = new Set(['verified', 'inferred', 'unknown']);

export function parseArguments(args) {
  const options = { evidence: [] };
  const allowed = new Set(['journal', 'id', 'lane', 'kind', 'category', 'label', 'at', 'outcome', 'confidence', 'actor', 'revision', 'detail', 'reason', 'evidence']);
  for (let index = 0; index < args.length; index += 1) {
    const part = args[index];
    if (['start', 'end', 'point'].includes(part)) {
      if (options.type) throw new Error('Use one start, end or point action');
      options.type = part;
      continue;
    }
    if (!part.startsWith('--') || !allowed.has(part.slice(2))) throw new Error(`Unknown argument: ${part}`);
    const key = part.slice(2);
    const value = args[++index];
    if (!value || value.startsWith('--')) throw new Error(`Value required for --${key}`);
    if (key === 'evidence') options.evidence.push({ href: value, label: value });
    else if (Object.hasOwn(options, key)) throw new Error(`Duplicate --${key}`);
    else options[key] = value;
  }
  return options;
}

export function makeRecord(options, now = new Date().toISOString()) {
  if (!['start', 'end', 'point'].includes(options.type)) throw new Error('start, end or point action required');
  const at = options.at ?? now;
  if (!/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(at) || !Number.isFinite(Date.parse(at))) throw new Error('Timestamp must be ISO with a timezone');
  const outcome = options.outcome ?? 'unknown';
  const confidence = options.confidence ?? 'verified';
  if (!outcomes.has(outcome) || !confidences.has(confidence)) throw new Error('Unknown outcome or confidence');
  const detail = Object.fromEntries(['actor', 'revision', 'detail', 'reason'].filter(key => options[key]).map(key => [key, options[key]]));
  const evidence = options.evidence ?? [];
  if (options.type === 'end') {
    if (!options.id) throw new Error('End requires the original --id');
    return { type: 'end', at, eventId: options.id, outcome,
      ...(options.confidence == null ? {} : { confidence }), ...detail, evidence };
  }
  if (!options.lane || !options.label) throw new Error('Start/point requires --lane and --label');
  const kind = options.type === 'point' ? 'point' : options.kind ?? 'phase';
  const category = options.category ?? 'coordination';
  if (!kinds.has(kind) || !categories.has(category)) throw new Error('Unknown kind or category');
  if (options.type === 'start' && kind === 'point') throw new Error('Use point action for a point event');
  return {
    type: options.type, at,
    event: {
      id: options.id ?? randomUUID(), lane: options.lane, kind, category,
      label: options.label, start: at, outcome, confidence, ...detail, evidence,
    },
  };
}

export function appendRecord(journal, record) {
  const target = resolve(journal);
  mkdirSync(dirname(target), { recursive: true });
  appendFileSync(target, JSON.stringify(record) + '\n', 'utf8');
  return record.event?.id ?? record.eventId;
}

export function main(args) {
  if (args.includes('--help')) {
    console.log('record-event.mjs --journal FILE start|end|point [--id ID] [--lane NAME --label TEXT] [--kind phase|command|wait] [--category NAME] [--at ISO] [--outcome NAME] [--evidence PATH]');
    return;
  }
  const options = parseArguments(args);
  if (!options.journal) throw new Error('--journal required');
  const record = makeRecord(options);
  console.log(appendRecord(options.journal, record));
}

if (process.argv[1] && existsSync(process.argv[1]) && import.meta.url === pathToFileURL(realpathSync(resolve(process.argv[1]))).href) {
  try { main(process.argv.slice(2)); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
