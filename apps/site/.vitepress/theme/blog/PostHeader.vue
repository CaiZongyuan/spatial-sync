<script setup lang="ts">
// Above a post's title: the way back to the index, the date and the author.
import { useData, withBase } from 'vitepress'
import { computed } from 'vue'
import { formatDate, isoDate } from './date'

const { frontmatter, page } = useData()
const isPost = computed(() => page.value.relativePath.startsWith('blog/') && Boolean(frontmatter.value.date))
const date = computed(() => (isPost.value ? isoDate(frontmatter.value.date) : ''))
</script>

<template>
  <div v-if="isPost" class="ph">
    <a :href="withBase('/blog/')">← 博客</a>
    <span>{{ formatDate(date) }}<template v-if="frontmatter.author"> · {{ frontmatter.author }}</template></span>
  </div>
</template>

<style scoped>
.ph { display: flex; flex-wrap: wrap; gap: 6px 20px; margin-bottom: 20px; font-family: var(--font-mono); font-size: 12px; color: var(--faint); }
.ph a { color: var(--muted); text-decoration: none; }
.ph a:hover { color: var(--ink); }
</style>
