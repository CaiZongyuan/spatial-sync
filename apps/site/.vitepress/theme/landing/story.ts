// The landing storyboard. The film runs on one clock `t` in seconds, and every
// overlay and 3D pose is a pure function of it, so scrolling either way, jumping
// to a chapter and rendering a still all agree.

export type Status = 'delivered' | 'progress' | 'planned' | 'vision'

export const STATUS_LABEL: Record<Status, string> = {
  delivered: '已交付',
  progress: '进行中',
  planned: '规划中',
  vision: '愿景',
}

export interface Chapter {
  id: string
  n: string
  kicker: string
  title: string
  body: string
  status: Status
  start: number
  end: number
  key: number
}

export const CHAPTERS: Chapter[] = [
  { id: 'goal', n: '00', kicker: '目标', title: '你只需要说一句话', body: '「在 4 个培养温度下比较样本生长，找出最佳条件。」这是人在整个流程里唯一一次打字。', status: 'vision', start: 0, end: 5, key: 4.2 },
  { id: 'sense', n: '01', kicker: '感知', title: '先看清整个实验室', body: '机器人、离心机、培养箱、环境传感器：agent 通过同一个平台读到每台设备能做什么、此刻怎样、还缺什么。', status: 'delivered', start: 5, end: 11, key: 10.2 },
  { id: 'plan', n: '02', kicker: '规划', title: '自己拆出实验流程', body: '目标被拆成一串操作计划，每一步写明要做什么、期望看到什么。机械臂要走的路径，在动手之前就画出来。', status: 'delivered', start: 11, end: 17, key: 15.4 },
  { id: 'run', n: '03', kicker: '执行', title: '长程执行，\n连续数天', body: '上千个动作逐一打勾。读数有历史，异常有告警，agent 每一轮先查收件箱。', status: 'progress', start: 17, end: 26, key: 21.8 },
  { id: 'find', n: '04', kicker: '发现', title: '从失败里找到瓶颈', body: 'agent 回看录制：「把试管插进离心机」这一步只有 62% 成功率，失败集中在对位阶段。', status: 'planned', start: 26, end: 31, key: 29.6 },
  { id: 'evolve', n: '05', kicker: '进化', title: '自己训练，\n再接回流程', body: 'agent 起草训练计划；人批准后，在 256 个并行环境里训练。评估达到 94/100、人再次批准，新策略接回设备程序，agent 同时记下一条新技能。', status: 'planned', start: 31, end: 42, key: 37.2 },
  { id: 'result', n: '06', kicker: '结果', title: '拿到实验结果', body: '流程恢复，样本全部完成检测：37 °C 组生长最快。', status: 'vision', start: 42, end: 48, key: 47.4 },
  { id: 'loop', n: '07', kicker: '闭环', title: '每一轮，\n都让下一轮更好', body: '目标、规划、执行、发现、进化、结果，再回到目标：这就是 RSI（Recursive Self-Improvement，递归自我改进）。每次改进都经过评估、可以追溯；人只在两处关口点一下批准。', status: 'planned', start: 48, end: 52, key: 51.6 },
  { id: 'scale', n: '08', kicker: '规模', title: '然后，\n是一千座实验室', body: '每座实验室有自己的 agent 和目标。任何一处学到的策略和技能，都会流向所有实验室。让科学发现的速度，不再受限于人手。', status: 'vision', start: 52, end: 62, key: 61.4 },
]

export const DURATION = 62

export const HERO = {
  eyebrow: 'Lab RSI Platform · 早期预览',
  title: '会自我改进的实验室',
  lede: '你给一个目标。agent 自己设计流程、操控设备，从失败里训练新策略，直到拿到结果，再把学到的带到下一座实验室。',
  note: '动画为示意，其中的数字不是实测结果。',
}

export function chapterAt(t: number): Chapter {
  for (const c of CHAPTERS) if (t < c.end) return c
  return CHAPTERS[CHAPTERS.length - 1]
}

export function chapterIndex(t: number): number {
  return CHAPTERS.indexOf(chapterAt(t))
}

