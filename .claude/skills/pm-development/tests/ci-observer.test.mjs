import test from 'node:test';
import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { observeCi } from '../scripts/ci-observer.mjs';

const script = fileURLToPath(new URL('../scripts/ci-observer.mjs', import.meta.url));
const repo = 'target/real-repository';
const pr = 23;
const sha = 'a'.repeat(40);
const nextSha = 'b'.repeat(40);

function run({ head = sha, attempt = 1, status = 'in_progress', conclusion = null } = {}) {
  return { id: 100, name: 'verify', head_sha: head, run_attempt: attempt, status, conclusion,
    html_url: 'https://github.com/target/real-repository/actions/runs/100',
    run_started_at: '2026-10-06T12:00:00Z' };
}
function check({ head = sha, status = 'in_progress', conclusion = null } = {}) {
  return { id: 200, name: 'contract', head_sha: head, status, conclusion,
    html_url: 'https://github.com/target/real-repository/runs/200',
    started_at: '2026-10-06T12:00:01Z', completed_at: status === 'completed' ? '2026-10-06T12:00:09Z' : null };
}
function scenarioApi(scenarios, calls = []) {
  let index = -1;
  let collecting = false;
  return async endpoint => {
    calls.push(endpoint);
    const pullRequest = endpoint.endsWith(`/pulls/${pr}`);
    const finalRead = pullRequest && collecting;
    if (pullRequest) {
      if (!collecting) index++;
      collecting = !collecting;
    }
    const state = scenarios[Math.min(index, scenarios.length - 1)];
    if (state.error) {
      collecting = false;
      throw Object.assign(new Error('SECRET_TOKEN=do-not-copy'), { code: 'api_error' });
    }
    if (pullRequest) return [{ head: { sha: finalRead ? state.finalHead ?? state.head ?? sha : state.head ?? sha }, state: state.merged ? 'closed' : 'open',
      merged_at: state.merged ? '2026-10-06T12:01:00Z' : null, merge_commit_sha: state.merged ? 'c'.repeat(40) : 'preview-merge' }];
    if (endpoint.includes('/actions/runs?')) return [{ workflow_runs: state.runs ?? [] }];
    if (endpoint.includes('/check-runs?')) return [{ check_runs: state.checks ?? [] }];
    if (endpoint.includes('/status?')) return [{ statuses: state.statuses ?? [] }];
    if (endpoint.includes('/attempts/')) return [{ jobs: state.jobs ?? [] }];
    throw new Error(`Unexpected endpoint: ${endpoint}`);
  };
}
function observer(scenarios, options = {}) {
  const messages = [];
  const saved = [];
  const calls = [];
  return { messages, saved, calls, result: observeCi({ repo, pr, watch: true, intervalMs: 1, timeoutMs: 1000,
    api: scenarioApi(scenarios, calls), onNotify: message => messages.push(message),
    onUpdate: value => saved.push(structuredClone(value)), ...options }) };
}

test('repeated polls do not repeat notifications; visible failures remain failures, not delivery completion', async () => {
  const active = { runs: [run()], checks: [check()] };
  const finished = { runs: [run({ status: 'completed', conclusion: 'failure' })], checks: [check({ status: 'completed', conclusion: 'failure' })] };
  const session = observer([active, active, finished]);
  const report = await session.result;
  assert.equal(session.messages.length, 2);
  assert.equal(report.history.length, 2);
  assert.equal(session.saved.length, 3);
  assert.equal(report.observation.status, 'ok');
  assert.equal(report.runs[0].conclusion, 'failure');
  assert.equal(report.checks[0].conclusion, 'failure');
  assert.equal(report.pullRequest.mergeSha, null);
  assert.equal('completed' in report, false);
  assert.equal('requiredCI' in report, false);
  assert.ok(session.calls.every(endpoint => endpoint.startsWith(`repos/${repo}/`)));
});

