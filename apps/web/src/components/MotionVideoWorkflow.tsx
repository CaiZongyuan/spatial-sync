import { Link } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import { request } from '../lib/api';
import { getJob, type TrainingJob } from '../lib/jobs';
import { jobReason, jobStatusLabels } from '../lib/training-presentation';
import { motionVersionPath, motionWrite, uploadMotionVideo, type MotionClip, type MotionVideo,
  type ReferenceDetail, type ReferencePin } from '../lib/motion';

const videoLimit = 16 * 1024 * 1024;

export function MotionVideoWorkflow({ asset, contentHash, onReference }: {
  asset: string; contentHash: string; onReference: (pin: ReferencePin) => void;
}) {
  const [file, setFile] = useState<File>();
  const [video, setVideo] = useState<MotionVideo>();
  const [job, setJob] = useState<TrainingJob>();
  const [clip, setClip] = useState<MotionClip>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [pollError, setPollError] = useState('');
  const [clipError, setClipError] = useState('');
  const [clipRead, setClipRead] = useState(0);
  const requestId = useRef<string | undefined>(undefined);
  const jobId = job?.jobId;
  const active = job?.status === 'queued' || job?.status === 'running';
  const clipId = job?.status === 'succeeded' ? job.motionClip?.id : undefined;
  const clipHash = job?.status === 'succeeded' ? job.motionClip?.sha256 : undefined;

  useEffect(() => {
    if (!jobId || !active) return;
    const identity = jobId;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const latest = await getJob(identity, controller.signal);
        if (controller.signal.aborted) return;
        setJob(latest);
        setPollError('');
      } catch (failure) {
        if (!controller.signal.aborted) setPollError((failure as Error).message);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 2000);
      }
    }
    timer = setTimeout(refresh, 2000);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [jobId, active]);

  useEffect(() => {
    if (!clipId || !clipHash || !video) return;
    const resultId = clipId;
    const source = video;
    const controller = new AbortController();
    setClipError('');
    async function readResult() {
      const value = await request<MotionClip>(motionVersionPath(resultId, 'motion-clip'), { signal: controller.signal });
      if (value.id !== resultId || value.sha256 !== clipHash || value.source.id !== source.id ||
          value.source.sha256 !== source.sha256) throw new Error('提取结果与固定源视频不符。');
      if (!controller.signal.aborted) setClip(value);
    }
    void readResult().catch((failure: Error) => { if (!controller.signal.aborted) setClipError(failure.message); });
    return () => controller.abort();
  }, [clipId, clipHash, video, clipRead]);

  function select(files: FileList | null) {
    if (busy || active || !files?.length) return;
    if (files.length !== 1 || !/\.(mp4|webm)$/i.test(files[0].name) || !files[0].size || files[0].size > videoLimit) {
      setError('请选择一个不超过 16 MiB 的 MP4 或 WebM。');
      return;
    }
    requestId.current = undefined;
    setFile(files[0]); setVideo(undefined); setJob(undefined); setClip(undefined); setError(''); setPollError('');
    setClipError('');
  }

  return <details className="motion-workflow">
    <summary>视频提取与重定向</summary>
    <form className="motion-form" onSubmit={async event => {
      event.preventDefault();
      if (!file || busy || active) return;
      const input = new FormData(event.currentTarget);
      const license = String(input.get('license')).trim(), attribution = String(input.get('attribution')).trim();
      const sourceUrl = String(input.get('sourceUrl')).trim();
      if (!license || !attribution) { setError('填写视频许可证和署名。'); return; }
      setBusy(true); setError('');
      try { setVideo(await uploadMotionVideo(file, { license, attribution, sourceUrl })); }
      catch (failure) { setError((failure as Error).message); }
      finally { setBusy(false); }
    }}>
      <fieldset disabled={busy || active || !!video}>
        <div className="robot-archive-upload" role="group" aria-label="上传动作视频"
          onDragOver={event => { if (event.dataTransfer.types.includes('Files')) event.preventDefault(); }}
          onDrop={event => { event.preventDefault(); if (!video) select(event.dataTransfer.files); }}>
          <label>拖放或选择动作视频<input type="file" accept=".mp4,.webm" onChange={event => {
            select(event.currentTarget.files); event.currentTarget.value = '';
          }} /></label>
          <small>单人全身 · 最长 20 秒 · 16 MiB</small>
          {file && <span>{file.name}</span>}
        </div>
        <label>视频许可证<input name="license" maxLength={256} required /></label>
        <label>署名<input name="attribution" maxLength={1024} required /></label>
        <label>来源网址（可选）<input name="sourceUrl" type="url" maxLength={2048} /></label>
        <button type="submit" disabled={!file}>上传视频</button>
      </fieldset>
    </form>
    {video && <>
      <p><code>{video.id}</code> · {video.license.license} · {video.license.attribution}</p>
      <button type="button" disabled={busy || active || job?.status === 'succeeded'} onClick={async () => {
        setBusy(true); setError('');
        try {
          requestId.current ??= crypto.randomUUID();
          const next = await motionWrite<TrainingJob>('/jobs', {
            kind: 'motion_preprocess', motionVideo: video.id, requestId: requestId.current,
          });
          requestId.current = undefined;
          setJob(next);
        } catch (failure) { setError((failure as Error).message); }
        finally { setBusy(false); }
      }}>提交 CPU 提取</button>
      <button type="button" disabled={busy || active} onClick={() => {
        requestId.current = undefined;
        setFile(undefined); setVideo(undefined); setJob(undefined); setClip(undefined); setError(''); setPollError('');
        setClipError('');
      }}>选择另一视频</button>
    </>}
    {busy && <p role="status">正在提交…</p>}
    {job && <p role="status">{job.status === 'running' ? 'CPU 提取中' : jobStatusLabels[job.status]} ·
      {job.nodeId ? ` ${job.nodeId} / CPU` : active ? ' 等待兼容 CPU 节点' : ' —'}
      {job.reason && ` · ${jobReason(job.reason)}`} · <Link to="/train/jobs/$id" params={{ id: job.jobId }}>查看提取作业</Link></p>}
    {pollError && <p role="alert">{pollError}</p>}
    {clipId && !clip && (clipError ? <div><p role="alert">{clipError}</p>
      <button type="button" onClick={() => setClipRead(read => read + 1)}>重试读取片段</button>
    </div> : <p role="status">正在读取固定片段…</p>)}
    {error && <p role="alert">{error}</p>}
    {clip && <form className="motion-form" onSubmit={async event => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      setBusy(true); setError('');
      try {
        const reference = await motionWrite<ReferenceDetail>('/library/reference-motions', {
          asset, assetContentHash: contentHash, clip: { id: clip.id, sha256: clip.sha256 },
          mapping: JSON.parse(String(form.get('mapping'))), inPlace: true,
        });
        if (reference.motion.asset !== asset || reference.motion.assetContentHash !== contentHash ||
            reference.source.clip?.id !== clip.id || reference.source.clip.sha256 !== clip.sha256) {
          throw new Error('参考动作与当前素材或源片段不符。');
        }
        onReference({ id: reference.id, sha256: reference.sha256, asset, assetContentHash: contentHash });
      } catch (failure) { setError((failure as Error).message); }
      finally { setBusy(false); }
    }}>
      <p>Motion Clip <code>{clip.id}</code> · 可见帧 {clip.diagnostics.visibleFrames} · 缺帧 {clip.diagnostics.missingFrames}</p>
      <p>姿态模型 {clip.model.runtime} · {clip.model.landmarkLicense} · <code title={clip.model.sha256}>{clip.model.sha256}</code></p>
      <p>目标 <code>{asset}</code> · <code title={contentHash}>{contentHash}</code></p>
      <label>肢体映射 JSON<textarea name="mapping" maxLength={16384} rows={4} required disabled={busy} /></label>
      <p>单目视频采用原地模式。需要世界平移轨迹时，请提供 Asset 坐标系中的关键帧。</p>
      <button type="submit" disabled={busy}>生成参考动作</button>
    </form>}
  </details>;
}
