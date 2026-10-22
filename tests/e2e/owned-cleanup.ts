import { execFile, type ChildProcess } from 'node:child_process';
import { writeFile } from 'node:fs/promises';
import { createConnection } from 'node:net';
import { promisify } from 'node:util';

export type IdentityResult = { identity: string } | { error: unknown };
export interface OwnedProcess {
  child: ChildProcess; label: string; output: string; exited: Promise<void>;
  identity: Promise<IdentityResult>; error?: Error;
}
interface Failure { stage: string; message: string; stack?: string; stdout?: string; stderr?: string }
interface ProcessReceipt {
  label: string; pid?: number; identity?: string; identityStatus: 'known' | 'unknown';
  cleanup?: unknown; stopped: boolean; errors: Failure[]; exitCode?: number | null; signalCode?: string | null;
}
export interface CleanupReport {
  processes: ProcessReceipt[]; ports: { port: number; closed?: boolean; error?: Failure }[];
  errors: Failure[]; original: Failure[];
}
interface Options {
  python: string; script: string; outputPath: (name: string) => string; context: object;
  prepare: { label: string; run: () => Promise<unknown> }[]; ports: number[];
  original: unknown[];
  execute?: (python: string, args: string[]) => Promise<{ stdout: string; stderr?: string }>;
  write?: (path: string, content: string) => Promise<unknown>;
  portClosed?: (port: number) => Promise<boolean>;
}
const execute = promisify(execFile);
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
const failure = (stage: string, value: unknown): Failure => {
  const error = value as Error & { stdout?: string; stderr?: string };
  return { stage, message: error?.message ?? String(value), stack: error?.stack,
    stdout: error?.stdout, stderr: error?.stderr };
};

async function closed(port: number) {
  return new Promise<boolean>(resolve => {
    const connection = createConnection({ host: '127.0.0.1', port });
    connection.once('connect', () => { connection.destroy(); resolve(false); });
    connection.once('error', () => resolve(true));
    connection.setTimeout(1000, () => { connection.destroy(); resolve(false); });
  });
}

/** One owner's failure must not prevent attempts or receipts for another owner. */
export async function cleanupOwnedProcesses(records: OwnedProcess[], options: Options): Promise<CleanupReport> {
  const report: CleanupReport = { processes: [], ports: [], errors: [],
    original: options.original.map(error => failure('journey', error)) };
  const write = options.write ?? writeFile, run = options.execute ?? execute;
  const attempt = async (stage: string, operation: () => Promise<unknown>, owner?: ProcessReceipt) => {
    try { return await operation(); }
    catch (error) {
      const entry = failure(stage, error); report.errors.push(entry); owner?.errors.push(entry);
      return undefined;
    }
  };
  for (const job of options.prepare) await attempt(job.label, job.run);
  for (const record of [...records].reverse()) {
    const owner: ProcessReceipt = { label: record.label, pid: record.child.pid,
      identityStatus: 'unknown', stopped: false, errors: [] };
    report.processes.push(owner);
    await attempt(`${record.label}:identity`, async () => {
      const result = await record.identity;
      if ('error' in result) throw result.error;
      owner.identity = result.identity; owner.identityStatus = 'known';
    }, owner);
    if (owner.identity !== undefined) await attempt(`${record.label}:stop`, async () => {
      const result = await run(options.python, [options.script, 'stop', owner.identity!]);
      owner.cleanup = JSON.parse(result.stdout);
    }, owner);
    // Only this task's current creating handle is used when identity/helper work
    // failed. Unknown descendants stay unknown in the receipt and listener result.
    await attempt(`${record.label}:fallback`, async () => {
      if (record.child.exitCode === null && record.child.signalCode === null) record.child.kill('SIGTERM');
      owner.stopped = await Promise.race([record.exited.then(() => true), delay(3000).then(() => false)]);
      if (!owner.stopped) {
        record.child.kill('SIGKILL');
        owner.stopped = await Promise.race([record.exited.then(() => true), delay(3000).then(() => false)]);
      }
      if (!owner.stopped) throw new Error('Owned creating handle did not exit');
    }, owner);
    owner.exitCode = record.child.exitCode; owner.signalCode = record.child.signalCode;
    await attempt(`${record.label}:log`, () => write(options.outputPath(`${record.label}.log`), record.output), owner);
    await attempt(`${record.label}:receipt`, () => write(options.outputPath(`${record.label}-receipt.json`),
      JSON.stringify({ ...options.context, ...owner })), owner);
  }
  for (const port of options.ports) {
    const result: CleanupReport['ports'][number] = { port }; report.ports.push(result);
    await attempt(`port:${port}`, async () => {
      try { result.closed = await (options.portClosed ?? closed)(port); }
      catch (error) { result.error = failure(`port:${port}`, error); throw error; }
      if (!result.closed) throw new Error('Owned listener remains open');
    });
  }
  await attempt('cleanup:receipt', () => write(options.outputPath('cleanup-receipt.json'), JSON.stringify(report)));
  return report;
}

export function finishCleanup(report: CleanupReport, original: unknown[]) {
  const errors = [...original, ...report.errors.map(value => new Error(`${value.stage}: ${value.message}`))];
  if (errors.length === 1 && original.length === 1) throw original[0];
  if (errors.length) throw new AggregateError(errors, 'Browser journey or owned cleanup failed');
}