export const clamp = (v: number, a = 0, b = 1) => Math.min(b, Math.max(a, v))
export const lerp = (a: number, b: number, s: number) => a + (b - a) * s
export const smooth = (s: number) => { s = clamp(s); return s * s * (3 - 2 * s) }
export const ramp = (t: number, a: number, b: number) => smooth((t - a) / (b - a))
/** 0 → 1 over [a, a + fade], holds, 1 → 0 over [b - fade, b]. */
export const window01 = (t: number, a: number, b: number, fade = 0.4) =>
  Math.min(ramp(t, a, a + fade), 1 - ramp(t, b - fade, b))

function keyed(t: number, keys: [number, number][]): number {
  if (t <= keys[0][0]) return keys[0][1]
  for (let i = 1; i < keys.length; i++) {
    const [t1, v1] = keys[i]
    const [t0, v0] = keys[i - 1]
    if (t <= t1) return lerp(v0, v1, (t - t0) / (t1 - t0))
  }
  return keys[keys.length - 1][1]
}

// ---- Console -----------------------------------------------------------------

export type LineKind = 'human' | 'agent' | 'cmd' | 'out' | 'alert' | 'gate' | 'good'

export interface Line { t: number; kind: LineKind; text: string; type?: number }

export const CONSOLE: Line[] = [
  { t: 0.9, kind: 'human', text: '在 4 个培养温度下比较样本生长，找出最佳条件', type: 2.4 },
  { t: 4.1, kind: 'agent', text: '收到目标。先检查实验室里有什么。' },
  { t: 5.6, kind: 'cmd', text: 'lw ls --json' },
  { t: 6.2, kind: 'out', text: '1 台机器人 · 5 台设备 · 3 个环境传感器' },
  { t: 7.2, kind: 'cmd', text: 'lw show /World/Lab/Incubator' },
  { t: 7.8, kind: 'out', text: '4 个温区 · setTemperature · openDrawer' },
  { t: 8.8, kind: 'cmd', text: 'lw inbox --since c-118' },
  { t: 9.3, kind: 'alert', text: '检测仪缺少校准记录，先补校准' },
  { t: 11.4, kind: 'agent', text: '把目标拆成操作计划。' },
  { t: 12.2, kind: 'cmd', text: 'lw plan create growth-screen.yaml' },
  { t: 12.8, kind: 'out', text: 'plan-0042 · 5 个阶段 · 26 步' },
  { t: 14.0, kind: 'agent', text: '预览第 1 步的机械臂路径：无碰撞。' },
  { t: 17.4, kind: 'cmd', text: 'lw do /World/Lab/RobotA pickPlace from=rack.A1 to=centrifuge.slot1 --plan plan-0042 --step 1' },
  { t: 19.2, kind: 'good', text: '第 1 步完成 · 期望 slot1 有管：通过' },
  { t: 21.0, kind: 'cmd', text: 'lw do /World/Lab/Centrifuge spin rpm=4000 seconds=30' },
  { t: 23.0, kind: 'cmd', text: 'lw history /World/Lab/Incubator temperature --since 24h --every 1h' },
  { t: 23.6, kind: 'out', text: '4 个温区稳定，偏差 < 0.2 °C' },
  { t: 24.6, kind: 'agent', text: '时间快进：第 3 天，已执行 1,240 个动作。' },
  { t: 26.8, kind: 'alert', text: '插入离心机失败 · 近 40 次成功 25 次' },
  { t: 27.8, kind: 'cmd', text: 'lw rec ls --failed --step insert' },
  { t: 28.4, kind: 'out', text: '15 段失败录制' },
  { t: 29.2, kind: 'agent', text: '失败集中在对位阶段：试管偏差 > 2 mm。值得单独训练。' },
  { t: 31.2, kind: 'cmd', text: 'lw plan submit train-insert.yaml' },
  { t: 31.8, kind: 'out', text: '训练计划 plan-0043 · 3 个 seed · 预算 4 GPU·h' },
  { t: 32.5, kind: 'gate', text: '人工关口：训练计划已批准' },
  { t: 33.2, kind: 'agent', text: '在 256 个并行环境里训练插管策略。' },
  { t: 38.6, kind: 'cmd', text: 'lw eval run policy/insert@3 --suite insert-100' },
  { t: 39.2, kind: 'good', text: '评估 94/100 · 超过接受线 85' },
  { t: 39.9, kind: 'gate', text: '人工关口：策略提升已批准' },
  { t: 40.8, kind: 'agent', text: '写下技能「离心前检查配平」。' },
  { t: 42.4, kind: 'agent', text: '用新策略继续流程。' },
  { t: 45.6, kind: 'cmd', text: 'lw history /World/Lab/Detector od600 --since 72h' },
  { t: 46.6, kind: 'good', text: '结论：37 °C 组生长最快' },
]

