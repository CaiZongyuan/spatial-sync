<script setup lang="ts">
// The closing vision: one lab becomes many, sharing what each of them learns.
import { withBase } from 'vitepress'
import StatusChip from './StatusChip.vue'
</script>

<template>
  <section id="scale" class="sv">
    <div class="sv-copy pl-section">
      <p class="pl-eyebrow pl-rule"><span>规模</span></p>
      <div class="pl-section-head">
        <h2><span class="nb">然后，</span><span class="nb">是一千座实验室</span></h2>
        <p class="pl-section-lede">每座实验室有自己的 agent 和自己的目标。任何一处学到的策略和技能，都会进入共享的能力库，流向所有实验室。实验不再一次只做一个，改进也不再只属于一个地方。</p>
        <StatusChip status="vision" />
      </div>
    </div>
    <figure class="sv-figure">
      <img :src="withBase('/images/landing/scale.webp')" alt="俯瞰成百上千座微缩实验室组成的网格，靛蓝色的光在实验室之间流动，一直延伸到远处" width="1600" height="800" loading="lazy" decoding="async" />
    </figure>
    <p class="sv-line"><span class="nb">让科学发现的速度，</span><span class="nb">不再受限于人手。</span></p>
  </section>
</template>

<style scoped>
.sv-copy :deep(.pl-chip) { margin-top: 18px; }
.sv-figure { margin: 56px 0 0; position: relative; }
.sv-figure img { display: block; width: 100%; height: min(70vh, 720px); object-fit: cover; background: #141413;
  -webkit-mask-image: linear-gradient(180deg, transparent, #000 14%, #000 80%, transparent);
  mask-image: linear-gradient(180deg, transparent, #000 14%, #000 80%, transparent); }
.pl .sv-line { margin: 40px auto 0; max-width: 1160px; padding: 0 24px; text-align: center; font-family: var(--font-display); font-weight: 600; font-size: clamp(28px, 4.2vw, 56px); line-height: 1.3; color: var(--ink); }
@media (max-width: 620px) { .sv-figure img { height: 56vh; } .pl .sv-line { padding: 0 16px; text-align: left; } }
</style>
