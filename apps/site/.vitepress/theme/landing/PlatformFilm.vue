<script setup lang="ts">
import { computed, ref } from 'vue'
import { FILMS, type FilmId } from '../films'
import SiteVideo from '../SiteVideo.vue'

const activeId = ref<FilmId>('promo')
const activeFilm = computed(() => FILMS.find((f) => f.id === activeId.value) ?? FILMS[0])
</script>

<template>
  <section id="platform-film" class="platform-film pl-section" tabindex="-1" aria-labelledby="platform-film-title">
    <p class="pl-eyebrow pl-rule"><span>Lab World · Platform Films</span><span class="duration">{{ activeFilm.tag }} · {{ activeFilm.duration }}</span></p>
    <div class="pl-section-head">
      <h2 id="platform-film-title"><span class="nb">看见 Lab World</span><span class="nb">如何运转</span></h2>
      <p class="pl-section-lede">三支中文短片：从平台全貌，到 World Building 搭建细胞传代实验室并由 Agent 规划实操，再到 14 步完整轨迹预演。</p>
    </div>

    <div class="film-tabs" role="group" aria-label="选择演示视频">
      <button
        v-for="film in FILMS"
        :key="film.id"
        type="button"
        class="film-tab"
        :class="{ active: activeId === film.id }"
        :aria-pressed="activeId === film.id"
        @click="activeId = film.id"
      >
        <span class="tab-top">
          <span class="tab-kicker"><span class="mono">{{ film.num }}</span><span>{{ film.tag }}</span></span>
          <span class="tab-duration mono">{{ film.duration }}</span>
        </span>
        <strong class="tab-title">{{ film.title }}</strong>
        <span class="tab-sub">{{ film.subtitle }}</span>
      </button>
    </div>

    <SiteVideo
      :key="activeFilm.id"
      class="film-figure"
      :film="activeFilm"
      show-summary
      show-post-link
      caption="点击播放 · 支持全屏观看"
    />
  </section>
</template>

<style scoped>
.platform-film { scroll-margin-top: 88px; }
.duration { order: 1; flex: none; letter-spacing: 0; }
.film-tabs { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 32px; }
.film-tab { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; padding: 16px 18px; border: 1px solid var(--line-2); border-radius: 12px; background: var(--surface); color: var(--ink-2); font: inherit; text-align: left; cursor: pointer; transition: border-color .2s var(--ease), box-shadow .2s var(--ease); }
.film-tab:hover { border-color: var(--ghost); }
.film-tab:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.film-tab.active { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 8%, var(--surface)); box-shadow: var(--shadow-1); }
.tab-top { width: 100%; display: flex; justify-content: space-between; align-items: center; gap: 8px; font-size: 11.5px; color: var(--muted); }
.tab-kicker { display: inline-flex; align-items: center; gap: 6px; }
.tab-kicker .mono { color: var(--faint); }
.film-tab.active .tab-kicker { color: var(--accent-ink); }
.film-tab.active .tab-kicker .mono { color: var(--accent-ink); }
.tab-duration { font-size: 11px; color: var(--faint); }
.tab-title { font-family: var(--font-display); font-weight: 600; font-size: 17px; line-height: 1.35; color: var(--ink); }
.tab-sub { font-size: 12.5px; line-height: 1.55; color: var(--muted); }
.film-figure { margin: 18px 0 0; }
@media (max-width: 900px) {
  .film-tabs { grid-template-columns: 1fr; gap: 10px; margin-top: 24px; }
  .film-tab { padding: 14px 16px; }
}
@media (max-width: 620px) {
  .film-figure { margin-top: 14px; }
}
</style>
