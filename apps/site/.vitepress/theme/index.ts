import DefaultTheme from 'vitepress/theme-without-fonts'
import type { Theme } from 'vitepress'
import { h } from 'vue'
import Landing from './landing/Landing.vue'
import BlogIndex from './blog/BlogIndex.vue'
import PostHeader from './blog/PostHeader.vue'
import './style.css'

export default {
  extends: DefaultTheme,
  Layout: () => h(DefaultTheme.Layout, null, { 'doc-before': () => h(PostHeader) }),
  enhanceApp({ app }) {
    app.component('Landing', Landing)
    app.component('BlogIndex', BlogIndex)
  },
} satisfies Theme
