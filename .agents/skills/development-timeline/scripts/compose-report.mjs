#!/usr/bin/env node
import { readFile, writeFile, mkdir, stat, lstat, realpath, rename, rm } from 'node:fs/promises';
import { realpathSync } from 'node:fs';
import { dirname, basename, resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { normalizeReport, parseEventLines, rebaseHref, renderNormalizedReport } from './render.mjs';

const usage = 'Usage: node compose-report.mjs --input report.json [--ci ci.json] [--events journal.jsonl ...] --current current.md --output timeline.html';
const observationStates = new Set(['ok', 'error', 'cancelled', 'timeout']);
const isSHA = value => typeof value === 'string' && /^[a-f0-9]{40}$/i.test(value);
const label = value => value == null ? 'Unknown' : typeof value === 'string' ? value : JSON.stringify(value, null, 2);

function object(value, name) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${name} must be an object`);
  return value;
}
function rows(value, name) {
  if (value == null) return [];
  if (!Array.isArray(value)) throw new Error(`${name} must be an array`);
  return value;
}
function time(value, name) {
  if (value == null) return null;
  if (typeof value !== 'string' || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) {
    throw new Error(`${name} must be an ISO timestamp with a timezone`);
  }
  return new Date(value).toISOString();
}

// This presents recorded facts, not a required-check or merge gate decision.
export function normalizeCI(snapshot, { inputPath, outputPath, revision } = {}) {
  object(snapshot, 'CI snapshot');
  if (snapshot.schemaVersion !== 1) throw new Error('CI snapshot schemaVersion must be 1');
  const source = object(snapshot.source, 'CI source');
  const observation = object(snapshot.observation, 'CI observation');
  if (!observationStates.has(observation.status)) throw new Error('Unknown CI observation status');
  const observedAt = time(snapshot.observedAt, 'CI observedAt');
  if (!observedAt) throw new Error('CI observedAt is required');
  const url = safeCIURL;
  const normalizeCheck = (row, name) => {
    object(row, name);
    return { ...row, url: url(row.url), startedAt: time(row.startedAt, `${name}.startedAt`), completedAt: time(row.completedAt, `${name}.completedAt`) };
  };
  const runs = rows(snapshot.runs, 'CI runs').map((row, index) => {
    const name = `CI runs[${index}]`;
    return { ...normalizeCheck(row, name), jobs: rows(row.jobs, `${name}.jobs`).map((job, j) => normalizeCheck(job, `${name}.jobs[${j}]`)) };
  });
  const checks = rows(snapshot.checks, 'CI checks').map((row, i) => normalizeCheck(row, `CI checks[${i}]`));
  const relation = isSHA(revision) && isSHA(snapshot.headSha) ?
    (revision.toLowerCase() === snapshot.headSha.toLowerCase() ? 'current' : 'historical') : 'unknown';
  const stale = snapshot.stale === true || observation.status !== 'ok';
  return { schemaVersion: 1, source: { ...source, url: url(source.url) }, headSha: snapshot.headSha ?? null,
    observedAt, observation: { ...observation }, lastSuccessfulAt: time(snapshot.lastSuccessfulAt, 'CI lastSuccessfulAt'),
    stale, pullRequest: snapshot.pullRequest ?? null, runs, checks,
    assessment: { revision: revision ?? null, relation, freshness: stale ? 'stale' : relation === 'current' ? 'observed' : 'unknown' },
    history: rows(snapshot.history, 'CI history').map((row, i) => ({
      at: time(object(row, `CI history[${i}]`).at, `CI history[${i}].at`), type: row.type,
      headSha: row.headSha ?? null, observationStatus: row.observationStatus,
    })) };
}

// CI locators are public links; signed or credential-bearing URLs are not retained.
export function safeCIURL(value) {
  if (typeof value !== 'string' || /[\u0000-\u001f\u007f]/.test(value) || !/^https?:\/\//i.test(value)) return null;
  try {
    const url = new URL(value);
    const authority = value.match(/^https?:\/\/([^/]+)/i)?.[1];
    return ['http:', 'https:'].includes(url.protocol) && url.hostname && !url.username && !url.password && !authority?.includes('@') &&
      !url.search && !url.hash && !value.includes('?') && !value.includes('#') ? url.href : null;
  } catch { return null; }
}

// Encode delimiters without changing the resource represented by the URL.
function markdownDestination(href) {
  return href.replace(/[()<>\s]/g, character => character === '(' ? '%28' : character === ')' ? '%29' : encodeURIComponent(character))
    .replace(/&/g, '&amp;');
}

function markdown(value) { return label(value).replace(/[\\`*_\[\]<>]/g, '\\$&'); }
function block(value) { return label(value).split('\n').map(line => `> ${line.replace(/</g, '&lt;').replace(/>/g, '&gt;')}`).join('\n'); }
function rowState(row) { return `${row.status ?? 'unknown'} / ${row.conclusion ?? 'unknown'}`; }