export interface VisibleLine extends Line { shown: string; age: number; typing: boolean }

export function consoleAt(t: number, limit = 7): VisibleLine[] {
  const out: VisibleLine[] = []
  for (const l of CONSOLE) {
    if (l.t > t) break
    const age = t - l.t
    let shown = l.text
    let typing = false
    if (l.type) {
      const n = Math.floor(clamp(age / l.type) * l.text.length)
      shown = l.text.slice(0, n)
      typing = n < l.text.length
    }
    out.push({ ...l, shown, age, typing })
  }
  return out.slice(-limit)
}

// ---- Plan --------------------------------------------------------------------

export type StepState = 'pending' | 'active' | 'done' | 'issue'

const PLAN_STEPS = [
  { text: '分装 8 份样本到试管', active: 17.2, done: 19.4 },
  { text: '离心 30 s，分离上清', active: 19.4, done: 22.6 },
  { text: '放入 30 / 34 / 37 / 40 °C 四个温区', active: 22.6, done: 44.6 },
  { text: '每 6 小时取样检测，共 12 轮', active: 44.6, done: 46.6 },
  { text: '汇总生长曲线，给出结论', active: 46.4, done: 47.2 },
]

export function planAt(t: number) {
  const shown = clamp((t - 12.6) / 1.6) * PLAN_STEPS.length
  return PLAN_STEPS.map((s, i) => {
    let state: StepState = 'pending'
    if (t >= s.done) state = 'done'
    else if (t >= s.active) state = 'active'
    if (i === 2 && t >= 26.6 && t < 42.4) state = 'issue'
    return { ...s, i: i + 1, state, visible: i < shown }
  })
}

export const planRevision = (t: number) => (t >= 42.2 ? 'r2' : 'r1')

// ---- HUD ---------------------------------------------------------------------

export function hudAt(t: number) {
  const day = keyed(t, [[0, 0], [17, 0.02], [26, 2.4], [31, 2.6], [42, 3.1], [48, 4.0]])
  const actions = Math.round(keyed(t, [[0, 0], [17, 0], [26, 1240], [31, 1310], [42, 1330], [48, 2416]]))
  const interventions = t >= 39.9 ? 2 : t >= 32.5 ? 1 : 0
  const hours = (day % 1) * 24
  const hh = String(Math.floor(hours)).padStart(2, '0')
  const mm = String(Math.floor((hours % 1) * 60)).padStart(2, '0')
  return {
    running: t >= 17,
    day: Math.floor(day) + 1,
    clock: `${hh}:${mm}`,
    actions,
    interventions,
  }
}

// ---- Insight panels ----------------------------------------------------------

/** Recent insertion attempts: true succeeded. 25 of 40 succeed (62%). */
export const ATTEMPTS: boolean[] = Array.from({ length: 40 }, (_, i) => {
  const fails = new Set([3, 7, 9, 12, 15, 18, 19, 22, 26, 27, 30, 33, 35, 37, 39])
  return !fails.has(i)
})

/** Learning curves of three seeds, success rate 0..1 over normalized step s. */
export function seedCurve(seed: number, s: number) {
  const k = [7.2, 6.4, 8.1][seed]
  const mid = [0.42, 0.48, 0.38][seed]
  const top = [0.96, 0.94, 0.95][seed]
  const base = 0.12 + seed * 0.03
  const wobble = 0.018 * Math.sin(s * 37 + seed * 2.1) * (1 - s * 0.6)
  return clamp(base + (top - base) / (1 + Math.exp(-k * (s - mid))) + wobble)
}

export const trainProgress = (t: number) => ramp(t, 33.2, 38.4)

export const TEMPS = [30, 34, 37, 40]
/** Optical density over time (h) per temperature, logistic growth. */
export function growth(temp: number, h: number) {
  const rate = { 30: 0.09, 34: 0.13, 37: 0.17, 40: 0.11 }[temp] ?? 0.1
  const cap = { 30: 0.92, 34: 1.18, 37: 1.42, 40: 0.86 }[temp] ?? 1
  return 0.05 + cap / (1 + Math.exp(-rate * (h - 34)))
}

