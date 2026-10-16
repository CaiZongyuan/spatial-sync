#!/usr/bin/env node
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptPath = fileURLToPath(import.meta.url);
const terminalCheck = new Set(['completed', 'success', 'failure', 'error']);
const help = `Read-only GitHub CI observer (Node.js 18+, authenticated gh).
Usage: node ci-observer.mjs --repo OWNER/NAME --pr NUMBER --output ci.json
       [--watch] [--interval-ms 15000] [--timeout-ms 300000]

One-shot reads the PR head, Actions attempts/jobs, check runs, and status contexts.
Watch exits when all currently visible CI is terminal, or on timeout/cancellation.
No checks is not a pass; visible CI is not the host's required-checks policy.
JSON is written atomically. stdout reports only initial facts and real changes.
Exit: 0 observation complete (CI may have failed), 1 API/output error,
      2 invalid arguments, 124 timeout, 130 cancellation.
This never triggers CI, writes GitHub state, or downloads job logs.`;

function abortError() { return Object.assign(new Error('Observation interrupted'), { name: 'AbortError' }); }
function assertActive(signal) { if (signal.aborted) throw abortError(); }

/** Run only gh's GET API command; keep stderr and credentials out of reports. */
export function createGhApi() {
  return (endpoint, { signal }) => new Promise((resolve, reject) => {
    if (signal.aborted) return reject(abortError());
    const child = spawn('gh', ['api', '--hostname', 'github.com', '--method', 'GET',
      '--paginate', '--jq', '@json', endpoint], {
      stdio: ['ignore', 'pipe', 'ignore'], detached: process.platform !== 'win32',
    });
    let stdout = '';
    let stdoutBytes = 0;
    let stopped = false;
    let force;
    const kill = (kind) => {
      if (!child.pid) return;
      try {
        if (process.platform === 'win32') child.kill(kind);
        else process.kill(-child.pid, kind);
      } catch (error) { if (error.code !== 'ESRCH') throw error; }
    };
    const stop = () => {
      if (stopped) return;
      stopped = true;
      kill('SIGTERM');
      force = setTimeout(() => kill('SIGKILL'), 500);
    };
    signal.addEventListener('abort', stop, { once: true });
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', chunk => {
      if (stopped) return;
      stdoutBytes += Buffer.byteLength(chunk, 'utf8');
      if (stdoutBytes > 32 * 1024 * 1024) { stop(); return; }
      stdout += chunk;
    });
    child.on('error', () => {
      signal.removeEventListener('abort', stop);
      clearTimeout(force);
      reject(Object.assign(new Error('Unable to start gh'), { code: 'gh_unavailable' }));
    });
    child.on('close', code => {
      signal.removeEventListener('abort', stop);
      // Stop the whole owned process group, including any helper surviving gh.
      if (stopped) { kill('SIGKILL'); clearTimeout(force); }
      if (signal.aborted) return reject(abortError());
      if (code !== 0 || stopped) return reject(Object.assign(new Error('GitHub API request failed'), { code: 'api_error' }));
      try {
        const result = stdout.split(/\r?\n/).filter(line => line.trim()).map(line => JSON.parse(line));
        if (!result.length) throw new Error('Empty API response');
        resolve(result);
      }
      catch { reject(Object.assign(new Error('Invalid GitHub API response'), { code: 'api_response' })); }
    });
  });
}

