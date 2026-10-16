import type { SceneManifest, TransformFrame } from '../packages/protocol/types';
import { PoseBuffer } from '../apps/web/src/lib/stream/pose-buffer';

type Corpus = {
  manifest: SceneManifest;
  frames: { json: string; binaryHex: string }[];
};

function percentile(values: number[], q: number) {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor((sorted.length - 1) * q)];
}

/** Only parse/decode + common buffer ingestion is timed; setup and the full oracle are outside. */
export function profileCorpus(corpus: Corpus, iterations = 40) {
  const rows = corpus.frames.map(
    (frame) => JSON.parse(frame.json) as TransformFrame,
  );
  const bodies = corpus.manifest.nodes.filter((node) => node.kind === 'body');
  const templates = corpus.frames.map(
    (frame) =>
      Uint8Array.from(frame.binaryHex.match(/../g)!, (byte) =>
        parseInt(byte, 16),
      ).buffer,
  );
  const warmup = 12;
  const records = [];
  // Reverse order on the second pass to expose order / JIT effects.
  for (let repeat = 0; repeat < 2; repeat++)
    for (const format of repeat ? ['binary', 'json'] : ['json', 'binary']) {
      const buffer = new PoseBuffer();
      buffer.configure(corpus.manifest);
      buffer.push({ ...rows[0], type: 'stream.snapshot', seq: 0 }, 1000);
      const payloads = Array.from(
        { length: iterations + warmup },
        (_, index) => {
          const seq = index + 1;
          const metadata = {
            seq,
            sampleTimeMs: 1000 + (seq * 1000) / 30,
            simTime: seq / 30,
          };
        if (format === 'json')
          return corpus.frames[index % 8].json
            .replace(/"seq":\d+/, `"seq":${seq}`)
            .replace(/"sampleTimeMs":[^,}]+/, `"sampleTimeMs":${metadata.sampleTimeMs}`)
            .replace(/"simTime":[^,}]+/, `"simTime":${metadata.simTime}`);
          const bytes = templates[index % 8].slice(0);
          const view = new DataView(bytes);
          view.setBigUint64(16, BigInt(seq), true);
          view.setFloat64(24, metadata.simTime, true);
          view.setFloat64(32, metadata.sampleTimeMs, true);
          return bytes;
        },
      );
      const durations: number[] = [];
      payloads.forEach((payload, index) => {
        const received = 1000 + ((index + 1) * 1000) / 30;
        const start = performance.now();
        const accepted =
          typeof payload === 'string'
            ? buffer.push(JSON.parse(payload), received)
            : buffer.pushBinary(payload, received);
        const elapsed = performance.now() - start;
        if (!accepted || buffer.lastSeq !== index + 1)
          throw new Error('Benchmark silently discarded a frame');
        // Check every useful output after every sample, outside the measured boundary.
        const expected = rows[index % 8].poses;
        bodies.forEach((body, bodyIndex) => {
          const actual = buffer.readLatest(body.entityKey)!;
          const wanted = expected[bodyIndex];
          [...actual.p, ...actual.q].forEach((value, component) => {
            if (
              !Number.isFinite(value) ||
              Math.abs(value - wanted[component + 1]) > 1e-6
            )
              throw new Error(
                `Pose oracle failed: ${body.streamId}/${component}`,
              );
          });
        });
        if (index >= warmup) durations.push(elapsed);
      });
      records.push({
        bodies: bodies.length,
        format,
        repeat,
        warmup,
        validSamples: durations.length,
        parseIngestP50Ms: percentile(durations, 0.5),
        parseIngestP95Ms: percentile(durations, 0.95),
        skipped: buffer.skippedNonFinite,
        invalid: buffer.invalidBinaryFrames,
        discarded: buffer.discardedFrames,
      });
    }
  return records;
}
