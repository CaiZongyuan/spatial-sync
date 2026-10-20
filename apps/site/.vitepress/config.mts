import { defineConfig } from 'vitepress'

const repo = 'https://github.com/CaiZongyuan/spatial-sync'

// Chinese is the root locale. English joins later under /en/; the language
// menu stays hidden until a second locale exists.
export default defineConfig({
  base: '/spatial-sync/',
  title: 'Lab RSI Platform',
  description: '会自我改进的实验室：agent 自己设计流程、操控设备、训练机器人，并在可度量的闭环里变得更好。',
  appearance: 'dark',
  cleanUrls: true,
  lastUpdated: false,
  head: [
    ['link', { rel: 'icon', type: 'image/svg+xml', href: '/spatial-sync/mark.svg' }],
    ['meta', { name: 'theme-color', content: '#121211' }],
  ],
  locales: {
    root: { label: '简体中文', lang: 'zh-CN' },
  },
  themeConfig: {
    logo: '/mark.svg',
    siteTitle: 'Lab RSI Platform',
    nav: [
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
    socialLinks: [{ icon: 'github', link: repo }],
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
