<script setup lang="ts">
// Every chapter as one still, for readers who skim instead of scrolling the film.
import StatusChip from './StatusChip.vue'
import { CHAPTERS, STATUS_LABEL } from './story'

defineProps<{ stills: string[] }>()
const emit = defineEmits<{ play: [index: number] }>()
const flat = (s: string) => s.replace('\n', '')
</script>

<template>
  <section id="chapters" class="ci pl-section">
    <p class="pl-eyebrow pl-rule"><span>动画逐章速览</span></p>
    <div class="pl-section-head">
      <h2><span class="nb">九个画面，</span><span class="nb">一条闭环</span></h2>
      <p class="pl-section-lede">从一句目标，到可以复用的物理智能。点任意一章，回到动画里的那一刻。</p>
    </div>
    <ol class="grid">
      <li v-for="(c, i) in CHAPTERS" :key="c.id">
        <button class="card" :aria-label="`在动画中播放第 ${c.n} 章：${flat(c.title)}`" @click="emit('play', i)">
          <span class="fig">
            <span class="frame" :class="c.id"><img v-if="stills[i]" :src="stills[i]" alt="" /></span>
            <span class="cap mono">图 {{ i }} · {{ c.kicker }} · {{ STATUS_LABEL[c.status] }}</span>
          </span>
          <span class="kick"><span class="mono">{{ c.n }}</span>{{ c.kicker }}<StatusChip :status="c.status" /></span>
          <span class="title">{{ flat(c.title) }}</span>
          <span class="body">{{ c.body }}</span>
        </button>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.grid { list-style: none; margin: 56px 0 0; padding: 0; display: grid; grid-template-columns: repeat(3, 1fr); gap: 48px 32px; }
.card { display: flex; flex-direction: column; align-items: flex-start; gap: 0; width: 100%; padding: 0; background: none; border: 0; text-align: left; color: inherit; font: inherit; cursor: pointer; }
.fig { display: block; width: 100%; }
.frame { display: block; width: 100%; aspect-ratio: 16 / 10; overflow: hidden; border-radius: 12px; border: 1px solid var(--line); background:
  radial-gradient(ellipse 70% 60% at 50% 45%, color-mix(in srgb, var(--surface-2) 75%, transparent), transparent 75%), var(--surface);
  transition: border-color .25s var(--ease), transform .35s var(--ease), box-shadow .35s var(--ease); }
.frame img { display: block; width: 100%; height: 100%; object-fit: cover; }
/* The live film dissolves the city's edge; the still does the same. */
.frame.scale img { -webkit-mask-image: radial-gradient(ellipse 62% 64% at 50% 50%, #000 35%, transparent 100%); mask-image: radial-gradient(ellipse 62% 64% at 50% 50%, #000 35%, transparent 100%); }
.card:hover .frame, .card:focus-visible .frame { border-color: var(--line-2); transform: translateY(-2px); box-shadow: var(--shadow-1); }
.cap { margin-top: 10px; display: flex; justify-content: space-between; font-size: 10.5px; letter-spacing: .04em; color: var(--faint); }
.kick { margin-top: 18px; display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--muted); }
.kick .mono { font-size: 12px; color: var(--faint); }
.title { margin-top: 10px; font-family: var(--font-display); font-weight: 600; font-size: 22px; line-height: 1.35; color: var(--ink); }
.body { margin-top: 8px; font-size: 14px; line-height: 1.75; color: var(--muted); }
.card:hover .title, .card:focus-visible .title { color: var(--accent-ink); }

@media (max-width: 1000px) {
  .grid { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 620px) {
  .grid { grid-template-columns: 1fr; gap: 40px; margin-top: 40px; }
}
</style>