test('attempt reruns, jobs, and new heads are distinct; old success is kept only as history', async () => {
  const session = observer([
    { runs: [run({ status: 'completed', conclusion: 'success' })], checks: [check()] },
    { runs: [run({ attempt: 2 })], checks: [check()], jobs: [{ id: 300, name: 'smoke', status: 'queued' }] },
    { runs: [run({ attempt: 2 })], checks: [check()], jobs: [{ id: 300, name: 'smoke', status: 'in_progress' }] },
    { head: nextSha, runs: [run({ status: 'completed', conclusion: 'success' }), run({ head: nextSha })],
      checks: [check({ status: 'completed', conclusion: 'success' }), check({ head: nextSha })] },
    { head: nextSha, runs: [run({ head: nextSha, status: 'completed', conclusion: 'failure' })],
      checks: [check({ head: nextSha, status: 'completed', conclusion: 'failure' })] },
  ]);
  const report = await session.result;
  assert.equal(session.messages.length, 5);
  assert.equal(report.headSha, nextSha);
  assert.ok(report.runs.every(item => item.headSha === nextSha));
  assert.ok(report.checks.every(item => item.headSha === nextSha));
  assert.equal(report.history[0].runs[0].headSha, sha);
  assert.equal(report.history[0].runs[0].conclusion, 'success');
  assert.equal(report.history[1].runs[0].attempt, 2);
  assert.ok(session.calls.some(endpoint => endpoint.includes('/attempts/2/jobs')));
});

test('a head changing during one collection invalidates old green facts and watch retries the new candidate', async () => {
  const oldGreen = { finalHead: nextSha,
    runs: [run({ status: 'completed', conclusion: 'success' })],
    checks: [check({ status: 'completed', conclusion: 'success' })] };
  const oneShot = await observeCi({ repo, pr, api: scenarioApi([oldGreen]) });
  assert.equal(oneShot.observation.status, 'error');
  assert.equal(oneShot.observation.error.code, 'head_changed');
  assert.equal(oneShot.stale, true);
  assert.equal(oneShot.headSha, null);
  assert.equal(oneShot.lastSuccessfulAt, null);
  const session = observer([{ runs: [run()], checks: [check()] }, oldGreen, { head: nextSha,
    runs: [run({ head: nextSha, status: 'completed', conclusion: 'failure' })],
    checks: [check({ head: nextSha, status: 'completed', conclusion: 'failure' })] }]);
  const report = await session.result;
  assert.equal(report.headSha, nextSha);
  assert.equal(report.runs[0].conclusion, 'failure');
  assert.equal(session.saved.length, 3);
  assert.equal(session.saved[1].observation.status, 'error');
  assert.equal(session.saved[1].headSha, sha);
  assert.equal(session.saved[1].runs[0].status, 'in_progress');
  assert.equal(session.saved[1].lastSuccessfulAt, session.saved[0].lastSuccessfulAt);
  assert.equal(report.history.some(item => item.observationStatus === 'ok' && item.headSha === sha && item.runs[0].conclusion === 'success'), false);
});

test('API failure preserves stale facts, deduplicates errors, then notifies recovery', async () => {
  const active = { runs: [run()], checks: [check()] };
  const finished = { runs: [run({ status: 'completed', conclusion: 'success' })], checks: [check({ status: 'completed', conclusion: 'success' })] };
  const session = observer([active, { error: true }, { error: true }, active, finished]);
  const report = await session.result;
  assert.equal(session.messages.length, 4);
  assert.equal(report.history[1].type, 'error');
  assert.equal(report.history[2].type, 'recovery');
  assert.equal(session.saved[1].stale, true);
  assert.equal(session.saved[1].headSha, sha);
  assert.deepEqual(session.saved[1].runs, session.saved[0].runs);
  assert.equal(session.saved[1].lastSuccessfulAt, session.saved[0].lastSuccessfulAt);
  assert.equal(report.stale, false);
  assert.equal(JSON.stringify(report).includes('SECRET_TOKEN'), false);
});

