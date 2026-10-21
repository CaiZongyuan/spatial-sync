import { useSyncExternalStore, type ReactNode } from 'react';
import type { SessionClient } from '../lib/stream/client';
import { EntityPanel } from './EntityPanel';
import { InvocationsPanel } from './InvocationsPanel';
import '../information-replay.css';

/** Published Live recordings contain device information without a spatial stream. */
export function InformationReplay({ client, simTime, children }: {
  client: SessionClient; simTime: number; children: ReactNode;
}) {
  const metadata = useSyncExternalStore(client.info.subscribeMetadata, client.info.getMetadata);
  const catalog = metadata.catalog;
  return <div className="information-replay" aria-label="Live information replay">
    <section className="information-replay-devices">
      <header><span className="eyebrow">LIVE · 录制回放</span><h1>实验室设备</h1>
        <p>查看录制中的读数与调用。设备操作和审批均为只读。</p></header>
      {!catalog ? <p role="status">正在读取设备目录…</p> :
        <div className="information-replay-grid">{Object.values(catalog.entities).map(binding =>
          <article key={binding.key} data-testid={`replay-device-${binding.key}`}>
            <EntityPanel store={client.info} binding={binding} definition={catalog.types[binding.type]}
              disabled disabledReason="录制回放为只读。"
              onAction={(key, action, params) => client.action(key, action, params)} />
          </article>)}</div>}
      <InvocationsPanel store={client.info} selected="" simTime={simTime} disabled
        onCancel={id => client.cancelInvocation(id)} />
    </section>
    <aside className="information-replay-plans">{children}</aside>
  </div>;
}
