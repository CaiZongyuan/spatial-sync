<script setup lang="ts">
// The blog index: newest first, each post as a cover, date, title and summary.
import { withBase } from 'vitepress'
import { data as posts } from './posts.data'
import { formatDate } from './date'
</script>

<template>
  <ol class="bi">
    <li v-for="p in posts" :key="p.url">
      <a :href="withBase(p.url)">
        <img v-if="p.cover" :src="withBase(p.cover)" alt="" loading="lazy" decoding="async" />
        <span class="meta">{{ formatDate(p.date) }}<template v-if="p.author"> · {{ p.author }}</template></span>
        <span class="title">{{ p.title }}</span>
        <span class="desc">{{ p.description }}</span>
      </a>
    </li>
  </ol>
</template>

<style scoped>
.bi { list-style: none; margin: 32px 0 0; padding: 0; display: flex; flex-direction: column; gap: 48px; }
.bi li { margin: 0; }
a { display: grid; grid-template-columns: 280px 1fr; grid-template-rows: auto auto 1fr; column-gap: 28px; color: inherit; text-decoration: none; }
img { grid-row: 1 / 4; width: 100%; aspect-ratio: 16 / 10; object-fit: cover; border-radius: 12px; border: 1px solid var(--line); background: var(--figure-ground); }
.meta { font-family: var(--font-mono); font-size: 12px; color: var(--faint); }
.title { margin-top: 8px; font-family: var(--font-display); font-weight: 600; font-size: 24px; line-height: 1.35; color: var(--ink); transition: color .2s var(--ease); }
.desc { margin-top: 10px; font-size: 14.5px; line-height: 1.75; color: var(--muted); }
a:hover .title { color: var(--accent-ink); }
@media (max-width: 640px) {
  a { grid-template-columns: 1fr; }
  img { grid-row: auto; margin-bottom: 16px; }
}
</style>