test('legacy contexts keep only their latest state and exact PR merge is factual', async () => {
  const session = observer([{ merged: true, statuses: [
    { id: 2, context: 'deploy', state: 'failure', target_url: 'https://ci.example/failure', updated_at: '2026-10-06T12:01:00Z' },
    { id: 1, context: 'deploy', state: 'success', target_url: 'https://ci.example/old', updated_at: '2026-10-06T12:00:00Z' },
  ] }]);
  const report = await session.result;
  assert.equal(report.checks.length, 1);
  assert.equal(report.checks[0].conclusion, 'failure');
  assert.equal(report.checks[0].source, 'status-context');
  assert.equal(report.pullRequest.state, 'closed');
  assert.equal(report.pullRequest.mergeSha, 'c'.repeat(40));
  assert.equal('completed' in report, false);
});

test('CI URLs omit credentials, queries, and fragments before snapshots or history can expose them', async () => {
  const unsafeUrls = [
    'https://SYNTHETIC_USER:SYNTHETIC_PASSWORD@ci.example/job',
    'https://SYNTHETIC_USER@ci.example/job',
    'https://@ci.example/job',
    'https://ci.example/job?token=SYNTHETIC_TOKEN',
    'https://ci.example/job?signature=SYNTHETIC_SIGNATURE',
    'https://ci.example/job?SYNTHETIC_QUERY',
    'https://ci.example/job#SYNTHETIC_FRAGMENT',
    'https://ci.example/job?',
    'https://ci.example/job#',
    'file:///tmp/SYNTHETIC_FILE',
    'javascript:SYNTHETIC_CODE',
    'https://ci.example/job\nSYNTHETIC_CONTROL',
    'not-a-url-SYNTHETIC_INVALID',
  ];
  const clean = 'https://ci.example/clean-job';
  const urls = [...unsafeUrls, clean];
  const snapshots = [];
  const report = await observeCi({ repo, pr, api: scenarioApi([{
    runs: urls.map((url, index) => ({ ...run({ status: 'completed', conclusion: 'success' }), id: 100 + index, html_url: url })),
    jobs: urls.map((url, index) => ({ id: 300 + index, name: `job-${index}`, status: 'completed', conclusion: 'success', html_url: url })),
    checks: urls.map((url, index) => ({ ...check({ status: 'completed', conclusion: 'success' }), id: 200 + index, html_url: url })),
    statuses: urls.map((url, index) => ({ id: 400 + index, context: `legacy-${index}`, state: 'success', target_url: url })),
  }]), onUpdate: value => snapshots.push(structuredClone(value)) });
  assert.equal(report.observation.status, 'ok');
  assert.ok(report.runs.slice(0, -1).every(value => value.url === null));
  assert.ok(report.runs.every(value => value.jobs.slice(0, -1).every(item => item.url === null)));
  assert.ok(report.checks.filter(value => value.url !== clean).every(value => value.url === null));
  assert.equal(report.runs.at(-1).url, clean);
  assert.equal(report.runs[0].jobs.at(-1).url, clean);
  assert.equal(report.checks.filter(value => value.url === clean).length, 2);
  assert.equal(JSON.stringify({ report, snapshots }).includes('SYNTHETIC_'), false);
  assert.equal(report.source.url, `https://github.com/${repo}/pull/${pr}`);
});

test('clean public CI locators preserve legal path spaces as equivalent encoded URLs', async () => {
  const report = await observeCi({ repo, pr, api: scenarioApi([{
    statuses: [{ id: 500, context: 'proof', state: 'success', target_url: 'https://ci.example/proof/a b).log' }],
  }]) });
  assert.equal(report.checks[0].url, 'https://ci.example/proof/a%20b).log');
  assert.equal(report.history[0].checks[0].url, 'https://ci.example/proof/a%20b).log');
});