export function renderCurrent(report, { reportPath, currentPath } = {}) {
  const lines = [`# ${markdown(report.project)} — current checkpoint`, '',
    `Snapshot: ${report.snapshotAt}`, `Task status (PM supplied): ${report.task.status}`, '',
    '## Goal and scope', '', block(report.task.goal || 'Goal not recorded'), '',
    `Platform: ${markdown(report.scope.platform)}; mobile included: ${report.scope.mobile}`, '',
    'The generator does not infer completion, authorization, or merge readiness from CI.', ''];
  if (report.checkpoint) {
    lines.push('## Manual checkpoint', '');
    for (const [key, value] of Object.entries(report.checkpoint)) lines.push(`**${markdown(key)}**`, '', block(value), '');
  }
  if (report.tickets.length) {
    lines.push('## Tickets', '');
    for (const ticket of report.tickets) {
      lines.push(`- ${markdown(ticket.id)}: ${markdown(ticket.title)} — ${ticket.status}`);
      if (ticket.pending.length) lines.push(`  Pending: ${markdown(ticket.pending.join('; '))}`);
    }
    lines.push('');
  }
  const ci = report.ci;
  if (ci) {
    lines.push('## CI observation', '', `Source: ${markdown(ci.source.repo)} PR ${markdown(ci.source.pr)}`,
      `Head: ${ci.headSha ?? 'Unknown'}; candidate relationship: ${ci.assessment.relation}`,
      `Current observation: ${ci.observation.status}; evidence freshness: ${ci.assessment.freshness}`,
      `Observed at: ${ci.observedAt}; last successful observation: ${ci.lastSuccessfulAt ?? 'Unknown'}`, '');
    if (ci.source.url) lines.push(`[CI source](${markdownDestination(rebaseHref(ci.source.url, reportPath, currentPath))})`, '');
    if (ci.observation.error) lines.push('Observation error:', '', block(ci.observation.error), '');
    if (ci.stale) lines.push('The rows below are last known CI facts; the latest observation did not refresh them.', '');
    if (ci.assessment.relation !== 'current') lines.push('These facts do not establish checks for the current candidate.', '');
    if (ci.pullRequest) lines.push(`PR state: ${markdown(ci.pullRequest.state)}; merged at: ${markdown(ci.pullRequest.mergedAt)}; merge SHA: ${markdown(ci.pullRequest.mergeSha)}`, '');
    lines.push('### Recorded runs and checks', '');
    for (const run of ci.runs) {
      lines.push(`- Run ${markdown(run.name)} (${markdown(run.id)}), head ${markdown(run.headSha)}, attempt ${markdown(run.attempt)}: ${markdown(rowState(run))}`,
        `  Runner started: ${run.startedAt ?? 'Unknown'}; finished: ${run.completedAt ?? 'Unknown'}`);
      for (const job of run.jobs) lines.push(`  Job ${markdown(job.name)}: ${markdown(rowState(job))}; started: ${job.startedAt ?? 'Unknown'}; finished: ${job.completedAt ?? 'Unknown'}`);
    }
    for (const check of ci.checks) lines.push(`- Check ${markdown(check.name)}, head ${markdown(check.headSha)}: ${markdown(rowState(check))}`,
      `  Runner started: ${check.startedAt ?? 'Unknown'}; finished: ${check.completedAt ?? 'Unknown'}`);
    if (!ci.runs.length && !ci.checks.length) lines.push('No CI records observed. Required checks and passing status are unknown.');
    lines.push('', 'Required checks are not established by this report. Observation times are not runner execution times.', '');
  }
  lines.push('## Recent recorded facts', '');
  const recent = [...report.events].sort((a, b) => Date.parse(b.end ?? b.start) - Date.parse(a.end ?? a.start)).slice(0, 5);
  for (const event of recent) lines.push(`- ${event.end ?? event.start}: ${markdown(event.label)} — ${event.outcome}; actor ${markdown(event.actor || 'Unknown')}; revision ${markdown(event.revision || 'Unknown')}`);
  if (!recent.length) lines.push('No events recorded.');
  if (report.resources.length) {
    lines.push('', '## Resources', '');
    for (const resource of report.resources) lines.push(`- ${markdown(resource.name)}; owner ${markdown(resource.owner)}; ${resource.disposition}; ${markdown(resource.purpose)}`);
  }
  const evidence = [...recent, ...report.findings, ...report.rules, ...report.tests].flatMap(row => row.evidence ?? []);
  if (evidence.length) {
    lines.push('', '## Evidence', '');
    for (const item of evidence) {
      const href = item.href && rebaseHref(item.href, reportPath, currentPath);
      lines.push(`- ${href ? `[${markdown(item.label)}](${markdownDestination(href)})` : markdown(item.label)}`);
    }
  }
  if (report.warnings.length) lines.push('', '## Recording limits', '', ...report.warnings.map(value => `- ${markdown(value)}`));
  return lines.join('\n') + '\n';
}

