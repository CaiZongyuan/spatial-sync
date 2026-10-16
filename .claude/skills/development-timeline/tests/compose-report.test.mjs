import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm, symlink, link } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { composeFile, normalizeCI } from '../scripts/compose-report.mjs';

const cli = fileURLToPath(new URL('../scripts/compose-report.mjs', import.meta.url));
const at = minute => `2026-10-06T10:${String(minute).padStart(2, '0')}:00.000Z`;
const head = 'a'.repeat(40), oldHead = 'b'.repeat(40);
const report = overrides => ({ project: 'Workflow test', title: 'Delivery facts', snapshotAt: at(40),
  task: { status: 'in-progress', goal: 'Keep explicit PM decisions', startedAt: at(0) },
  scope: { platform: 'desktop-web', mobile: false }, events: [], tickets: [],
  checkpoint: { authorization: 'Local preview only.\nDo not publish.', roles: [{ role: 'Developer', owner: 'Agent A' }], revision: head,
    next: { action: 'Ask user to inspect preview', timing: 'Unknown' } }, ...overrides });
const ci = overrides => ({ schemaVersion: 1, source: { repo: 'Owner/repo', pr: 7, url: 'https://github.com/Owner/repo/pull/7' },
  headSha: head, observedAt: at(30), lastSuccessfulAt: at(29), stale: false, observation: { status: 'ok' },
  pullRequest: { state: 'OPEN', mergedAt: null, mergeSha: null },
  runs: [{ id: 17, name: 'Verification', headSha: head, attempt: 2, status: 'completed', conclusion: 'success',
    url: 'https://github.com/Owner/repo/actions/runs/17', startedAt: at(10), completedAt: at(20),
    jobs: [{ id: 19, name: 'Behavior tests', status: 'completed', conclusion: 'success', startedAt: at(12), completedAt: at(19) }] }],
  checks: [{ name: 'Public behavior', headSha: head, status: 'completed', conclusion: 'success', source: 'check-run' }],
  history: [{ type: 'snapshot', at: at(29), headSha: head, observationStatus: 'ok' }], ...overrides });