test('a queued job on a second API page keeps watch active until it actually finishes', async () => {
  const states = [{ runs: [run({ status: 'completed', conclusion: 'success' })],
    checks: [check({ status: 'completed', conclusion: 'success' })] }];
  const baseline = scenarioApi(states);
  const saved = [];
  let poll = 0;
  let pullReads = 0;
  const report = await observeCi({ repo, pr, watch: true, intervalMs: 1, timeoutMs: 1000,
    api: async endpoint => {
      if (endpoint.endsWith(`/pulls/${pr}`)) poll = Math.ceil(++pullReads / 2);
      if (endpoint.includes('/attempts/')) return [
        { jobs: [{ id: 1, name: 'first-page', status: 'completed', conclusion: 'success' }] },
        { jobs: [{ id: 2, name: 'second-page', status: poll === 1 ? 'queued' : 'completed', conclusion: poll === 1 ? null : 'failure' }] },
      ];
      return baseline(endpoint);
    }, onUpdate: value => saved.push(structuredClone(value)) });
  assert.equal(saved.length, 2);
  assert.equal(saved[0].runs[0].jobs[1].status, 'queued');
  assert.equal(report.runs[0].jobs[1].conclusion, 'failure');
  assert.equal(report.observation.status, 'ok');
});

test('zero checks are not a terminal pass; timeout and cancellation keep honest stale outcomes', async () => {
  const empty = observer([{}], { timeoutMs: 25, intervalMs: 5 });
  const timeout = await empty.result;
  assert.equal(timeout.observation.status, 'timeout');
  assert.equal(timeout.stale, true);
  assert.equal(timeout.checks.length, 0);
  assert.equal(empty.messages.length, 2);
  assert.equal(timeout.history.at(-1).type, 'timeout');
  const cancellation = new AbortController();
  const cancelled = observer([{ runs: [run()] }], { signal: cancellation.signal,
    onUpdate: () => cancellation.abort() });
  const report = await cancelled.result;
  assert.equal(report.observation.status, 'cancelled');
  assert.equal(report.runs[0].status, 'in_progress');
  assert.equal(report.stale, true);
  const firstError = await observeCi({ repo, pr, api: scenarioApi([{ error: true }]) });
  assert.equal(firstError.headSha, null);
  assert.equal(firstError.lastSuccessfulAt, null);
  assert.equal(firstError.observation.status, 'error');
});

async function fakeGhFixture() {
  const directory = await fs.mkdtemp(path.join(tmpdir(), 'ci-observer-'));
  const fixture = path.join(directory, 'fixture.json');
  const log = path.join(directory, 'calls.jsonl');
  const fake = path.join(directory, 'gh');
  await fs.writeFile(fake, `#!/usr/bin/env node
const fs = require('node:fs');
const args = process.argv.slice(2);
const data = JSON.parse(fs.readFileSync(process.env.CI_OBSERVER_FIXTURE, 'utf8'));
fs.appendFileSync(process.env.CI_OBSERVER_CALLS, JSON.stringify({args,pid:process.pid})+'\\n');
if(args.includes('--slurp')) { process.stderr.write('unknown flag: --slurp\\n'); process.exit(1); }
if(!args.includes('--paginate') || args[args.indexOf('--jq')+1] !== '@json') { process.stderr.write('expected built-in JSON page output\\n'); process.exit(1); }
if(data.stall) { setInterval(()=>{},1000); }
else {
  const endpoint=args.at(-1);
  const emit=pages=>{
    const output=pages.map(page=>JSON.stringify(page)).join('\\n')+'\\n';
    if(data.splitUnicode && output.includes('资产')) {
      const bytes=Buffer.from(output);
      const positions=['资产','🧪'].map(value=>bytes.indexOf(Buffer.from(value))).filter(value=>value>=0).map(value=>value+1).sort((a,b)=>a-b);
      process.stdout.write(bytes.subarray(0,positions[0]));
      positions.forEach((position,index)=>setTimeout(()=>process.stdout.write(bytes.subarray(position,positions[index+1])),30*(index+1)));
    } else process.stdout.write(output);
  };
  if(endpoint.includes('/pulls/')) emit([{head:{sha:${JSON.stringify(sha)}},state:'open'}]);
  else if(endpoint.includes('/actions/runs?')) emit([{workflow_runs:[]}]);
  else if(endpoint.includes('/check-runs?')) emit(data.checkPages ?? [{check_runs:[]}]);
  else if(endpoint.includes('/status?')) emit(data.statusPages ?? [{statuses:[]}]);
  else process.exitCode=1;
}
`, { mode: 0o700 });
  await fs.writeFile(fixture, '{}');
  return { directory, fixture, log, env: { ...process.env, PATH: `${directory}${path.delimiter}${process.env.PATH}`,
    CI_OBSERVER_FIXTURE: fixture, CI_OBSERVER_CALLS: log } };
}