async function existingStat(path, inspect = stat) {
  try { return await inspect(path); } catch (error) { if (error.code === 'ENOENT') return undefined; throw error; }
}
async function canonicalDestination(path) {
  try { return await realpath(path); } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    const parent = dirname(path);
    if (parent === path) throw error;
    return join(await canonicalDestination(parent), basename(path));
  }
}
async function guardPaths(sources, outputs) {
  const sourceInfo = await Promise.all(sources.map(async path => ({ path: await realpath(path), stat: await stat(path) })));
  const outputInfo = [];
  for (const path of outputs) {
    const info = await existingStat(path, lstat);
    if (info?.isSymbolicLink()) throw new Error(`Output must not overwrite an input or use a symbolic link: ${path}`);
    if (info && !info.isFile()) throw new Error(`Output must be a regular file: ${path}`);
    const entry = { path: await canonicalDestination(path), stat: info };
    const same = other => entry.path === other.path || (entry.stat && other.stat && entry.stat.dev === other.stat.dev && entry.stat.ino === other.stat.ino);
    if (sourceInfo.some(same)) throw new Error('Outputs must not overwrite an input file');
    if (outputInfo.some(same)) throw new Error('Current and HTML outputs must be distinct files');
    outputInfo.push(entry);
  }
}

export async function composeFile({ input, ci, events = [], current, output }) {
  const inputPath = resolve(input), currentPath = resolve(current), outputPath = resolve(output);
  const eventPaths = (Array.isArray(events) ? events : [events]).map(path => resolve(path));
  const ciPath = ci && resolve(ci);
  const sources = [inputPath, ...eventPaths, ...(ciPath ? [ciPath] : [])];
  await guardPaths(sources, [currentPath, outputPath]);
  const raw = JSON.parse(await readFile(inputPath, 'utf8'));
  const eventRecords = [];
  for (const path of eventPaths) for (const record of parseEventLines(await readFile(path, 'utf8'))) eventRecords.push({ record, inputPath: path });
  const model = normalizeReport(raw, { inputPath, outputPath, eventRecords });
  if (ciPath) model.ci = normalizeCI(JSON.parse(await readFile(ciPath, 'utf8')), { inputPath: ciPath, outputPath, revision: model.checkpoint?.revision });
  const contents = [renderCurrent(model, { reportPath: outputPath, currentPath }), await renderNormalizedReport(model)];
  const destinations = [currentPath, outputPath], staged = [], published = [];
  try {
    for (const [i, path] of destinations.entries()) {
      await mkdir(dirname(path), { recursive: true });
      const temporary = join(dirname(path), `.${basename(path)}.${randomUUID()}.tmp`);
      staged.push(temporary);
      await writeFile(temporary, contents[i], { encoding: 'utf8', flag: 'wx' });
    }
    // Recheck aliases after staging; replacing two files cannot be a filesystem transaction.
    await guardPaths(sources, destinations);
    for (const [i, path] of destinations.entries()) { await rename(staged[i], path); published.push(path); }
  } catch (error) {
    if (published.length) throw new Error(`Partial publication: refreshed ${published.join(', ')}; other output was not refreshed. Rerun after fixing the error. ${error.message}`);
    throw error;
  } finally { await Promise.all(staged.map(path => rm(path, { force: true }))); }
  return { current: currentPath, output: outputPath };
}

export function parseArgs(args) {
  const options = { events: [] };
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--help' || args[i] === '-h') return { help: true };
    if (!['--input', '--ci', '--events', '--current', '--output'].includes(args[i])) throw new Error(`Unknown option: ${args[i]}\n${usage}`);
    const key = args[i].slice(2), value = args[++i];
    if (!value || value.startsWith('--')) throw new Error(`Missing value for --${key}`);
    if (key === 'events') options.events.push(value);
    else if (options[key]) throw new Error(`Repeated option: --${key}`);
    else options[key] = value;
  }
  if (!options.input || !options.current || !options.output) throw new Error(usage);
  return options;
}
function isMainModule() {
  if (!process.argv[1]) return false;
  try { return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url)); } catch { return false; }
}
if (isMainModule()) {
  try {
    const options = parseArgs(process.argv.slice(2));
    if (options.help) console.log(usage);
    else console.log(JSON.stringify(await composeFile(options)));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
