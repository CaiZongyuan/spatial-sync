import type { SceneManifest } from '../lib/protocol';
import type { SessionClient, ViewState } from '../lib/stream/client';
import type { RenderMetrics } from './viewer/SceneViewport';

function ValueGrid({ items }: { items: [string, React.ReactNode][] }) {
  return (
    <dl className="diagnostic-values">
      {items.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value ?? '—'}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Diagnostics({
  client,
  state,
  metrics,
  warnings,
}: {
  client: SessionClient;
  state: ViewState;
  metrics: RenderMetrics;
  warnings: string[];
}) {
  const manifest = state.manifest!;
  const source = manifest.source as SceneManifest['source'] &
    Record<string, unknown>;
  const stats = state.stats;
  const gatewayClients = (
    Array.isArray(stats.clients) ? stats.clients : []
  ) as {
    subscribed?: boolean;
    grantedRateHz?: number;
    reliableQueueDepth?: number;
    droppedFrames?: number;
  }[];
  const solveMs = stats.solver_ms ?? stats.solverMs;
  const readbackMs = stats.readback_ms ?? stats.readbackMs;
  return (
    <div className="diagnostics-view">
      <div className="diagnostics-heading">
        <span className="eyebrow">OBSERVABILITY</span>
        <h2>Understand the whole pipeline.</h2>
        <p>
          Measured values from the source, gateway, and this browser. Unreported
          metrics remain empty.
        </p>
      </div>
      <div className="diagnostic-grid">
        <section className="diagnostic-card">
          <span className="card-number">01 / SOURCE</span>
          <h3>
            {source.kind === 'newton' ? 'Newton runtime' : 'Synthetic source'}
          </h3>
          <ValueGrid
            items={[
              ['Solver', source.solver as string],
              ['physics_dt', `${source.physicsDt.toFixed(6)} s`],
              ['simTime', `${state.simTime.toFixed(4)} s`],
              ['Target RTF', state.realtimeFactor ?? 'Unlimited'],
              ['Actual RTF', state.actualRtf.toFixed(3)],
              [
                'Observer policy',
                typeof stats.continueWithoutObservers === 'boolean'
                  ? stats.continueWithoutObservers
                    ? 'Keep running'
                    : 'Pause without subscribers'
                  : undefined,
              ],
              [
                'Automatic pause',
                typeof stats.autoPaused === 'boolean'
                  ? stats.autoPaused
                    ? 'Active'
                    : 'Inactive'
                  : undefined,
              ],
              ['Step FPS', (stats.stepHz ?? stats.stepFps) as number],
              ['Solve', solveMs == null ? undefined : `${solveMs} ms`],
              ['Readback', readbackMs == null ? undefined : `${readbackMs} ms`],
              ['Control tick', typeof stats.tick_ms === 'number' ? `${stats.tick_ms} ms` : undefined],
              ['Control tick p95', typeof stats.tickP95Ms === 'number' ? `${stats.tickP95Ms} ms` : undefined],
              ['Control ticks', typeof stats.controlTickCount === 'number' ? stats.controlTickCount : undefined],
              ['Readback mode', stats.readbackMode as string],
              ['Solve timing', stats.solverTimingMode as string],
              [
                'Publish work',
                stats.publish_ms == null ? undefined : `${stats.publish_ms} ms`,
              ],
              [
                'Bodies / Sites',
                `${manifest.nodes.filter((node) => node.kind === 'body').length} / ${manifest.sites.length}`,
              ],
              ['Shapes', source.shapeCount],
              ['Readback bodies', stats.readbackBodyCount as number],
              [
                'Readback bytes',
                typeof stats.readbackBytes === 'number'
                  ? `${stats.readbackBytes} B`
                  : undefined,
              ],
              [
                'Unlabeled bodies',
                manifest.nodes.filter(
                  (node) => node.kind === 'body' && !node.stableKey,
                ).length,
              ],
            ]}
          />
        </section>
        <section className="diagnostic-card">
          <span className="card-number">02 / GATEWAY & NETWORK</span>
          <h3>Transform delivery</h3>
          <ValueGrid
            items={[
              [
                'Base / granted rate',
                `${source.basePublishHz} / ${client.buffer.grantedRateHz} Hz`,
              ],
              ['Received rate', `${state.receivedRateHz} Hz`],
              [
                'Actual publish rate',
                state.publishedRateHz == null
                  ? undefined
                  : `${state.publishedRateHz.toFixed(1)} Hz`,
              ],
              ['Published frames', stats.publishedFrames as number],
              ['Received frames', state.receivedFrames],
              ['Frame format', 'binary'],
              ['Malformed binary frames', client.buffer.invalidBinaryFrames],
              [
                'Received payload',
                `${(state.payloadBytes / 1024).toFixed(1)} KiB`,
              ],
              [
                'Dropped client frames',
                gatewayClients.reduce(
                  (sum, item) => sum + (item.droppedFrames ?? 0),
                  0,
                ),
              ],
              [
                'Subscribed fanout',
                gatewayClients.filter((item) => item.subscribed).length,
              ],
              [
                'Reliable queue depth',
                gatewayClients.reduce(
                  (sum, item) => sum + (item.reliableQueueDepth ?? 0),
                  0,
                ),
              ],
              [
                'Client granted rates',
                gatewayClients
                  .map((item) => item.grantedRateHz ?? '—')
                  .join(', ') || '—',
              ],
              [
                'Encoded payload',
                typeof stats.encodedPayloadBytes === 'number'
                  ? `${(stats.encodedPayloadBytes / 1024).toFixed(1)} KiB`
                  : undefined,
              ],
              [
                'Runner heartbeat',
                typeof stats.runnerHeartbeatMs === 'number' &&
                typeof stats.runnerHeartbeatSeq === 'number'
                  ? `${stats.runnerHeartbeatAlive === false ? 'Unavailable · ' : ''}${Math.round(stats.runnerHeartbeatMs)} ms ago`
                  : undefined,
              ],
              [
                'Gateway heartbeat',
                state.gatewayHeartbeatMs === undefined
                  ? undefined
                  : `${Math.round(state.gatewayHeartbeatMs)} ms ago`,
              ],
              ['Clock offset', `${client.buffer.clockOffset.toFixed(1)} ms`],
              ['Playout delay', `${client.buffer.playoutDelay.toFixed(1)} ms`],
              ['Rejected stale frames', client.buffer.discardedFrames],
              ['Discontinuities', client.buffer.discontinuities],
            ]}
          />
        </section>
        <section className="diagnostic-card">
          <span className="card-number">03 / THIS BROWSER</span>
          <h3>Render health</h3>
          <ValueGrid
            items={[
              ['Render FPS', metrics.fps.toFixed(1)],
              ['Draw calls', metrics.drawCalls],
              ['Scene objects', metrics.objects],
              ['Transform apply', `${metrics.applyMs.toFixed(3)} ms`],
              [
                'Frame age',
                state.sessionState === 'RUNNING'
                  ? `${Math.max(0, performance.now() - client.buffer.receivedAt).toFixed(0)} ms`
                  : 'Paused · no frame-age alarm',
              ],
              ['Scene revision', manifest.sceneRevision],
              [
                'Epoch / sequence',
                `${client.buffer.epoch} / ${client.buffer.lastSeq}`,
              ],
              ['Connection', state.connection],
              ['Session state', state.sessionState],
              ['Skipped non-finite poses', client.buffer.skippedNonFinite],
            ]}
          />
        </section>
      </div>
      <section className="warnings-card">
        <div className="panel-heading">
          <h3>Compatibility & asset warnings</h3>
          <span>{manifest.warnings.length + warnings.length}</span>
        </div>
        {manifest.warnings.length + warnings.length ? (
          <ul>
            {manifest.warnings.map((warning, i) => (
              <li key={`manifest-${i}`}>
                <code>{warning.code}</code> {warning.message}
                {warning.entityKey && <small>{warning.entityKey}</small>}
              </li>
            ))}
            {warnings.map((warning) => (
              <li key={warning}>
                <code>asset_fallback</code> {warning}
              </li>
            ))}
          </ul>
        ) : (
          <p>All scene entities have compatible visuals.</p>
        )}
      </section>
    </div>
  );
}