function data(html) {
  return JSON.parse(html.match(/<script id="report-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
}
async function fixture(work) {
  const root = await mkdtemp(join(tmpdir(), 'compose-report-'));
  try { await work(root); } finally { await rm(root, { recursive: true, force: true }); }
}
async function writeSources(root, input = report(), snapshot = ci()) {
  const paths = { input: join(root, 'report.json'), ci: join(root, 'ci.json'),
    current: join(root, 'checkpoint', 'current.md'), output: join(root, 'published', 'timeline.html') };
  await writeFile(paths.input, JSON.stringify(input));
  if (snapshot) await writeFile(paths.ci, JSON.stringify(snapshot)); else delete paths.ci;
  return paths;
}

// Exercise the installed CLI with the real timeline renderer, not a duplicate render stub.
test('CLI renders both views from one model and never infers completion from green CI', async () => {
  await fixture(async root => {
    const manual = report(), paths = await writeSources(root, manual);
    const before = [await readFile(paths.input, 'utf8'), await readFile(paths.ci, 'utf8')];
    const result = spawnSync(process.execPath, [cli, '--input', paths.input, '--ci', paths.ci, '--current', paths.current, '--output', paths.output], { encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(JSON.parse(result.stdout), { current: paths.current, output: paths.output });
    const html = await readFile(paths.output, 'utf8'), current = await readFile(paths.current, 'utf8'), model = data(html);
    assert.equal(model.task.status, 'in-progress');
    assert.deepEqual(model.checkpoint, manual.checkpoint);
    assert.equal(model.ci.assessment.relation, 'current');
    assert.equal(model.ci.runs[0].attempt, 2);
    assert.match(current, /Task status \(PM supplied\): in-progress/);
    for (const text of ['Owner/repo', head, 'attempt 2', 'Verification', 'completed / success', 'Local preview only.', 'Do not publish.', 'Unknown']) assert.ok(current.includes(text), text);
    assert.match(current, /Runner started: 2026-10-06T10:10:00.000Z; finished: 2026-10-06T10:20:00.000Z/);
    assert.match(current, /Observed at: 2026-10-06T10:30:00.000Z/);
    assert.match(html, /id="ci-panel"/);
    assert.match(html, /Current observation:/);
    assert.match(html, /Manual checkpoint/);
    assert.equal(await readFile(paths.input, 'utf8'), before[0]);
    assert.equal(await readFile(paths.ci, 'utf8'), before[1]);
  });
});

test('observation error preserves successful run facts while both views report stale evidence', async () => {
  await fixture(async root => {
    const snapshot = ci({ observedAt: at(35), stale: true, observation: { status: 'error', error: { code: 'FETCH', message: 'Rate limit exhausted' } } });
    const paths = await writeSources(root, report(), snapshot);
    await composeFile(paths);
    const current = await readFile(paths.current, 'utf8'), model = data(await readFile(paths.output, 'utf8'));
    assert.equal(model.ci.observation.status, 'error');
    assert.equal(model.ci.assessment.freshness, 'stale');
    assert.equal(model.ci.runs[0].conclusion, 'success');
    assert.equal(model.ci.runs[0].attempt, 2);
    assert.equal(model.ci.lastSuccessfulAt, at(29));
    assert.match(current, /Current observation: error; evidence freshness: stale/);
    assert.match(current, /Rate limit exhausted/);
    assert.match(current, /last known CI facts/);
    assert.match(current, /completed \/ success/);
    assert.match(current, /last successful observation: 2026-10-06T10:29:00.000Z/);
  });
});

test('old heads, uncomparable revisions, missing checks and first observation failure stay unproven', async () => {
  await fixture(async root => {
    const paths = await writeSources(root, report(), ci({ headSha: oldHead, runs: [], checks: [] }));
    await composeFile(paths);
    const model = data(await readFile(paths.output, 'utf8')), current = await readFile(paths.current, 'utf8');
    assert.equal(model.ci.assessment.relation, 'historical');
    assert.equal(model.ci.assessment.freshness, 'unknown');
    assert.match(current, /facts do not establish checks for the current candidate/);
    assert.match(current, /Required checks and passing status are unknown/);
    for (const revision of ['working-tree:dirty', head.slice(0, 7), undefined]) {
      const observation = normalizeCI(ci(), { revision });
      assert.equal(observation.assessment.relation, 'unknown');
      assert.equal(observation.assessment.freshness, 'unknown');
    }
    const observation = normalizeCI(ci({ headSha: null, lastSuccessfulAt: null, runs: [], checks: [], stale: true,
      pullRequest: null, observation: { status: 'timeout' } }), { revision: head });
    assert.equal(observation.assessment.relation, 'unknown');
    assert.equal(observation.assessment.freshness, 'stale');
    assert.equal(observation.lastSuccessfulAt, null);
  });
});

test('multiple journals retain their origins and links relocate independently for both outputs', async () => {
  await fixture(async root => {
    const paths = await writeSources(root, report(), null);
    const first = join(root, 'start.jsonl'), second = join(root, 'other', 'end.jsonl');
    await mkdir(join(root, 'other'));
    const start = { type: 'start', at: at(5), event: { id: 'developer-7', lane: 'Developer', kind: 'command', category: 'validation', label: 'Public behavior', start: at(5), evidence: [{ label: 'Start proof', href: 'proof/start.txt' }] } };
    const end = { type: 'end', eventId: 'developer-7', at: at(10), outcome: 'passed', evidence: [{ label: 'End proof', href: 'end.txt' }] };
    const contentA = JSON.stringify(start) + '\n', contentB = JSON.stringify(end) + '\n';
    await writeFile(first, contentA); await writeFile(second, contentB);
    const result = spawnSync(process.execPath, [cli, '--input', paths.input, '--events', first, '--events', second, '--current', paths.current, '--output', paths.output], { encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
    const model = data(await readFile(paths.output, 'utf8')), current = await readFile(paths.current, 'utf8');
    assert.equal(model.events[0].end, at(10));
    assert.deepEqual(model.events[0].evidence.map(item => item.href), ['../proof/start.txt', '../other/end.txt']);
    assert.match(current, /\[Start proof\]\(\.\.\/proof\/start.txt\)/);
    assert.match(current, /\[End proof\]\(\.\.\/other\/end.txt\)/);
    assert.equal(model.metrics.unionMs, 5 * 60_000);
    assert.equal(model.task.status, 'in-progress');
    assert.equal(await readFile(first, 'utf8'), contentA);
    assert.equal(await readFile(second, 'utf8'), contentB);
  });
});

test('output aliases reject report, journal, CI and each other without changing sources', async () => {
  await fixture(async root => {
    const paths = await writeSources(root);
    const journal = join(root, 'journal.jsonl'); await writeFile(journal, '');
    const originals = await Promise.all([paths.input, paths.ci, journal].map(path => readFile(path, 'utf8')));
    for (const [i, source] of [paths.input, paths.ci, journal].entries()) {
      for (const [type, make] of [['symlink', symlink], ['hardlink', link]]) {
        const alias = join(root, `${type}-${i}`); await make(source, alias);
        for (const destination of ['current', 'output']) await assert.rejects(composeFile({ ...paths, events: journal, [destination]: alias }), /must not overwrite/);
      }
      await assert.rejects(composeFile({ ...paths, events: journal, current: source }), /must not overwrite/);
    }
    const first = join(root, 'existing.md'), second = join(root, 'linked.html');
    await writeFile(first, 'Old checkpoint'); await link(first, second);
    await assert.rejects(composeFile({ ...paths, current: first, output: second }), /distinct files/);
    await assert.rejects(composeFile({ ...paths, current: paths.output }), /distinct files/);
    await mkdir(join(root, 'real')); await symlink(join(root, 'real'), join(root, 'via-link'));
    await assert.rejects(composeFile({ ...paths, current: join(root, 'real', 'same'), output: join(root, 'via-link', 'same') }), /distinct files/);
    assert.deepEqual(await Promise.all([paths.input, paths.ci, journal].map(path => readFile(path, 'utf8'))), originals);
    assert.equal(await readFile(first, 'utf8'), 'Old checkpoint');
  });
});

test('invalid second output fails before refreshing either artifact', async () => {
  await fixture(async root => {
    const paths = await writeSources(root);
    await mkdir(join(root, 'checkpoint')); await mkdir(paths.output, { recursive: true });
    await writeFile(paths.current, 'Existing checkpoint');
    const result = spawnSync(process.execPath, [cli, '--input', paths.input, '--ci', paths.ci, '--current', paths.current, '--output', paths.output], { encoding: 'utf8' });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /regular file/);
    assert.equal(await readFile(paths.current, 'utf8'), 'Existing checkpoint');
  });
});

test('without CI or checkpoint, existing reports keep optional timeline views', async () => {
  await fixture(async root => {
    const paths = await writeSources(root, report({ checkpoint: undefined }), null);
    await composeFile(paths);
    const model = data(await readFile(paths.output, 'utf8'));
    assert.equal(model.ci, undefined);
    assert.equal(model.checkpoint, undefined);
    assert.equal(model.task.status, 'in-progress');
    assert.equal(model.scope.platform, 'desktop-web');
  });
});

test('CLI Markdown destinations preserve parentheses, spaces and entity-like text with a real CommonMark parser', async t => {
  await fixture(async root => {
    const evidence = ['proof/a)b.log', 'proof/a(b.log', 'proof/a b).log', 'proof/a&copy;.log'];
    const paths = await writeSources(root, report({ events: [{ id: 'proof', lane: 'Developer', kind: 'point', category: 'validation',
      label: 'Saved proofs', start: at(20), evidence: evidence.map((href, index) => ({ href, label: `Proof ${index}` })) }] }),
      ci({ source: { repo: 'Owner/repo', pr: 7, url: 'https://ci.example/proof/a b).log' } }));
    const originals = [await readFile(paths.input, 'utf8'), await readFile(paths.ci, 'utf8')];
    const result = spawnSync(process.execPath, [cli, '--input', paths.input, '--ci', paths.ci, '--current', paths.current, '--output', paths.output], { encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
    const current = await readFile(paths.current, 'utf8'), model = data(await readFile(paths.output, 'utf8'));
    assert.match(current, /\[Proof 0\]\(\.\.\/proof\/a%29b.log\)/);
    assert.match(current, /\[Proof 1\]\(\.\.\/proof\/a%28b.log\)/);
    assert.match(current, /\[Proof 2\]\(\.\.\/proof\/a%20b%29.log\)/);
    assert.match(current, /\[CI source\]\(https:\/\/ci.example\/proof\/a%20b%29.log\)/);
    assert.deepEqual([await readFile(paths.input, 'utf8'), await readFile(paths.ci, 'utf8')], originals);
    // External parser is a validation aid, not a Node runtime/install dependency.
    const python = spawnSync('python3', ['-c', 'import json,sys\nfrom markdown_it import MarkdownIt\ntokens=MarkdownIt("commonmark").parse(sys.stdin.read())\nprint(json.dumps([c.attrGet("href") for t in tokens for c in (t.children or []) if c.type=="link_open"]))'], { input: current, encoding: 'utf8' });
    if (python.error?.code === 'ENOENT' || /No module named .markdown_it./.test(python.stderr)) { t.diagnostic('CommonMark parser check unavailable; CLI destination assertions still ran.'); return; }
    assert.equal(python.status, 0, python.stderr);
    const parsed = JSON.parse(python.stdout);
    assert.equal(parsed.length, 5);
    const expected = [model.ci.source.url, ...model.events[0].evidence.map(item => item.href)].map(href => decodeURIComponent(href));
    assert.deepEqual(parsed.map(href => decodeURIComponent(href)), expected);
  });
});

test('CLI removes sensitive CI URLs from both outputs and the embedded model without rewriting source JSON', async () => {
  await fixture(async root => {
    const secret = 'SYNTHETIC_SECRET', clean = 'https://github.com/Owner/repo/pull/7';
    const unsafe = [
      `https://ci.example/job?token=${secret}`,
      `https://${secret}:password@ci.example/job`,
      `https://ci.example/job#${secret}`,
      `https://ci.example/job?signature=${secret}#fragment`,
    ];
    for (const href of unsafe) {
      const snapshot = ci();
      snapshot.runs[0].url = href;
      snapshot.runs[0].jobs[0].url = href;
      snapshot.checks[0].url = href;
      snapshot.history[0].runs = [{ ...snapshot.runs[0], url: href }];
      const paths = await writeSources(root, report(), snapshot);
      const original = await readFile(paths.ci, 'utf8');
      const result = spawnSync(process.execPath, [cli, '--input', paths.input, '--ci', paths.ci, '--current', paths.current, '--output', paths.output], { encoding: 'utf8' });
      assert.equal(result.status, 0, result.stderr);
      const current = await readFile(paths.current, 'utf8'), html = await readFile(paths.output, 'utf8'), model = data(html);
      assert.equal(current.includes(secret), false);
      assert.equal(html.includes(secret), false);
      assert.equal(model.ci.runs[0].url, null);
      assert.equal(model.ci.runs[0].jobs[0].url, null);
      assert.equal(model.ci.checks[0].url, null);
      assert.equal(model.ci.source.url, clean);
      assert.equal(await readFile(paths.ci, 'utf8'), original);
      snapshot.source.url = href;
      await writeFile(paths.ci, JSON.stringify(snapshot));
      await composeFile(paths);
      assert.equal(data(await readFile(paths.output, 'utf8')).ci.source.url, null);
      assert.equal((await readFile(paths.current, 'utf8')).includes(secret), false);
    }
    for (const href of ['file:///tmp/proof', 'mailto:agent@example.com', '//ci.example/proof', 'proof/local.txt', 'https://ci.example/proof?', 'https://ci.example/proof#', 'https://@ci.example/proof', 'https://ci.example/proof\u0000']) {
      const model = normalizeCI(ci({ source: { repo: 'Owner/repo', pr: 7, url: href } }), { revision: head });
      assert.equal(model.source.url, null);
    }
  });
});
