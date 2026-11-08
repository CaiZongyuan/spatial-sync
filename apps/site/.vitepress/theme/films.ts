export type FilmId = 'promo' | 'experiment' | 'passage'

export interface FilmItem {
  id: FilmId
  num: string
  tag: string
  title: string
  subtitle: string
  duration: string
  poster: string
  primary: string
  backup: string
  ariaLabel: string
  summary: string
  post?: string
}

export const PROMO_FILM: FilmItem = {
  id: 'promo',
  num: '01',
  tag: '平台介绍',
  title: '迈向物理 AI',
  subtitle: '世界构建、具身智能体大脑与持续进化',
  duration: '01:42 / 中文',
  poster: '/images/landing/platform-film.webp',
  primary: 'https://media.lumen-physical-ai.top/lab-world-promo-platform-update-zh.webm',
  backup: 'https://pub-6d8f23b1806440e89d34541e9d6f10cb.r2.dev/lab-world-promo-platform-update-zh.webm',
  ariaLabel: 'Lab World 中文平台介绍',
  summary: '从可计算的世界，到智能体的行动与反馈。用一段视频，看看 Lab World 如何把世界构建、具身智能体大脑与持续进化连接起来。',
}

export const EXPERIMENT_FILM: FilmItem = {
  id: 'experiment',
  num: '02',
  tag: '搭场景到实操',
  title: '让 Agent 动手做实验',
  subtitle: '一座细胞传代实验室，从搭场景到 Agent 实操',
  duration: '02:26 / 中文',
  poster: '/images/landing/experiment-film.webp',
  primary: 'https://media.lumen-physical-ai.top/lab-world-experiment-zh-burned.webm',
  backup: 'https://pub-6d8f23b1806440e89d34541e9d6f10cb.r2.dev/lab-world-experiment-zh-burned.webm',
  ariaLabel: 'Lab World · 让 Agent 动手做实验',
  summary: '用 World Building 搭起含 38 件视觉素材、59 个刚体与 2993 个功能面碰撞体的细胞传代实验室；导入 Lab World 后，Agent 查询设备目录并经过两轮计划核对修订完成第 1 步 23 个多设备协同动作（当前基础驱动执行关节到位，尚不含抓持随动）。',
  post: '/blog/from-world-building-to-physical-ai',
}

export const PASSAGE_FILM: FilmItem = {
  id: 'passage',
  num: '03',
  tag: '14 步仿真渲染',
  title: '一次细胞传代 · 14 步',
  subtitle: '十四步，从培养箱到培养箱',
  duration: '01:55 / 中文',
  poster: '/images/landing/passage-film.webp',
  primary: 'https://media.lumen-physical-ai.top/lab-world-passage-zh-burned.webm',
  backup: 'https://pub-6d8f23b1806440e89d34541e9d6f10cb.r2.dev/lab-world-passage-zh-burned.webm',
  ariaLabel: '一次细胞传代 · 14 步轨迹预演',
  summary: '从培养箱取出母板，经观察站检测、去盖清洗、解离收集、配平离心、重悬计数、子板接种回箱，到耗材入固废与机械臂停靠的完整 14 步流程（由 MuJoCo 连续计算 911.9 秒轨迹并通过 20 项门禁校验，再交由 World Building 在 Blender 逐帧渲染；解离与离心采用教学时长，计数为模拟数据）。',
  post: '/blog/from-world-building-to-physical-ai#passage-14-steps',
}

export const FILMS: FilmItem[] = [PROMO_FILM, EXPERIMENT_FILM, PASSAGE_FILM]