test('actual CLI uses explicit read-only repo endpoints and atomically owns its output', async () => {
  const fixture = await fakeGhFixture();
  const output = path.join(fixture.directory, 'ci.json');
  try {
    const result = spawnSync(process.execPath, [script, '--repo', repo, '--pr', String(pr), '--output', output],
      { env: fixture.env, encoding: 'utf8', timeout: 5000 });
    assert.equal(result.status, 0, result.stderr);
    const report = JSON.parse(await fs.readFile(output, 'utf8'));
    assert.equal(report.observation.status, 'ok');
    assert.equal(report.source.repo, repo);
    assert.equal(report.source.pr, pr);
    assert.equal(report.headSha, sha);
    assert.equal(report.checks.length, 0);
    const calls = (await fs.readFile(fixture.log, 'utf8')).trim().split('\n').map(JSON.parse);
    assert.equal(calls.length, 5);
    assert.ok(calls.every(call => call.args.includes('GET') && call.args.includes('github.com')));
    assert.ok(calls.every(call => !call.args.includes('--slurp') && call.args[call.args.indexOf('--jq') + 1] === '@json'));
    assert.ok(calls.every(call => call.args.at(-1).startsWith(`repos/${repo}/`)));
    assert.deepEqual((await fs.readdir(fixture.directory)).filter(name => name.includes('.lock') || name.endsWith('.tmp')), []);
    await fs.writeFile(output, 'human-authored-input');
    const collision = spawnSync(process.execPath, [script, '--repo', repo, '--pr', String(pr), '--output', output],
      { env: fixture.env, encoding: 'utf8', timeout: 5000 });
    assert.equal(collision.status, 1);
    assert.equal(await fs.readFile(output, 'utf8'), 'human-authored-input');
    const linked = path.join(fixture.directory, 'linked.json');
    await fs.symlink(output, linked);
    const symlink = spawnSync(process.execPath, [script, '--repo', repo, '--pr', String(pr), '--output', linked],
      { env: fixture.env, encoding: 'utf8', timeout: 5000 });
    assert.equal(symlink.status, 1);
    assert.equal(await fs.readFile(output, 'utf8'), 'human-authored-input');
    const alias = path.join(fixture.directory, 'directory-alias');
    await fs.symlink(fixture.directory, alias);
    const locked = path.join(fixture.directory, 'locked.json');
    await fs.writeFile(`${locked}.lock`, '{"ownerId":"another-owner","pid":123}');
    const lockCollision = spawnSync(process.execPath, [script, '--repo', repo, '--pr', String(pr),
      '--output', path.join(alias, 'locked.json')], { env: fixture.env, encoding: 'utf8', timeout: 5000 });
    assert.equal(lockCollision.status, 1);
    assert.equal((JSON.parse(await fs.readFile(`${locked}.lock`, 'utf8'))).ownerId, 'another-owner');
  } finally { await fs.rm(fixture.directory, { recursive: true, force: true }); }
});

