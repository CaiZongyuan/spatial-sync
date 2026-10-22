import type { ChildProcess } from 'node:child_process';
import { expect, it, vi } from 'vitest';
import { cleanupOwnedProcesses, finishCleanup, type IdentityResult, type OwnedProcess } from '../../../../tests/e2e/owned-cleanup';

it.each(['snapshot', 'stop'])('continues both owned cleanups and records listeners after the first %s failure', async mode => {
  const business = new Error('Original business assertion');
  const unknown = [{ pid: 123, created: 50, command: ['owned-web'], alive: true }];
  const helperError = Object.assign(new Error('Injected helper failure'), { stdout: JSON.stringify({ identities: unknown, alive: [123] }), stderr: 'diagnostic' });
  const make = (label: string, identity: IdentityResult): OwnedProcess => ({ label, output: `${label} log`,
    child: { pid: label === 'api' ? 1 : 2, exitCode: null, signalCode: null, kill: vi.fn(() => true) } as unknown as ChildProcess,
    exited: Promise.resolve(), identity: Promise.resolve(identity) });
  const api = make('api', { identity: 'api-identity' });
  const web = make('web', mode === 'snapshot' ? { error: helperError } : { identity: 'web-identity' });
  const execute = vi.fn(async (_python: string, args: string[]) => {
    if (args[2] === 'web-identity') throw helperError;
    return { stdout: JSON.stringify({ identities: ['api-known'], alive: [] }) };
  });
  const written = new Map<string, string>();
  const portClosed = vi.fn(async (port: number) => port === 8000);
  const report = await cleanupOwnedProcesses([api, web], { python: 'python', script: 'owned.py',
    outputPath: name => name, context: {}, prepare: [], ports: [8000, 5000], execute, original: [business],
    write: async (name, body) => { written.set(name, body); }, portClosed });
  expect(execute).toHaveBeenCalledWith('python', ['owned.py', 'stop', 'api-identity']);
  expect(web.child.kill).toHaveBeenCalledWith('SIGTERM');
  expect(api.child.kill).toHaveBeenCalledWith('SIGTERM');
  expect(report.processes.map(value => value.label)).toEqual(['web', 'api']);
  expect(report.processes[0]).toMatchObject({ identityStatus: mode === 'snapshot' ? 'unknown' : 'known',
    stopped: true, errors: [{ stdout: helperError.stdout, stderr: 'diagnostic' }] });
  expect(written.has('web-receipt.json')).toBe(true); expect(written.has('api-receipt.json')).toBe(true);
  expect(portClosed.mock.calls).toEqual([[8000], [5000]]);
  expect(JSON.parse(written.get('cleanup-receipt.json')!)).toMatchObject({ original: [
    { stage: 'journey', message: business.message, stack: business.stack },
  ], ports: [
    { port: 8000, closed: true }, { port: 5000, closed: false },
  ] });
  try { finishCleanup(report, [business]); throw new Error('Expected aggregate failure'); }
  catch (error) {
    expect(error).toBeInstanceOf(AggregateError);
    expect((error as AggregateError).errors[0]).toBe(business);
    expect((error as AggregateError).errors).toHaveLength(3);
  }
});
