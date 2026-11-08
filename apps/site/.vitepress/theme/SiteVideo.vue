<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { withBase } from 'vitepress'
import type { FilmItem } from './films'

const props = defineProps<{
  film: FilmItem
  caption?: string
  showSummary?: boolean
  showPostLink?: boolean
}>()

const video = ref<HTMLVideoElement>()
const enhanced = ref(false)
const showPlay = ref(true)
const usingBackup = ref(false)
const failed = ref(false)
const source = computed(() => usingBackup.value ? props.film.backup : props.film.primary)

onMounted(() => { enhanced.value = true })

function play() {
  const player = video.value
  if (!player) return
  showPlay.value = false
  player.play().catch((error: DOMException) => {
    if (error.name !== 'AbortError' && !player.error) showPlay.value = true
  })
}

function start() {
  video.value?.focus()
  play()
}

async function recover() {
  if (usingBackup.value) {
    failed.value = true
    showPlay.value = false
    return
  }
  usingBackup.value = true
  await nextTick()
  video.value?.load()
  play()
}

function onPlaying() {
  showPlay.value = false
  failed.value = false
}
</script>

<template>
  <figure class="site-video">
    <div class="player">
      <video
        ref="video"
        :src="source"
        :poster="withBase(film.poster)"
        width="1920"
        height="1080"
        controls
        playsinline
        preload="none"
        :aria-label="film.ariaLabel"
        @error="recover"
        @playing="onPlaying"
        @ended="showPlay = true"
      >
        当前浏览器无法播放此视频。<a :href="film.primary">直接打开视频</a>。
      </video>
      <button
        v-if="enhanced && showPlay"
        class="play"
        type="button"
        :aria-label="`播放 ${film.title}`"
        @click="start"
      >
        <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 4.5v15l12-7.5z" /></svg>
      </button>
    </div>
    <p v-if="failed" class="error" role="status">视频暂时无法播放，请通过下方链接直接打开。</p>
    <figcaption>
      <p v-if="showSummary" class="film-summary">{{ film.summary }}</p>
      <div class="film-foot">
        <span>{{ caption ?? `${film.title} · ${film.duration}` }}</span>
        <span class="links">
          <a :href="film.primary" target="_blank" rel="noopener">直接打开</a>
          <a :href="film.backup" target="_blank" rel="noopener">备用线路</a>
          <a v-if="showPostLink && film.post" :href="withBase(film.post)">阅读配套文章 →</a>
        </span>
      </div>
    </figcaption>
  </figure>
</template>

<style scoped>
.site-video { margin: 28px 0; }
.player { position: relative; overflow: hidden; border: 1px solid var(--line-2); border-radius: 14px; background: #101010; box-shadow: var(--shadow-1); }
video { display: block; width: 100%; height: auto; aspect-ratio: 16 / 9; object-fit: contain; }
.play { position: absolute; left: 50%; top: 50%; translate: -50% -50%; display: grid; place-items: center; width: 72px; height: 72px; padding: 0; border: 1px solid rgba(255, 255, 255, .45); border-radius: 50%; color: #171716; background: #f2f0e9; box-shadow: 0 6px 28px rgba(0, 0, 0, .25); cursor: pointer; transition: background .2s var(--ease), scale .2s var(--ease); }
.play:hover { background: #fff; scale: 1.04; }
.play:focus-visible { outline: 2px solid #fff; outline-offset: 5px; }
figcaption { display: flex; flex-direction: column; gap: 10px; margin-top: 14px; font-size: 12.5px; line-height: 1.8; color: var(--muted); }
.film-summary { margin: 0; font-size: 13.5px; line-height: 1.75; color: var(--ink-2); }
.film-foot { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px 24px; }
.links { display: inline-flex; flex-wrap: wrap; gap: 20px; }
.links a { color: var(--muted); text-decoration: underline; text-underline-offset: 4px; }
.links a:hover { color: var(--ink); }
.links a:focus-visible { outline: 2px solid var(--accent); outline-offset: 4px; border-radius: 2px; }
.error { margin: 12px 0 0; color: var(--ink-2); font-size: 14px; line-height: 1.8; }
@media (max-width: 620px) {
  .play { width: 56px; height: 56px; }
  .player { border-radius: 10px; }
}
</style>
