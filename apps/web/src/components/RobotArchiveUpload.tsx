import { useNavigate } from '@tanstack/react-router';
import { useRef, useState } from 'react';
import { ApiError, request } from '../lib/api';

const archiveLimit = 128 * 1024 * 1024;

export function RobotArchiveUpload() {
  const navigate = useNavigate();
  const uploading = useRef(false);
  const [file, setFile] = useState<File>();
  const [error, setError] = useState('');
  const busy = file !== undefined;

  async function upload(files: FileList | null) {
    if (uploading.current || !files?.length) return;
    if (files.length !== 1) {
      setError('每次上传一个机器人归档。');
      return;
    }
    const archive = files[0];
    const gzip = /\.(tar\.gz|tgz)$/i.test(archive.name);
    if ((!gzip && !/\.tar$/i.test(archive.name)) || archive.size === 0 || archive.size > archiveLimit) {
      setError('选择不超过 128 MiB 的有效 tar 或 tar.gz 归档。');
      return;
    }
    uploading.current = true;
    setFile(archive);
    setError('');
    try {
      const receipt = await request<{ assetVersion: string }>('/library/imports?robotSource=true', {
        method: 'POST', headers: { 'Content-Type': gzip ? 'application/gzip' : 'application/x-tar' },
        body: archive,
      });
      const version = /^robot\/([a-z0-9]+(?:-[a-z0-9]+)*)@([1-9][0-9]*)$/.exec(receipt.assetVersion);
      if (!version) throw new Error('导入回执缺少精确机器人版本。');
      await navigate({ to: '/assets/$kind/$name/$rev', params: { kind: 'robot', name: version[1], rev: version[2] } });
    } catch (failure) {
      const details = failure instanceof ApiError ? failure.detail as { problems?: { path: string; message: string }[] } : undefined;
      const problems = details?.problems?.map(problem => [problem.path, problem.message].filter(Boolean).join(' · ')) ?? [];
      setError([(failure as Error).message, ...problems].join('。'));
    } finally {
      uploading.current = false;
      setFile(undefined);
    }
  }

  return <section className="robot-archive-upload" role="group" aria-label="上传机器人归档" aria-busy={busy}
    onDragOver={event => { if (event.dataTransfer.types.includes('Files')) event.preventDefault(); }}
    onDrop={event => { event.preventDefault(); void upload(event.dataTransfer.files); }}>
    <label>拖放或选择机器人归档<input type="file" accept=".tar,.tar.gz,.tgz" disabled={busy}
      onChange={event => { void upload(event.currentTarget.files); event.currentTarget.value = ''; }} /></label>
    <small>支持含 robot.json 的 URDF、MJCF 或 USD 源包。</small>
    {busy && <p role="status">正在导入 {file?.name}…</p>}
    {error && <p role="alert" className="asset-failure">{error}</p>}
  </section>;
}