function pages(value) {
  if (!Array.isArray(value)) throw Object.assign(new Error('Expected paginated API data'), { code: 'api_response' });
  return value;
}
function records(value, key) {
  return pages(value).flatMap(page => {
    if (!Array.isArray(page?.[key])) throw Object.assign(new Error('Missing API records'), { code: 'api_response' });
    return page[key];
  });
}
function sort(items) { return items.sort((a, b) => String(a.id ?? a.name).localeCompare(String(b.id ?? b.name), 'en', { numeric: true })); }
function locator(value) {
  if (typeof value !== 'string' || !/^https?:\/\//i.test(value) || /[\u0000-\u001f\u007f?#]/.test(value)) return null;
  try {
    const url = new URL(value);
    const authority = value.match(/^https?:\/\/([^/]+)/i)?.[1];
    return ['http:', 'https:'].includes(url.protocol) && url.hostname && !url.username && !url.password && !authority?.includes('@') ? url.href : null;
  } catch { return null; }
}
function job(value) {
  return { id: value.id, name: value.name, status: value.status, conclusion: value.conclusion ?? null,
    url: locator(value.html_url), startedAt: value.started_at ?? null, completedAt: value.completed_at ?? null };
}

async function together(promises) {
  const results = await Promise.allSettled(promises);
  const rejected = results.find(result => result.status === 'rejected');
  if (rejected) throw rejected.reason;
  return results.map(result => result.value);
}

async function readPull(prefix, pr, api, signal) {
  const pull = pages(await api(`${prefix}/pulls/${pr}`, { signal }))[0];
  if (!/^[a-f0-9]{40}$/i.test(pull?.head?.sha ?? '') || !['open', 'closed'].includes(pull.state)) {
    throw Object.assign(new Error('Incomplete pull request response'), { code: 'api_response' });
  }
  return pull;
}

async function readSnapshot(repo, pr, api, signal) {
  const prefix = `repos/${repo}`;
  const pull = await readPull(prefix, pr, api, signal);
  const headSha = pull.head.sha;
  const [runPages, checkPages, statusPages] = await together([
    api(`${prefix}/actions/runs?head_sha=${encodeURIComponent(headSha)}&per_page=100`, { signal }),
    api(`${prefix}/commits/${headSha}/check-runs?filter=latest&per_page=100`, { signal }),
    api(`${prefix}/commits/${headSha}/status?per_page=100`, { signal }),
  ]);
  const rawRuns = records(runPages, 'workflow_runs').filter(run => run.head_sha === headSha);
  if (rawRuns.some(run => !Number.isSafeInteger(run.id) || !Number.isSafeInteger(run.run_attempt) || run.run_attempt < 1 || !run.status)) {
    throw Object.assign(new Error('Incomplete workflow run response'), { code: 'api_response' });
  }
  const jobs = await together(rawRuns.map(run => api(
    `${prefix}/actions/runs/${run.id}/attempts/${run.run_attempt}/jobs?per_page=100`, { signal })));
  const runs = sort(rawRuns.map((run, index) => ({ id: run.id, name: run.name, headSha: run.head_sha,
    attempt: run.run_attempt, status: run.status, conclusion: run.conclusion ?? null,
    url: locator(run.html_url), startedAt: run.run_started_at ?? null,
    // The runs API has no completed_at. updated_at can reflect later metadata edits.
    completedAt: null, jobs: sort(records(jobs[index], 'jobs').map(job)),
  })));
  const checks = records(checkPages, 'check_runs').filter(check => check.head_sha === headSha).map(check => ({
    ...job(check), headSha: check.head_sha, source: 'check-run',
  }));
  const contexts = new Map();
  for (const status of records(statusPages, 'statuses')) {
    // Combined status returns newest first; one current state per legacy context.
    if (!contexts.has(status.context)) contexts.set(status.context, status);
  }
  for (const status of contexts.values()) checks.push({ id: status.id, name: status.context,
    headSha, status: status.state === 'pending' ? 'in_progress' : 'completed',
    conclusion: status.state === 'pending' ? null : status.state,
    url: locator(status.target_url), startedAt: null,
    completedAt: status.state === 'pending' ? null : status.updated_at ?? null, source: 'status-context',
  });
  const confirmedPull = await readPull(prefix, pr, api, signal);
  if (confirmedPull.head.sha !== headSha) {
    throw Object.assign(new Error('Pull request head changed during collection'), { code: 'head_changed' });
  }
  return { headSha, pullRequest: { state: confirmedPull.state, mergedAt: confirmedPull.merged_at ?? null,
    mergeSha: confirmedPull.merged_at ? confirmedPull.merge_commit_sha ?? null : null }, runs, checks: sort(checks) };
}

function facts(report) {
  return { headSha: report.headSha, pullRequest: report.pullRequest, runs: report.runs, checks: report.checks };
}
function fingerprint(report) { return JSON.stringify({ ...facts(report), observation: report.observation }); }
function visibleCiFinished(report) {
  return report.runs.length + report.checks.length > 0 &&
    report.runs.every(run => run.status === 'completed' && run.jobs.every(item => item.status === 'completed')) &&
    report.checks.every(check => terminalCheck.has(check.status));
}
function notification(report) {
  if (report.observation.status !== 'ok') return `CI observation ${report.observation.status}; retained facts are stale.`;
  const runText = report.runs.map(run => {
    const jobStates = new Map();
    for (const item of run.jobs) {
      const key = `${item.status}${item.conclusion ? `/${item.conclusion}` : ''}`;
      jobStates.set(key, (jobStates.get(key) ?? 0) + 1);
    }
    return `${run.name} #${run.id}/${run.attempt} ${run.status}${run.conclusion ? `/${run.conclusion}` : ''}${run.jobs.length ? ` [jobs ${[...jobStates].map(([state, count]) => `${count} ${state}`).join(', ')}]` : ''}`;
  }).join(', ');
  const checkText = report.checks.map(check => `${check.name} ${check.status}${check.conclusion ? `/${check.conclusion}` : ''}`).join(', ');
  const message = `PR #${report.source.pr} ${report.pullRequest.mergedAt ? 'merged' : report.pullRequest.state} head ${report.headSha.slice(0, 8)}: ${runText || 'no Actions runs'}; ${checkText || 'no checks'}.`;
  return message.length > 1200 ? `${message.slice(0, 1180)}… (see JSON)` : message;
}
function delay(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(abortError());
    const timer = setTimeout(() => { signal.removeEventListener('abort', stop); resolve(); }, ms);
    const stop = () => { clearTimeout(timer); reject(abortError()); };
    signal.addEventListener('abort', stop, { once: true });
  });
}

