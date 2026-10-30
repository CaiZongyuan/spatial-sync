<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { withBase } from 'vitepress'

const primary = 'https://media.lumen-physical-ai.top/lab-world-promo-platform-update-zh.webm'
const backup = 'https://pub-6d8f23b1806440e89d34541e9d6f10cb.r2.dev/lab-world-promo-platform-update-zh.webm'
const video = ref<HTMLVideoElement>()
const enhanced = ref(false)
const showPlay = ref(true)
const usingBackup = ref(false)
const failed = ref(false)
const source = computed(() => usingBackup.value ? backup : primary)

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
  <section id="platform-film" class="platform-film pl-section" tabindex="-1" aria-labelledby="platform-film-title">
    <p class="pl-eyebrow pl-rule"><span>Lab World · Platform Film</span><span class="duration">01:42 / 中文</span></p>
    <div class="pl-section-head">
      <h2 id="platform-film-title"><span class="nb">看见 Lab World</span><span class="nb">如何运转</span></h2>
      <p class="pl-section-lede">从可计算的世界，到智能体的行动与反馈。用一段视频，看看 Lab World 如何把它们连接起来。</p>
    </div>
    <figure class="film-figure">
      <div class="player">
        <video ref="video" :src="source" :poster="withBase('/images/landing/platform-film.webp')" width="1920" height="1080" controls playsinline preload="none" aria-label="Lab World 中文平台介绍" @error="recover" @playing="onPlaying" @ended="showPlay = true">
          当前浏览器无法播放此视频。<a :href="primary">直接打开视频</a>。
        </video>
        <button v-if="enhanced && showPlay" class="play" type="button" aria-label="播放 Lab World 平台介绍" @click="start"><svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 4.5v15l12-7.5z" /></svg></button>
      </div>
      <p v-if="failed" class="error" role="status">视频暂时无法播放，请通过下方链接直接打开。</p>
      <figcaption>
        <span>点击播放 · 支持全屏观看</span>
        <span class="links"><a :href="primary" target="_blank" rel="noopener">直接打开</a><a :href="backup" target="_blank" rel="noopener">备用线路</a></span>
      </figcaption>
    </figure>
  </section>
</template>

<style scoped>
.platform-film { scroll-margin-top: 88px; }
.duration { order: 1; flex: none; letter-spacing: 0; }
.film-figure { margin: 40px 0 0; }
.player { position: relative; overflow: hidden; border: 1px solid var(--line-2); border-radius: 14px; background: #101010; box-shadow: var(--shadow-1); }
video { display: block; width: 100%; height: auto; aspect-ratio: 16 / 9; object-fit: contain; }
.play { position: absolute; left: 50%; top: 50%; translate: -50% -50%; display: grid; place-items: center; width: 76px; height: 76px; padding: 0; border: 1px solid rgba(255, 255, 255, .45); border-radius: 50%; color: #171716; background: #f2f0e9; box-shadow: 0 6px 28px rgba(0, 0, 0, .25); cursor: pointer; transition: background .2s var(--ease), scale .2s var(--ease); }
.play:hover { background: #fff; scale: 1.04; }
.play:focus-visible { outline: 2px solid #fff; outline-offset: 5px; }
figcaption { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px 24px; margin-top: 14px; font-size: 12.5px; line-height: 1.8; color: var(--muted); }
.links { display: flex; flex-wrap: wrap; gap: 20px; }
.links a { text-decoration: underline; text-underline-offset: 4px; }
.links a:hover { color: var(--ink); }
.links a:focus-visible { outline: 2px solid var(--accent); outline-offset: 4px; border-radius: 2px; }
.pl .error { margin-top: 12px; color: var(--ink-2); font-size: 14px; line-height: 1.8; }
@media (max-width: 620px) {
  .film-figure { margin-top: 28px; }
  .play { width: 56px; height: 56px; }
  .player { border-radius: 10px; }
}
</style>
