import DefaultTheme from 'vitepress/theme'
import type { Theme } from 'vitepress'
import PrototypeLanding from './prototype-landing/PrototypeLanding.vue'
import './style.css'

export default {
  extends: DefaultTheme,
  enhanceApp({ app }) {
    app.component('PrototypeLanding', PrototypeLanding)
  },
} satisfies Theme