test('actual CLI parses every JSONL API page with an older gh flag surface', async () => {
  const fixture = await fakeGhFixture();
  const output = path.join(fixture.directory, 'ci.json');
  try {
    await fs.writeFile(fixture.fixture, JSON.stringify({ statusPages: [
      { statuses: [{ id: 3, context: 'deploy', state: 'success', target_url: 'https://ci.example/current' }] },
      { statuses: [{ id: 2, context: 'deploy', state: 'failure', target_url: 'https://ci.example/old' },
        { id: 1, context: 'second-page', state: 'failure', target_url: 'https://ci.example/second' }] },
    ] }));
    const result = spawnSync(process.execPath, [script, '--repo', repo, '--pr', String(pr), '--output', output],
      { env: fixture.env, encoding: 'utf8', timeout: 5000 });
    assert.equal(result.status, 0, result.stderr);
    const report = JSON.parse(await fs.readFile(output, 'utf8'));
    assert.equal(report.observation.status, 'ok');
    assert.equal(report.checks.length, 2);
    assert.equal(report.checks.find(item => item.name === 'deploy').conclusion, 'success');
    assert.equal(report.checks.find(item => item.name === 'second-page').url, 'https://ci.example/second');
  } finally { await fs.rm(fixture.directory, { recursive: true, force: true }); }
});

test('actual CLI preserves UTF-8 CI names split within a character across stdout chunks', async () => {
  const fixture = await fakeGhFixture();
  const output = path.join(fixture.directory, 'ci.json');
  const name = '资产 🧪 检查';
  try {
    await fs.writeFile(fixture.fixture, JSON.stringify({ splitUnicode: true,
      checkPages: [{ check_runs: [{ ...check({ status: 'completed', conclusion: 'success' }), name }] }] }));
    const result = spawnSync(process.execPath, [script, '--repo', repo, '--pr', String(pr), '--output', output],
      { env: fixture.env, encoding: 'utf8', timeout: 5000 });
    assert.equal(result.status, 0, result.stderr);
    const report = JSON.parse(await fs.readFile(output, 'utf8'));
    assert.equal(report.checks[0].name, name);
    assert.equal(report.history[0].checks[0].name, name);
    assert.ok(result.stdout.includes(name));
  } finally { await fs.rm(fixture.directory, { recursive: true, force: true }); }
});

test('actual CLI timeout and SIGTERM stop in-flight gh without a lock or temp orphan', async () => {
  for (const kind of ['timeout', 'cancelled']) {
    const fixture = await fakeGhFixture();
    const output = path.join(fixture.directory, 'ci.json');
    let child;
    try {
      await fs.writeFile(fixture.fixture, '{"stall":true}');
      child = spawn(process.execPath, [script, '--repo', repo, '--pr', String(pr), '--output', output,
        '--timeout-ms', kind === 'timeout' ? '400' : '3000'], { env: fixture.env, stdio: ['ignore', 'pipe', 'pipe'] });
      const done = new Promise((resolve, reject) => {
        child.once('error', reject);
        child.once('exit', code => resolve(code));
      });
      if (kind === 'cancelled') {
        for (let tries = 0; tries < 100; tries++) {
          if (await fs.stat(fixture.log).catch(() => null)) break;
          await new Promise(resolve => setTimeout(resolve, 10));
        }
        child.kill('SIGTERM');
      }
      assert.equal(await done, kind === 'timeout' ? 124 : 130);
      const report = JSON.parse(await fs.readFile(output, 'utf8'));
      assert.equal(report.observation.status, kind);
      assert.equal(report.lastSuccessfulAt, null);
      assert.equal(report.stale, true);
      const calls = (await fs.readFile(fixture.log, 'utf8')).trim().split('\n').map(JSON.parse);
      for (const call of calls) assert.throws(() => process.kill(call.pid, 0), error => error.code === 'ESRCH');
      assert.deepEqual((await fs.readdir(fixture.directory)).filter(name => name.includes('.lock') || name.endsWith('.tmp')), []);
    } finally {
      child?.kill('SIGKILL');
      await fs.rm(fixture.directory, { recursive: true, force: true });
    }
  }
});