export const resultProgress = (t: number) => ramp(t, 44.6, 47.0)

export const LOOP_NODES = ['目标', '规划', '执行', '发现', '进化', '结果']

export function libraryAt(t: number) {
  const cards = [] as { kind: 'policy' | 'skill'; title: string; meta: string; at: number }[]
  if (t >= 40.6) cards.push({ kind: 'policy', title: '插管 v3', meta: '策略 · 评估 94/100', at: 40.6 })
  if (t >= 41.0) cards.push({ kind: 'skill', title: '离心前检查配平', meta: '技能 · agent 层', at: 41.0 })
  const count = t < 53 ? cards.length : Math.round(lerp(2, 1340, Math.pow(ramp(t, 53.5, 61), 1.6)))
  return { cards, count }
}

export function scaleAt(t: number) {
  const s = Math.pow(ramp(t, 53.2, 61), 1.5)
  return {
    labs: Math.max(1, Math.round(lerp(1, 1024, s))),
    goals: Math.max(1, Math.round(lerp(1, 3812, s))),
    skills: libraryAt(t).count,
  }
}

// ---- Scene labels ------------------------------------------------------------

export type Tone = 'plain' | 'accent' | 'warn' | 'bad' | 'good'

export interface LabelSpec { id: string; anchor: string; text: string; tone: Tone; o: number }

export function labelsAt(t: number): LabelSpec[] {
  const L: LabelSpec[] = []
  const sense = (at: number) => Math.min(ramp(t, at, at + 0.5), 1 - ramp(t, 16.6, 17.2) * 0.75)
  const hideAfter = 1 - ramp(t, 51.6, 52.6)
  const push = (id: string, anchor: string, text: string, tone: Tone, o: number) => {
    o *= hideAfter
    if (o > 0.01) L.push({ id, anchor, text, tone, o })
  }

  push('rack', 'rack', '试管架 · 8 管样本', 'plain', sense(5.9) * (1 - ramp(t, 17, 17.4)))
  push('arm', 'arm', t < 17 ? 'FR3 机械臂 · 空闲' : t < 26.4 ? 'FR3 · pickPlace' : t < 42.2 ? 'FR3 · 待命' : 'FR3 · 策略 插管 v3', t >= 42.2 ? 'accent' : 'plain', t < 17 ? sense(6.3) : window01(t, 17, 50.5) * (t > 31 && t < 42 ? 0 : 1))
  const cfText = t < 21.2 ? '离心机 · 0 rpm' : t < 22.4 ? '离心机 · 4,000 rpm' : '离心机 · 待机'
  push('cf', 'centrifuge', cfText, t >= 21.2 && t < 22.4 ? 'accent' : 'plain', t < 17 ? sense(6.8) : window01(t, 17, 26.4))
  push('cf-bad', 'centrifuge', '插入失败 · 对位偏差 2.4 mm', 'bad', window01(t, 26.9, 31.2, 0.3))
  push('cf-good', 'centrifuge', '插入成功 · 策略 插管 v3', 'good', window01(t, 43.0, 45.2, 0.3))
  push('inc', 'incubator', '培养箱 · 4 温区 30 / 34 / 37 / 40 °C', 'plain', t < 17 ? sense(7.3) : window01(t, 22.6, 25.6) * 0.9)
  push('bsc', 'cabinet', '生物安全柜 · 风速 0.45 m/s', 'plain', sense(7.8))
  push('envA', 'sensorA', '环境 · 22.4 °C · 41% RH', 'plain', sense(8.2))
  push('envB', 'sensorB', 'CO₂ · 412 ppm', 'plain', sense(8.6))
  push('det', 'detector', t < 44.6 ? '检测仪 · 缺校准记录' : '检测仪 · 读数 12 / 12', t < 44.6 ? 'warn' : 'good', t < 17 ? sense(9.2) : window01(t, 44.6, 48.4))
  push('best', 'chamber37', '37 °C · 生长最快', 'accent', window01(t, 46.8, 51.6))
  push('sim', 'sim', '训练视图 · 256 个并行环境', 'accent', window01(t, 32.6, 40.4))
  return L
}