/** Injectable API supports offline behavioral tests without a network or gh login. */
export async function observeCi({ repo, pr, watch = false, intervalMs = 15000, timeoutMs = 300000,
  api = createGhApi(), signal, onUpdate = async () => {}, onNotify = () => {},
  now = () => new Date().toISOString(), ownerId = randomUUID() }) {
  const controller = new AbortController();
  const cancel = () => controller.abort({ kind: 'cancelled' });
  signal?.addEventListener('abort', cancel, { once: true });
  if (signal?.aborted) cancel();
  const timer = setTimeout(() => controller.abort({ kind: 'timeout' }), timeoutMs);
  let report = { schemaVersion: 1, producer: { name: 'ci-observer', ownerId },
    source: { repo, pr, url: `https://github.com/${repo}/pull/${pr}` }, headSha: null,
    observedAt: null, observation: { status: 'ok' }, lastSuccessfulAt: null, stale: true,
    pullRequest: null, runs: [], checks: [], history: [] };
  let previous;
  const publish = async type => {
    report.observedAt = now();
    const current = fingerprint(report);
    if (current !== previous) {
      report.history.push({ at: report.observedAt, type, observationStatus: report.observation.status, ...facts(report) });
      onNotify(notification(report));
      previous = current;
    }
    await onUpdate(report);
  };
  try {
    while (true) {
      try {
        assertActive(controller.signal);
        const snapshot = await readSnapshot(repo, pr, api, controller.signal);
        assertActive(controller.signal);
        const recovered = report.observation.status === 'error';
        report = { ...report, ...snapshot, observation: { status: 'ok' }, stale: false, lastSuccessfulAt: now() };
        await publish(recovered ? 'recovery' : 'snapshot');
        assertActive(controller.signal);
        if (!watch || visibleCiFinished(report)) return report;
      } catch (error) {
        if (error.name === 'AbortError' || controller.signal.aborted) throw abortError();
        report.observation = { status: 'error', error: { code: error.code ?? 'api_error',
          message: error.code === 'head_changed'
            ? 'PR head changed during collection. Retry the current candidate; retained facts are stale.'
            : 'GitHub observation failed. Check gh authentication, repository access, and network; retained facts are stale.' } };
        report.stale = true;
        await publish('error');
        if (!watch) return report;
      }
      await delay(intervalMs, controller.signal);
    }
  } catch (error) {
    if (error.name !== 'AbortError') throw error;
    const status = controller.signal.reason?.kind ?? 'cancelled';
    report.observation = { status };
    report.stale = true;
    await publish(status);
    return report;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', cancel);
  }
}

