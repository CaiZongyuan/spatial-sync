import { useContext, useState } from 'react';
import { Link } from '@tanstack/react-router';
import { AlertContext, acknowledgeAlert, reading, type AlertEpisode } from '../lib/live';

export function Severity({ severity }: { severity: string }) {
  return <span className={`live-chip severity-${severity}`}><i />{severity}</span>;
}

export function AlertAcknowledgement({ alert }: { alert: AlertEpisode }) {
  const [open, setOpen] = useState(false), [note, setNote] = useState(''), [error, setError] = useState(''), [pending, setPending] = useState(false);
  const [saved, setSaved] = useState<AlertEpisode>();
  const current = saved?.alertId === alert.alertId && !alert.acknowledgement ? saved : alert;
  if (current.acknowledgement) return <div className="alert-audit"><span>已确认 · {current.acknowledgement.actor.kind}:{current.acknowledgement.actor.name}</span>
    <q>{current.acknowledgement.note || '未填写备注'}</q></div>;
  if (!current.active) return <span className="live-muted">{current.closedContext ? `上下文结束 · ${current.closedContext.reason}` : '已恢复'}</span>;
  return open ? <form className="alert-ack-form" onSubmit={event => {
    event.preventDefault(); setPending(true); setError('');
    void acknowledgeAlert(alert.alertId, note).then(value => { setSaved(value); setOpen(false); })
      .catch((error: Error) => setError(error.message)).finally(() => setPending(false));
  }}>
    <label>确认备注<textarea aria-label="确认备注" maxLength={4000} value={note} onChange={event => setNote(event.target.value)} /></label>
    <div><button type="submit" disabled={pending}>{pending ? '提交中…' : '提交确认'}</button><button type="button" disabled={pending} onClick={() => { setOpen(false); setNote(''); setError(''); }}>取消</button></div>
    {error && <span role="alert">{error}</span>}
  </form> : <button className="alert-ack-button" onClick={() => setOpen(true)}>确认</button>;
}

export function CriticalAlertBanner() {
  const { critical: alert } = useContext(AlertContext);
  if (!alert) return null;
  return <section className="critical-alert-banner" role="alert" aria-label="Critical 告警" data-testid="critical-alert-banner">
    <i className={`alert-pulse ${alert.acknowledgement ? 'is-acknowledged' : ''}`} aria-hidden="true" />
    <div className="critical-alert-copy"><div><code>{alert.entityKey}</code><strong>触发值 {reading(alert.value)}</strong><Severity severity="critical" /></div>
      <p>{alert.message} · {alert.rule} · 自 {reading(alert.since)} s 起</p></div>
    <AlertAcknowledgement key={alert.alertId} alert={alert} /><Link to="/live" className="reference-link">查看实况 →</Link>
  </section>;
}
