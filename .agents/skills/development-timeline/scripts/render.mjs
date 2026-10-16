#!/usr/bin/env node
import { readFile, writeFile, mkdir, stat } from 'node:fs/promises';
import { realpathSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const templatePath = fileURLToPath(new URL('../assets/report.html', import.meta.url));
const taskStatuses = new Set(['in-progress', 'completed', 'partial', 'blocked', 'paused']);
const ticketStatuses = new Set(['integrated', 'in-progress', 'ready', 'blocked', 'not-started']);
const kinds = new Set(['phase', 'command', 'wait', 'point']);
const categories = new Set(['implementation', 'review', 'validation', 'diagnosis', 'integration', 'coordination']);
const outcomes = new Set(['passed', 'failed', 'expected-red', 'skipped', 'unknown']);
const confidence = new Set(['verified', 'inferred', 'unknown']);

function record(value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${label} must be an object`);
  return value;
}

function timestamp(value, label, optional = false) {
  if (optional && value == null) return undefined;
  if (typeof value !== 'string' || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) {
    throw new Error(`${label} must be an ISO timestamp with a timezone`);
  }
  return new Date(value).toISOString();
}

function choice(value, allowed, label) {
  if (!allowed.has(value)) throw new Error(`${label} must be one of ${[...allowed].join(', ')}`);
  return value;
}

function text(value, fallback = '') {
  return value == null ? fallback : String(value);
}

function list(value, label) {
  if (value == null) return [];
  if (!Array.isArray(value)) throw new Error(`${label} must be an array`);
  return value;
}

export function rebaseHref(value, inputPath, outputPath) {
  if (typeof value !== 'string') return undefined;
  const href = value.trim();
  if (!href || /[\u0000-\u001f\u007f]/.test(href) || /^(?:\/\/|\\\\)/.test(href)) return undefined;
  if (href.startsWith('#')) return href;
  const scheme = href.match(/^([^/?#]*):/);
  if (scheme) {
    if (!['http', 'https', 'mailto'].includes(scheme[1].toLowerCase())) return undefined;
    try {
      const url = new URL(href);
      return ['http:', 'https:', 'mailto:'].includes(url.protocol) ? url.href : undefined;
    } catch {
      return undefined;
    }
  }
  const suffixAt = href.search(/[?#]/);
  const pathname = suffixAt < 0 ? href : href.slice(0, suffixAt);
  const suffix = suffixAt < 0 ? '' : href.slice(suffixAt);
  if (!pathname) return suffix;
  let decoded;
  try { decoded = decodeURIComponent(pathname); } catch { return undefined; }
  const target = isAbsolute(decoded) ? decoded : resolve(dirname(inputPath), decoded);
  const rebased = relative(dirname(outputPath), target).split(sep).map(encodeURIComponent).join('/');
  return (rebased.startsWith('.') ? rebased : `./${rebased}`) + suffix;
}

function evidence(items, inputPath, outputPath) {
  return list(items, 'evidence').map((item, i) => {
    record(item, `evidence[${i}]`);
    const href = rebaseHref(item.href, inputPath, outputPath);
    return { label: text(item.label, text(item.href, 'Evidence')), ...(href ? { href } : {}) };
  });
}

function normalizeEvent(item, index, inputPath, outputPath) {
  record(item, `events[${index}]`);
  const start = timestamp(item.start, `events[${index}].start`);
  const end = timestamp(item.end, `events[${index}].end`, true);
  if (end && Date.parse(end) < Date.parse(start)) throw new Error(`events[${index}].end precedes start`);
  return {
    id: text(item.id, `event-${index + 1}`), lane: text(item.lane, 'Uncategorized'),
    kind: choice(item.kind, kinds, `events[${index}].kind`),
    category: choice(item.category, categories, `events[${index}].category`),
    label: text(item.label, 'Unnamed event'), start, ...(end ? { end } : {}),
    outcome: choice(item.outcome ?? 'unknown', outcomes, `events[${index}].outcome`),
    confidence: choice(item.confidence ?? 'unknown', confidence, `events[${index}].confidence`),
    detail: text(item.detail), actor: text(item.actor), revision: text(item.revision), reason: text(item.reason),
    evidence: evidence(item.evidence, inputPath, outputPath),
  };
}

export function parseEventLines(source) {
  return source.split(/\r?\n/).flatMap((line, i) => {
    if (!line.trim()) return [];
    try { return [record(JSON.parse(line), `events JSONL line ${i + 1}`)]; }
    catch (error) { throw new Error(`Invalid events JSONL line ${i + 1}: ${error.message}`); }
  });
}

export function mergeEventRecords(initialEvents, records, options = {}) {
  const events = [...initialEvents];
  const positions = new Map(events.map((event, i) => [event.id, i]));
  const warnings = [];
  const inputPath = resolve(options.inputPath ?? 'report.json');
  const outputPath = resolve(options.outputPath ?? 'timeline.html');
  for (let i = 0; i < records.length; i++) {
    const entry = records[i];
    const row = record(entry.record ?? entry, `event record ${i + 1}`);
    const origin = resolve(entry.inputPath ?? options.eventPath ?? inputPath);
    if (row.type === 'end') {
      const at = timestamp(row.at, `event record ${i + 1}.at`);
      const id = text(row.eventId);
      const position = positions.get(id);
      const endingEvidence = evidence(row.evidence, origin, outputPath);
      if (position == null) {
        const orphanId = `orphan-end-${id || 'unknown'}-${i + 1}`;
        warnings.push(`End record ${id || '(no id)'} has no start; only its recorded instant is retained.`);
        events.push({ id: orphanId, lane: 'Unmatched records', kind: 'point', category: 'coordination',
          label: `Unmatched end: ${id || 'no id'}`, start: at, outcome: choice(row.outcome ?? 'unknown', outcomes, `orphan end ${id}.outcome`), confidence: 'unknown',
          detail: text(row.detail), actor: text(row.actor), revision: text(row.revision),
          reason: [text(row.reason), 'Start not recorded; no measured duration'].filter(Boolean).join('\n'), evidence: endingEvidence });
        positions.set(orphanId, events.length - 1);
        continue;
      }
      const event = events[position];
      if (Date.parse(at) < Date.parse(event.start)) throw new Error(`End record ${id} precedes start`);
      if (event.end) throw new Error(`Repeated end record: ${id}`);
      if (event.kind === 'point') warnings.push(`Point ${id} received an end record; retained as a point with no measured duration.`);
      events[position] = { ...event, ...(event.kind === 'point' ? {} : { end: at }),
        outcome: choice(row.outcome ?? 'unknown', outcomes, `end ${id}.outcome`),
        confidence: choice(row.confidence ?? event.confidence, confidence, `end ${id}.confidence`),
        actor: row.actor == null ? event.actor : text(row.actor),
        reason: row.reason == null ? event.reason : text(row.reason),
        revision: row.revision == null ? event.revision : text(row.revision),
        detail: [event.detail, text(row.detail)].filter(Boolean).join('\n'), evidence: [...event.evidence, ...endingEvidence] };
      continue;
    }
    let source;
    if (row.type === 'start' || row.type === 'point') {
      timestamp(row.at, `event record ${i + 1}.at`);
      source = record(row.event, `event record ${i + 1}.event`);
      if (row.type === 'point' && source.kind !== 'point') throw new Error('Point envelope must contain a point event');
    } else if (row.type != null) throw new Error(`Unknown event record type: ${row.type}`);
    else source = row;
    const event = normalizeEvent(source, i, origin, outputPath);
    if (positions.has(event.id)) throw new Error(`Duplicate event id: ${event.id}`);
    positions.set(event.id, events.length);
    events.push(event);
  }
  return { events, warnings };
}

export function summarizeCommands(events) {
  const intervals = events.filter(event => event.kind === 'command' && event.end)
    .map(event => [Date.parse(event.start), Date.parse(event.end)])
    .filter(([start, end]) => Number.isFinite(start) && Number.isFinite(end) && end >= start)
    .sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const cumulativeMs = intervals.reduce((sum, [start, end]) => sum + end - start, 0);
  let unionMs = 0;
  let active;
  for (const interval of intervals) {
    if (!active) active = [...interval];
    else if (interval[0] <= active[1]) active[1] = Math.max(active[1], interval[1]);
    else { unionMs += active[1] - active[0]; active = [...interval]; }
  }
  if (active) unionMs += active[1] - active[0];
  return { count: intervals.length, cumulativeMs, unionMs, overlapMs: cumulativeMs - unionMs };
}

export function normalizeReport(input, options = {}) {
  record(input, 'report');
  const inputPath = resolve(options.inputPath ?? 'report.json');
  const outputPath = resolve(options.outputPath ?? 'timeline.html');
  const task = record(input.task, 'task');
  const startedAt = timestamp(task.startedAt, 'task.startedAt');
  const endedAt = timestamp(task.endedAt, 'task.endedAt', true);
  const snapshotAt = timestamp(input.snapshotAt, 'snapshotAt');
  if (endedAt && Date.parse(endedAt) < Date.parse(startedAt)) throw new Error('task.endedAt precedes task.startedAt');
  if (Date.parse(snapshotAt) < Date.parse(startedAt)) throw new Error('snapshotAt precedes task.startedAt');
  const initialEvents = list(input.events, 'events').map((item, i) => normalizeEvent(item, i, inputPath, outputPath));
  const { events, warnings } = mergeEventRecords(initialEvents, options.eventRecords ?? [], { ...options, inputPath, outputPath });
  const eventIds = new Set();
  for (const event of events) {
    if (eventIds.has(event.id)) throw new Error(`Duplicate event id: ${event.id}`);
    eventIds.add(event.id);
  }
  const rows = (key, mapper) => list(input[key], key).map((item, i) => mapper(record(item, `${key}[${i}]`), i));
  const report = {
    project: text(input.project, 'Project'), title: text(input.title, 'Development timeline'), snapshotAt,
    ...(input.checkpoint == null ? {} : { checkpoint: record(input.checkpoint, 'checkpoint') }),
    task: { status: choice(task.status, taskStatuses, 'task.status'), goal: text(task.goal), startedAt, ...(endedAt ? { endedAt } : {}) },
    scope: { platform: text(input.scope?.platform, 'desktop-web'), mobile: input.scope?.mobile === true },
    tickets: rows('tickets', (item, i) => ({ id: text(item.id, `ticket-${i + 1}`), title: text(item.title),
      status: choice(item.status, ticketStatuses, `tickets[${i}].status`),
      url: rebaseHref(item.url, inputPath, outputPath), blockers: list(item.blockers, 'blockers').map(String),
      delivered: list(item.delivered, 'delivered').map(String), pending: list(item.pending, 'pending').map(String) })),
    events, warnings,
    findings: rows('findings', (item, i) => ({ id: text(item.id, `finding-${i + 1}`), title: text(item.title),
      mechanism: text(item.mechanism), impact: text(item.impact), proposal: text(item.proposal),
      confidence: choice(item.confidence ?? 'unknown', confidence, `findings[${i}].confidence`), evidence: evidence(item.evidence, inputPath, outputPath) })),
    rules: rows('rules', (item, i) => ({ id: text(item.id, `rule-${i + 1}`), title: text(item.title),
      source: rebaseHref(item.source, inputPath, outputPath), quote: text(item.quote), actual: text(item.actual),
      mechanism: text(item.mechanism), proposal: text(item.proposal), retain: text(item.retain), classification: text(item.classification),
      confidence: choice(item.confidence ?? 'unknown', confidence, `rules[${i}].confidence`), evidence: evidence(item.evidence, inputPath, outputPath) })),
    tests: rows('tests', (item, i) => {
      const start = timestamp(item.start, `tests[${i}].start`, true);
      const end = timestamp(item.end, `tests[${i}].end`, true);
      if (start && end && Date.parse(end) < Date.parse(start)) throw new Error(`tests[${i}].end precedes start`);
      return { id: text(item.id, `test-${i + 1}`), label: text(item.label), method: text(item.method),
        ...(start ? { start } : {}), ...(end ? { end } : {}), uiExecuted: item.uiExecuted,
        outcome: choice(item.outcome ?? 'unknown', outcomes, `tests[${i}].outcome`), cause: text(item.cause), fix: text(item.fix),
        evidence: evidence(item.evidence, inputPath, outputPath) };
    }),
    resources: rows('resources', (item, i) => ({ name: text(item.name), owner: text(item.owner), purpose: text(item.purpose),
      disposition: choice(item.disposition, new Set(['cleaned', 'retained', 'unknown']), `resources[${i}].disposition`) })),
  };
  report.metrics = summarizeCommands(events);
  return report;
}

function escapeHTML(value) {
  return value.replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
}

function safeJSON(value) {
  return JSON.stringify(value).replace(/[<>&\u2028\u2029]/g, character => `\\u${character.charCodeAt(0).toString(16).padStart(4, '0')}`);
}

export async function renderReport(input, options = {}) {
  return renderNormalizedReport(normalizeReport(input, options));
}

export async function renderNormalizedReport(report) {
  const template = await readFile(templatePath, 'utf8');
  const replacements = { __REPORT_TITLE__: escapeHTML(report.title), __REPORT_DATA__: safeJSON(report) };
  return template.replace(/__REPORT_TITLE__|__REPORT_DATA__/g, marker => replacements[marker]);
}

export async function renderFile({ input, events, output }) {
  const inputPath = resolve(input);
  const outputPath = resolve(output);
  const eventPaths = events == null ? [] : Array.isArray(events) ? events : [events];
  const sourcePaths = [inputPath, ...eventPaths.map(path => resolve(path))];
  if (sourcePaths.includes(outputPath)) throw new Error('Output must not overwrite an input file');
  const outputStat = await stat(outputPath).catch(error => {
    if (error.code === 'ENOENT') return undefined;
    throw error;
  });
  if (outputStat) {
    for (const path of sourcePaths) {
      const sourceStat = await stat(path);
      if (sourceStat.dev === outputStat.dev && sourceStat.ino === outputStat.ino) {
        throw new Error('Output must not overwrite an input file');
      }
    }
  }
  const report = JSON.parse(await readFile(inputPath, 'utf8'));
  const eventRecords = [];
  for (const path of eventPaths) {
    for (const record of parseEventLines(await readFile(resolve(path), 'utf8'))) eventRecords.push({ record, inputPath: resolve(path) });
  }
  const html = await renderReport(report, { inputPath, outputPath, eventRecords });
  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, html, 'utf8');
  return outputPath;
}

function usage() { return 'Usage: node render.mjs --input REPORT.json [--events events.jsonl] --output timeline.html'; }

export function parseArgs(args) {
  const options = {};
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--help' || args[i] === '-h') return { help: true };
    if (!['--input', '--events', '--output'].includes(args[i])) throw new Error(`Unknown option: ${args[i]}\n${usage()}`);
    const key = args[i].slice(2);
    if (options[key] && key !== 'events') throw new Error(`Repeated option: ${args[i]}`);
    if (!args[i + 1] || args[i + 1].startsWith('--')) throw new Error(`Missing value for ${args[i]}`);
    const value = args[++i];
    options[key] = key === 'events' && options[key] ? [options[key], value].flat() : value;
  }
  if (!options.input || !options.output) throw new Error(usage());
  return options;
}

function isMainModule() {
  if (!process.argv[1] || process.argv[1] === '-') return false;
  try { return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url)); }
  catch { return false; }
}

if (isMainModule()) {
  try {
    const options = parseArgs(process.argv.slice(2));
    if (options.help) console.log(usage());
    else console.log(await renderFile(options));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
