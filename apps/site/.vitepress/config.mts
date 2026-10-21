import { defineConfig, type HeadConfig } from 'vitepress'
import { assertDisplayFontCovers } from './display-font'
import { HERO } from './theme/landing/story'

const base = '/spatial-sync/'
const origin = 'https://caizongyuan.github.io'

// Chinese is the root locale. English joins later under /en/; the language
// menu stays hidden until a second locale exists.
export default defineConfig({
  base,
  title: 'Lab World',
  description: HERO.lede,
  appearance: 'dark',
  cleanUrls: true,
  lastUpdated: false,
  head: [
    ['link', { rel: 'icon', type: 'image/svg+xml', href: `${base}mark.svg` }],
    ['link', { rel: 'preload', href: `${base}fonts/noto-serif-sc-600.woff2`, as: 'font', type: 'font/woff2', crossorigin: '' }],
    ['meta', { name: 'theme-color', content: '#121211' }],
    ['meta', { name: 'twitter:card', content: 'summary_large_image' }],
    ['meta', { property: 'og:site_name', content: 'Lab World' }],
  ],
  // Link previews: every page gets its own title and summary; a post shows its cover.
  transformHead({ pageData, title, description: pageDescription }): HeadConfig[] {
    const fm = pageData.frontmatter
    const path = pageData.relativePath.replace(/(^|\/)index\.md$/, '$1').replace(/\.md$/, '')
    const image = `${origin}${base}${(fm.cover ?? '/og.jpg').replace(/^\//, '')}`
    return [
      ['meta', { property: 'og:type', content: fm.date ? 'article' : 'website' }],
      ['meta', { property: 'og:title', content: title }],
      ['meta', { property: 'og:description', content: pageDescription }],
      ['meta', { property: 'og:url', content: `${origin}${base}${path}` }],
      ['meta', { property: 'og:image', content: image }],
    ]
  },
  locales: {
    root: { label: '简体中文', lang: 'zh-CN' },
  },
  buildEnd: (site) => assertDisplayFontCovers(site.srcDir),
  // three.js is one lazily loaded chunk of about 560 kB; it never blocks the first paint.
  vite: { build: { chunkSizeWarningLimit: 600 } },
  themeConfig: {
    logo: '/mark.svg',
    siteTitle: 'Lab World',
    nav: [
      { text: '博客', link: '/blog/', activeMatch: '/blog/' },
      { text: '文档', link: '/guide/getting-started', activeMatch: '/guide/' },
    ],
    sidebar: {
      '/guide/': [
        {
          text: '开始',
          items: [{ text: '快速开始', link: '/guide/getting-started' }],
        },
      ],
    },
    outline: { label: '本页目录' },
    docFooter: { prev: '上一页', next: '下一页' },
    darkModeSwitchLabel: '外观',
    lightModeSwitchTitle: '切换到浅色',
    darkModeSwitchTitle: '切换到深色',
    sidebarMenuLabel: '菜单',
    returnToTopLabel: '回到顶部',
    externalLinkIcon: false,
  },
})
