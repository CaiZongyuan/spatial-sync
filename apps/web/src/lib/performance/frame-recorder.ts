export interface FrameSample {
  timeMs: number;
  applyMs: number;
  drawCalls: number;
  objects: number;
  frameAgeMs: number;
}

/** Opt-in bounded sampling of the real R3F loop; inactive during normal use. */
export class FrameRecorder {
  private samples?: Float64Array;
  private count = 0;
  private overflow = 0;
  private active = false;
  private readonly stride = 5;
  constructor(private readonly capacity = 60_000) {}

  start() {
    this.samples = new Float64Array(this.capacity * this.stride);
    this.count = 0;
    this.overflow = 0;
    this.active = true;
  }

  record(
    timeMs: number,
    applyMs: number,
    drawCalls: number,
    objects: number,
    frameAgeMs: number,
  ) {
    if (!this.active || !this.samples) return;
    if (this.count >= this.capacity) {
      this.overflow++;
      return;
    }
    const offset = this.count++ * this.stride;
    this.samples[offset] = timeMs;
    this.samples[offset + 1] = applyMs;
    this.samples[offset + 2] = drawCalls;
    this.samples[offset + 3] = objects;
    this.samples[offset + 4] = frameAgeMs;
  }

  stop(): { samples: FrameSample[]; overflow: number } {
    this.active = false;
    const samples: FrameSample[] = [];
    for (let i = 0; this.samples && i < this.count; i++) {
      const offset = i * this.stride;
      samples.push({
        timeMs: this.samples[offset],
        applyMs: this.samples[offset + 1],
        drawCalls: this.samples[offset + 2],
        objects: this.samples[offset + 3],
        frameAgeMs: this.samples[offset + 4],
      });
    }
    this.samples = undefined;
    return { samples, overflow: this.overflow };
  }
}
