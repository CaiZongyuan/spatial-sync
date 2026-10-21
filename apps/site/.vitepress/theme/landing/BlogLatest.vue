<script setup lang="ts">
// The newest posts, as the home page's way into the blog.
import { withBase } from 'vitepress'
import { data as posts } from '../blog/posts.data'
import { formatDate } from '../blog/date'

const latest = posts.slice(0, 3)
</script>

<template>
  <section id="blog" class="pl-section">
    <p class="pl-eyebrow pl-rule"><span>博客</span></p>
    <div class="head">
      <h2>最新文章</h2>
      <a class="all" :href="withBase('/blog/')">全部文章 →</a>
    </div>
    <div class="posts" :class="{ feature: latest.length === 1 }">
      <a v-for="p in latest" :key="p.url" class="post" :href="withBase(p.url)">
        <img v-if="p.cover" :src="withBase(p.cover)" alt="" width="1600" height="900" loading="lazy" decoding="async" />
        <span class="meta mono">{{ formatDate(p.date) }}</span>
        <span class="title">{{ p.title }}</span>
        <span class="desc">{{ p.description }}</span>
      </a>
    </div>
  </section>
</template>

<style scoped>
.head { margin-top: 32px; display: flex; align-items: baseline; justify-content: space-between; gap: 16px; }
.pl .head h2 { font-size: clamp(32px, 4vw, 48px); }
.all { font-size: 13.5px; color: var(--muted); border-bottom: 1px solid var(--line-2); padding-bottom: 2px; }
.all:hover { color: var(--ink); border-color: var(--ink); }
.posts { margin-top: 40px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 32px; }
.post { display: flex; flex-direction: column; }
.post img { width: 100%; aspect-ratio: 16 / 10; object-fit: cover; border-radius: 12px; border: 1px solid var(--line); background: #141413; transition: transform .35s var(--ease), border-color .25s var(--ease); }
.post:hover img { transform: translateY(-2px); border-color: var(--line-2); }
.meta { margin-top: 16px; font-size: 11.5px; color: var(--faint); }
.title { margin-top: 8px; font-family: var(--font-display); font-weight: 600; font-size: 21px; line-height: 1.4; color: var(--ink); }
.post:hover .title { color: var(--accent-ink); }
.desc { margin-top: 8px; font-size: 14px; line-height: 1.75; color: var(--muted); }
/* A lone post gets the full width: cover on the left, text on the right. */
.posts.feature { grid-template-columns: 1fr; }
.feature .post { display: grid; grid-template-columns: 1.15fr 1fr; grid-template-rows: auto auto 1fr; column-gap: 40px; }
.feature .post img { grid-row: 1 / 4; }
.feature .meta { margin-top: 4px; }
.feature .title { font-size: clamp(24px, 2.6vw, 32px); }
.feature .desc { font-size: 15.5px; }
@media (max-width: 1000px) { .posts { grid-template-columns: 1fr 1fr; } .feature .post { grid-template-columns: 1fr; } .feature .post img { grid-row: auto; } .feature .meta { margin-top: 16px; } }
@media (max-width: 620px) { .posts { grid-template-columns: 1fr; } }
</style>