function parseArgs(args) {
  const options = {};
  for (let index = 0; index < args.length; index++) {
    const arg = args[index];
    if (arg === '--help' || arg === '-h') return { help: true };
    if (arg === '--watch') { options.watch = true; continue; }
    const keys = { '--repo': 'repo', '--pr': 'pr', '--output': 'output', '--interval-ms': 'intervalMs', '--timeout-ms': 'timeoutMs' };
    if (!keys[arg] || !args[index + 1] || args[index + 1].startsWith('--') || keys[arg] in options) throw new Error('Invalid or duplicate arguments');
    options[keys[arg]] = args[++index];
  }
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(options.repo ?? '') || !/^[1-9]\d*$/.test(options.pr ?? '') || !options.output) throw new Error('Require explicit --repo OWNER/NAME, --pr NUMBER, and --output FILE');
  options.pr = Number(options.pr);
  if (!Number.isSafeInteger(options.pr)) throw new Error('Invalid PR number');
  for (const [key, fallback] of [['intervalMs', 15000], ['timeoutMs', 300000]]) {
    if (options[key] !== undefined && !/^[1-9]\d*$/.test(options[key])) throw new Error(`Invalid ${key}`);
    options[key] = Number(options[key] ?? fallback);
    if (!Number.isSafeInteger(options[key]) || options[key] > 2147483647) throw new Error(`Invalid ${key}`);
  }
  return options;
}

async function ownedOutput(output, source, ownerId) {
  const full = path.resolve(output);
  if (full.endsWith('.lock') || full.endsWith('.tmp')) throw new Error('Output overlaps observer bookkeeping');
  const parent = await fs.realpath(path.dirname(full));
  const target = path.join(parent, path.basename(full));
  if (target === await fs.realpath(scriptPath)) throw new Error('Output overlaps the observer source');
  try {
    const stat = await fs.lstat(target);
    if (!stat.isFile() || stat.isSymbolicLink()) throw new Error('Output must be an ordinary owned JSON file');
    const existing = JSON.parse(await fs.readFile(target, 'utf8'));
    if (existing?.producer?.name !== 'ci-observer' || existing.schemaVersion !== 1 ||
      existing.source?.repo !== source.repo || existing.source?.pr !== source.pr) throw new Error('Output belongs to other input or source');
  } catch (error) { if (error.code !== 'ENOENT') throw error; }
  const lock = `${target}.lock`;
  const temporary = `${target}.${ownerId}.tmp`;
  const handle = await fs.open(lock, 'wx', 0o600);
  try { await handle.writeFile(JSON.stringify({ ownerId, pid: process.pid })); }
  catch (error) { await fs.unlink(lock); throw error; }
  finally { await handle.close(); }
  return {
    write: async report => {
      try {
        await fs.writeFile(temporary, `${JSON.stringify(report, null, 2)}\n`, { flag: 'wx', mode: 0o600 });
        await fs.rename(temporary, target);
      } finally { await fs.rm(temporary, { force: true }); }
    },
    close: async () => {
      await fs.rm(temporary, { force: true });
      const current = JSON.parse(await fs.readFile(lock, 'utf8'));
      if (current.ownerId === ownerId) await fs.unlink(lock);
    },
  };
}

export async function main(args = process.argv.slice(2)) {
  let options;
  try { options = parseArgs(args); }
  catch (error) { process.stderr.write(`${error.message}\nUse --help for usage.\n`); return 2; }
  if (options.help) { process.stdout.write(`${help}\n`); return 0; }
  const ownerId = randomUUID();
  const cancellation = new AbortController();
  const stop = () => cancellation.abort();
  process.on('SIGINT', stop);
  process.on('SIGTERM', stop);
  let output;
  try {
    output = await ownedOutput(options.output, options, ownerId);
    const report = await observeCi({ ...options, ownerId, signal: cancellation.signal,
      onUpdate: report => output.write(report), onNotify: message => process.stdout.write(`${message}\n`) });
    return { ok: 0, error: 1, timeout: 124, cancelled: 130 }[report.observation.status];
  } catch {
    process.stderr.write('CI observer could not write its owned output or start observation. Check the output path and lock; no raw API errors were saved.\n');
    return 1;
  } finally {
    try { await output?.close(); }
    finally { process.removeListener('SIGINT', stop); process.removeListener('SIGTERM', stop); }
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === scriptPath) {
  process.exitCode = await main();
}
