// A stylized isometric laboratory whose whole state is a pure
// function of the film clock. Geometry is procedural (no real FR3 meshes) and
// all colors come from the --m-* material tokens, re-read on theme change.
// The film opens with World Building over the idle lab; everything after it
// runs on the lab story's own clock.

import * as THREE from 'three'
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js'
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js'
import { BUILT, clamp, labT, lerp, ramp, smooth, window01 } from './story'

const V = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z)
const TAU = Math.PI * 2
const hash = (a: number, b = 0) => {
  const s = Math.sin(a * 127.1 + b * 311.7) * 43758.5453
  return s - Math.floor(s)
}

// ---- Tokens and materials ----------------------------------------------------

function token(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}
function col(name: string, fallback = '#888888') {
  const v = token(name)
  return new THREE.Color(v || fallback)
}
function num(name: string, fallback: number) {
  const v = parseFloat(token(name))
  return Number.isFinite(v) ? v : fallback
}

const std = (roughness = 0.7, metalness = 0, extra: THREE.MeshStandardMaterialParameters = {}) =>
  new THREE.MeshStandardMaterial({ roughness, metalness, ...extra })

class Mats {
  floor = std(0.95)
  floorSide = std(0.95)
  wall = std(0.92)
  body = std(0.48, 0.02)
  body2 = std(0.62)
  dark = std(0.5, 0.15)
  steel = std(0.3, 0.6)
  alu = std(0.4, 0.45)
  plate = std(0.72)
  glass = std(0.06, 0, { transparent: true, opacity: 0.24, depthWrite: false })
  medium = std(0.3)
  cap = std(0.45)
  leaf = std(0.85, 0, { flatShading: true })
  clay = std(0.9)
  simFloor = std(0.9, 0, { transparent: true })
  holo = std(0.55, 0, { transparent: true, opacity: 0.7 })
  ghost = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.16, depthWrite: false })
  led = new THREE.MeshBasicMaterial()
  warmGlow = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.85 })
  screen = new THREE.MeshBasicMaterial()
  ledOk = new THREE.MeshBasicMaterial()
  ledBad = new THREE.MeshBasicMaterial({ transparent: true })
  ledGood = new THREE.MeshBasicMaterial({ transparent: true })
  ring = new THREE.MeshBasicMaterial({ transparent: true, depthWrite: false })
  edge = new THREE.LineBasicMaterial({ transparent: true })
  edgeAccent = new THREE.LineBasicMaterial({ transparent: true })
  grid = new THREE.LineBasicMaterial({ transparent: true })
  simGrid = new THREE.LineBasicMaterial({ transparent: true })
  tape = new THREE.LineDashedMaterial({ transparent: true, dashSize: 0.12, gapSize: 0.08 })
  path = new THREE.LineDashedMaterial({ transparent: true, dashSize: 0.045, gapSize: 0.03 })
  buildFill = new THREE.MeshBasicMaterial({ transparent: true, depthWrite: false, side: THREE.DoubleSide })
  buildEdge = new THREE.LineBasicMaterial({ transparent: true, depthWrite: false })
  buildScan = new THREE.MeshBasicMaterial({ transparent: true, depthWrite: false, side: THREE.DoubleSide })
  buildOk = new THREE.MeshBasicMaterial({ transparent: true, depthWrite: false })
  ao: THREE.MeshBasicMaterial
  accent = new THREE.Color()
  ok = new THREE.Color()
  bad = new THREE.Color()
  warn = new THREE.Color()
  edgeO = 0.3
  edgeBase = new THREE.Color()
  dark01 = true

  constructor() {
    const c = document.createElement('canvas')
    c.width = c.height = 128
    const g = c.getContext('2d')!
    const grad = g.createRadialGradient(64, 64, 4, 64, 64, 64)
    grad.addColorStop(0, 'rgba(0,0,0,1)')
    grad.addColorStop(0.45, 'rgba(0,0,0,.55)')
    grad.addColorStop(1, 'rgba(0,0,0,0)')
    g.fillStyle = grad
    g.fillRect(0, 0, 128, 128)
    const tex = new THREE.CanvasTexture(c)
    this.ao = new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2 })
  }

  apply() {
    this.dark01 = document.documentElement.classList.contains('dark')
    this.floor.color = col('--m-floor')
    this.floorSide.color = col('--m-floor-side')
    this.wall.color = col('--m-wall')
    this.body.color = col('--m-body')
    this.body2.color = col('--m-body-2')
    this.dark.color = col('--m-dark')
    this.steel.color = col('--m-steel')
    this.alu.color = col('--m-alu')
    this.plate.color = col('--m-plate')
    this.glass.color = col('--m-glass')
    this.medium.color = col('--m-medium')
    this.cap.color = col('--m-cap')
    this.leaf.color = col('--m-leaf')
    this.clay.color = col('--c2').lerp(col('--m-body-2'), 0.55)
    this.accent = col('--accent')
    this.ok = col('--ok')
    this.bad = col('--bad')
    this.warn = col('--warn')
    this.simFloor.color = col('--m-floor').lerp(this.accent, 0.035)
    this.holo.color = col('--m-body').lerp(this.accent, 0.3)
    this.holo.emissive = this.accent.clone()
    this.holo.emissiveIntensity = this.dark01 ? 0.05 : 0.02
    this.ghost.color = this.accent
    this.led.color = this.accent
    this.screen.color = col('--m-dark').lerp(this.accent, 0.18)
    this.warmGlow.color = col('--warn').lerp(col('--m-sun'), 0.45)
    this.ledOk.color = this.ok
    this.ledGood.color = this.ok
    this.ledBad.color = this.bad
    this.ring.color = this.accent
    this.edgeO = num('--m-edge-o', 0.3)
    this.edgeBase = col('--m-edge')
    this.edge.color = this.edgeBase.clone()
    this.edge.opacity = this.edgeO
    this.buildFill.color = this.accent
    this.buildEdge.color = this.accent
    this.buildScan.color = this.accent
    this.buildOk.color = this.ok
    this.edgeAccent.color = this.accent
    this.grid.color = col('--m-grid')
    this.grid.opacity = this.dark01 ? 0.55 : 0.6
    this.simGrid.color = this.accent
    this.tape.color = this.warn
    this.tape.opacity = this.dark01 ? 0.45 : 0.6
    this.path.color = this.accent
    this.ao.color = col('--m-shadow')
    for (const m of [this.floor, this.floorSide, this.wall, this.body, this.body2, this.dark, this.steel, this.alu, this.plate, this.glass, this.medium, this.cap, this.leaf, this.clay, this.simFloor, this.holo]) m.needsUpdate = true
  }
}

// ---- Geometry helpers --------------------------------------------------------

function edgesOf(geo: THREE.BufferGeometry, mat: THREE.LineBasicMaterial, threshold = 30) {
  return new THREE.LineSegments(new THREE.EdgesGeometry(geo, threshold), mat)
}

/** Rounded box resting on y = 0 of its parent, with a crisp outline. */
function rbox(w: number, h: number, d: number, mat: THREE.Material, edge: THREE.LineBasicMaterial | null, r = 0.022) {
  const geo = new RoundedBoxGeometry(w, h, d, 2, Math.min(r, w / 2.2, h / 2.2, d / 2.2))
  geo.translate(0, h / 2, 0)
  const m = new THREE.Mesh(geo, mat)
  m.castShadow = true
  m.receiveShadow = true
  if (edge) {
    const eg = new THREE.BoxGeometry(w, h, d)
    eg.translate(0, h / 2, 0)
    m.add(edgesOf(eg, edge))
  }
  return m
}

function cyl(rt: number, rb: number, h: number, mat: THREE.Material, edge: THREE.LineBasicMaterial | null, seg = 40) {
  const geo = new THREE.CylinderGeometry(rt, rb, h, seg)
  geo.translate(0, h / 2, 0)
  const m = new THREE.Mesh(geo, mat)
  m.castShadow = true
  m.receiveShadow = true
  if (edge) m.add(edgesOf(geo, edge, 40))
  return m
}

function capsule(r: number, len: number, mat: THREE.Material) {
  const geo = new THREE.CapsuleGeometry(r, len, 6, 20)
  geo.translate(0, len / 2, 0)
  const m = new THREE.Mesh(geo, mat)
  m.castShadow = true
  return m
}

function aoBlob(mats: Mats, w: number, d: number, strength = 1) {
  const m = new THREE.Mesh(new THREE.PlaneGeometry(w, d), mats.ao)
  m.rotation.x = -Math.PI / 2
  m.position.y = 0.003
  m.renderOrder = 1
  m.userData.aoStrength = strength
  return m
}

function at<T extends THREE.Object3D>(o: T, x: number, y: number, z: number): T {
  o.position.set(x, y, z)
  return o
}

// ---- Robot arm ---------------------------------------------------------------

const L1 = 0.5
const L2 = 0.46
const COLUMN = 0.3
const TOOL = 0.19

class ArmRig {
  root = new THREE.Group()
  yaw = new THREE.Group()
  shoulder = new THREE.Group()
  elbow = new THREE.Group()
  wrist = new THREE.Group()
  fingerL: THREE.Mesh
  fingerR: THREE.Mesh
  baseRing: THREE.Mesh
  tcp = new THREE.Object3D()

  constructor(body: THREE.Material, dark: THREE.Material, led: THREE.Material, edge: THREE.LineBasicMaterial | null, public s = 1) {
    this.root.scale.setScalar(s)
    const base = cyl(0.13, 0.14, 0.07, dark, edge)
    this.root.add(base)
    this.baseRing = cyl(0.142, 0.142, 0.012, led, null)
    this.baseRing.position.y = 0.05
    this.root.add(this.baseRing)
    this.yaw.position.y = 0.07
    this.root.add(this.yaw)
    const column = capsule(0.072, COLUMN - 0.07 - 0.06, body)
    column.position.y = 0.02
    this.yaw.add(column)
    const sh = cyl(0.085, 0.085, 0.17, dark, edge, 28)
    sh.rotation.x = Math.PI / 2
    sh.position.set(0, COLUMN - 0.07, -0.085)
    this.yaw.add(sh)
    this.shoulder.position.y = COLUMN - 0.07
    this.yaw.add(this.shoulder)
    this.shoulder.add(capsule(0.064, L1, body))
    const el = cyl(0.07, 0.07, 0.15, dark, edge, 28)
    el.rotation.x = Math.PI / 2
    el.position.set(0, L1, -0.075)
    this.shoulder.add(el)
    this.elbow.position.y = L1
    this.shoulder.add(this.elbow)
    this.elbow.add(capsule(0.054, L2, body))
    const wr = cyl(0.056, 0.056, 0.12, dark, edge, 24)
    wr.rotation.x = Math.PI / 2
    wr.position.set(0, L2, -0.06)
    this.elbow.add(wr)
    this.wrist.position.y = L2
    this.elbow.add(this.wrist)
    this.wrist.add(cyl(0.045, 0.045, 0.07, dark, edge, 24))
    const palm = rbox(0.12, 0.035, 0.06, body, edge, 0.01)
    palm.position.y = 0.07
    this.wrist.add(palm)
    const fg = new THREE.BoxGeometry(0.016, 0.085, 0.034)
    fg.translate(0, 0.0425, 0)
    this.fingerL = new THREE.Mesh(fg, dark)
    this.fingerR = new THREE.Mesh(fg, dark)
    this.fingerL.castShadow = this.fingerR.castShadow = true
    this.fingerL.position.y = this.fingerR.position.y = 0.105
    this.wrist.add(this.fingerL, this.fingerR)
    this.tcp.position.y = TOOL
    this.wrist.add(this.tcp)
    this.grip(1)
  }

  grip(open: number) {
    const x = 0.018 + 0.026 * clamp(open)
    this.fingerL.position.x = -x
    this.fingerR.position.x = x
  }

  /** Elbow-up IK with the tool pointing straight down. */
  solve(target: THREE.Vector3) {
    const p = target.clone().sub(this.root.position).divideScalar(this.s)
    const yaw = Math.atan2(-p.z, p.x)
    const r = Math.max(0.14, Math.hypot(p.x, p.z))
    let wx = r
    let wy = p.y - COLUMN + TOOL
    let d = Math.hypot(wx, wy)
    const dmax = L1 + L2 - 0.002
    if (d > dmax) { wx *= dmax / d; wy *= dmax / d; d = dmax }
    const alpha = Math.atan2(wx, wy)
    const beta = Math.acos(clamp((L1 * L1 + d * d - L2 * L2) / (2 * L1 * d), -1, 1))
    const gamma = Math.acos(clamp((L1 * L1 + L2 * L2 - d * d) / (2 * L1 * L2), -1, 1))
    const q2 = alpha - beta
    const q3 = Math.PI - gamma
    const q4 = Math.PI - (q2 + q3)
    this.yaw.rotation.y = yaw
    this.shoulder.rotation.z = -q2
    this.elbow.rotation.z = -q3
    this.wrist.rotation.z = -q4
  }
}

// ---- Laboratory layout -------------------------------------------------------

const ARM_BASE = V(-0.05, 0.9, -0.15)
const HOME = V(0.12, 1.5, 0.12)
const GRASP = 0.13
const LIFT = 0.15
const RACK = V(-0.68, 0.9, 0.25)
const RACK_X = [-0.135, -0.045, 0.045, 0.135]
const RACK_Z = [-0.05, 0.05]
const CF = V(0.7, 0.9, -0.12)
const CF_R = 0.14
const CF_A = [Math.PI / 4, (3 * Math.PI) / 4, (5 * Math.PI) / 4, (7 * Math.PI) / 4]
const INC = V(-0.2, 0, -1.2)
const INC_FRONT = -0.85
const CH_X = [-0.47, 0.07, -0.47, 0.07]
const CH_Y = [1.38, 1.38, 1.0, 1.0]
const DET = V(0.55, 0.9, 0.4)
const PLATE = V(6.4, 0, -6.4)

type Slot = { k: 'rack'; i: number } | { k: 'cf'; i: number } | { k: 'inc'; i: number } | { k: 'det' } | { k: 'fail' }
const rack = (i: number): Slot => ({ k: 'rack', i })
const cf = (i: number): Slot => ({ k: 'cf', i })
const inc = (i: number): Slot => ({ k: 'inc', i })

interface Task { t0: number; dur: number; tube: number; from: Slot; to: Slot; fail?: boolean }

const TASKS: Task[] = [
  { t0: 17.3, dur: 1.9, tube: 0, from: rack(0), to: cf(0) },
  { t0: 19.2, dur: 1.6, tube: 1, from: rack(1), to: cf(2) },
  { t0: 22.8, dur: 1.1, tube: 0, from: cf(0), to: inc(2) },
  { t0: 23.9, dur: 0.9, tube: 1, from: cf(2), to: inc(0) },
  { t0: 24.8, dur: 0.6, tube: 2, from: rack(2), to: inc(1) },
  { t0: 25.4, dur: 0.55, tube: 3, from: rack(3), to: inc(3) },
  { t0: 26.1, dur: 1.5, tube: 4, from: rack(4), to: cf(0), fail: true },
  { t0: 42.4, dur: 1.2, tube: 4, from: { k: 'fail' }, to: cf(0) },
  { t0: 43.6, dur: 0.9, tube: 5, from: rack(5), to: cf(2) },
  { t0: 44.8, dur: 1.0, tube: 0, from: inc(2), to: { k: 'det' } },
]
const pickTime = (k: Task) => k.t0 + 0.42 * k.dur
const releaseTime = (k: Task) => k.t0 + 0.92 * k.dur

const SPINS: [number, number][] = [[21.2, 22.4], [45.0, 46.8]]

function lidOpen(t: number) {
  return ramp(t, 17.0, 17.25) - ramp(t, 20.9, 21.15) + ramp(t, 22.45, 22.7) - ramp(t, 44.6, 44.85)
}

function rotorAngle(t: number) {
  let a = 0
  for (const [s, e] of SPINS) a += TAU * 12 * smooth((t - s) / (e - s))
  return a
}

function drawerExt(i: number, t: number) {
  let e = 0
  for (const k of TASKS) {
    for (const [slot, tp] of [[k.from, pickTime(k)], [k.to, releaseTime(k)]] as [Slot, number][]) {
      if (slot.k === 'inc' && slot.i === i) e = Math.max(e, window01(t, tp - 0.55, tp + 0.4, 0.22))
    }
  }
  return e
}

function slotPos(s: Slot, t: number) {
  switch (s.k) {
    case 'rack': return V(RACK.x + RACK_X[s.i % 4], RACK.y + 0.012, RACK.z + RACK_Z[Math.floor(s.i / 4)])
    case 'cf': {
      const a = CF_A[s.i] + rotorAngle(t)
      return V(CF.x + CF_R * Math.cos(a), CF.y + 0.082, CF.z - CF_R * Math.sin(a))
    }
    case 'inc': return V(CH_X[s.i], CH_Y[s.i] - 0.16, INC_FRONT - 0.08 + drawerExt(s.i, t) * 0.26)
    case 'det': return V(DET.x, DET.y + 0.12, DET.z)
    case 'fail': return V(CF.x + CF_R * Math.cos(CF_A[0]) + 0.03, CF.y + 0.262, CF.z - CF_R * Math.sin(CF_A[0]) + 0.025)
  }
}

const grasp = (s: Slot, t: number) => slotPos(s, t).add(V(0, GRASP, 0))
const above = (s: Slot, t: number) => grasp(s, t).add(V(0, LIFT, 0))

/** Travel around the arm's base axis so the arm swings instead of folding through itself. */
function travel(a: THREE.Vector3, b: THREE.Vector3, s: number, lift = 0.08) {
  const ax = a.x - ARM_BASE.x, az = a.z - ARM_BASE.z
  const bx = b.x - ARM_BASE.x, bz = b.z - ARM_BASE.z
  const aa = Math.atan2(az, ax)
  let da = Math.atan2(bz, bx) - aa
  while (da > Math.PI) da -= TAU
  while (da < -Math.PI) da += TAU
  const e = smooth(s)
  const ang = aa + da * e
  const r = lerp(Math.hypot(ax, az), Math.hypot(bx, bz), e)
  return V(ARM_BASE.x + r * Math.cos(ang), lerp(a.y, b.y, e) + Math.sin(Math.PI * e) * lift, ARM_BASE.z + r * Math.sin(ang))
}

function taskTcp(k: Task, s: number, start: THREE.Vector3, t: number): { p: THREE.Vector3; open: number } {
  const from = k.from, to = k.to
  const gF = grasp(from, pickTime(k)), aF = above(from, pickTime(k))
  const gT = grasp(to, releaseTime(k)), aT = above(to, releaseTime(k))
  if (k.fail) gT.add(V(0.028, 0.03, 0.02))
  const seg = (a: number, b: number) => clamp((s - a) / (b - a))
  if (s < 0.3) return { p: travel(start, aF, seg(0, 0.3)), open: 1 }
  if (s < 0.4) return { p: aF.clone().lerp(gF, smooth(seg(0.3, 0.4))), open: 1 }
  if (s < 0.45) return { p: gF, open: 1 - seg(0.4, 0.45) }
  if (s < 0.53) return { p: gF.clone().lerp(aF, smooth(seg(0.45, 0.53))), open: 0 }
  if (s < 0.8) return { p: travel(aF, aT, seg(0.53, 0.8), 0.1), open: 0 }
  if (s < 0.9) return { p: aT.clone().lerp(gT, smooth(seg(0.8, 0.9))), open: 0 }
  if (s < 0.94) return { p: gT, open: seg(0.9, 0.94) }
  void t
  return { p: gT.clone().lerp(aT, smooth(seg(0.94, 1))), open: 1 }
}

function armTcp(t: number): { p: THREE.Vector3; open: number } {
  let prevEnd = HOME.clone()
  let prevT = -1
  for (const k of TASKS) {
    if (t < k.t0) break
    if (t < k.t0 + k.dur) {
      const start = idle(prevEnd, prevT, k.t0)
      return taskTcp(k, (t - k.t0) / k.dur, start, t)
    }
    prevEnd = taskTcp(k, 1, HOME, k.t0 + k.dur).p
    prevT = k.t0 + k.dur
  }
  return { p: idle(prevEnd, prevT, t), open: 1 }
}

function idle(prevEnd: THREE.Vector3, prevT: number, t: number) {
  if (prevT < 0) return HOME.clone()
  return travel(prevEnd, HOME, clamp((t - prevT) / 0.7), 0.04)
}

/** Where a tube is, and whether it is in the gripper. */
function tubeState(i: number, t: number): { slot: Slot | null; carried: boolean } {
  let slot: Slot = i < 8 ? rack(i) : rack(0)
  for (const k of TASKS) {
    if (k.tube !== i) continue
    if (t < pickTime(k)) break
    if (t < releaseTime(k)) return { slot: null, carried: true }
    slot = k.fail ? { k: 'fail' } : k.to
  }
  return { slot, carried: false }
}

// ---- Camera ------------------------------------------------------------------

interface Cam { t: number; target: THREE.Vector3; span: number; el: number; az: number }

const LAB_C = V(0.1, 0.85, -0.35)
const FILM: Cam[] = [
  { t: 0, target: LAB_C, span: 15, el: 33, az: 45 },
  { t: 5, target: LAB_C, span: 14, el: 33, az: 45 },
  { t: 10.6, target: LAB_C, span: 12.6, el: 33, az: 43 },
  { t: 12.4, target: V(0.0, 1.0, -0.2), span: 8.6, el: 31, az: 41 },
  { t: 16.6, target: V(0.0, 1.0, -0.2), span: 8.2, el: 31, az: 43 },
  { t: 18.2, target: V(0.05, 0.95, -0.35), span: 9.4, el: 32, az: 45 },
  { t: 25.6, target: V(0.05, 0.95, -0.35), span: 9.8, el: 32, az: 47 },
  { t: 26.8, target: V(0.62, 1.1, -0.1), span: 5.0, el: 30, az: 45 },
  { t: 30.4, target: V(0.62, 1.1, -0.1), span: 4.7, el: 30, az: 44 },
  { t: 32.2, target: PLATE.clone().add(V(0, 0.3, 0)), span: 15.5, el: 36, az: 45 },
  { t: 40.4, target: PLATE.clone().add(V(0, 0.3, 0)), span: 15, el: 36, az: 46 },
  { t: 42.4, target: V(0.05, 0.95, -0.35), span: 9.6, el: 32, az: 45 },
  { t: 47.6, target: V(0.0, 0.95, -0.45), span: 10.2, el: 32, az: 43 },
  { t: 49.4, target: LAB_C, span: 13.4, el: 34, az: 45 },
  { t: 52.6, target: LAB_C, span: 14, el: 35, az: 46 },
  { t: 54.6, target: V(0, 0.4, 0), span: 30, el: 40, az: 48 },
  { t: 58.0, target: V(0, 0, 0), span: 92, el: 47, az: 52 },
  { t: 62.0, target: V(0, 0, 0), span: 140, el: 50, az: 55 },
]

function filmCam(t: number): Omit<Cam, 't'> {
  if (t <= FILM[0].t) return FILM[0]
  for (let i = 1; i < FILM.length; i++) {
    const b = FILM[i]
    if (t <= b.t) {
      const a = FILM[i - 1]
      const s = smooth((t - a.t) / (b.t - a.t))
      return {
        target: a.target.clone().lerp(b.target, s),
        span: Math.exp(lerp(Math.log(a.span), Math.log(b.span), s)),
        el: lerp(a.el, b.el, s),
        az: lerp(a.az, b.az, s),
      }
    }
  }
  return FILM[FILM.length - 1]
}

// ---- Stage -------------------------------------------------------------------

export interface StageOptions {
  reducedMotion?: boolean
}

export interface Projected { x: number; y: number; visible: boolean }

interface Tube { g: THREE.Group; liquid: THREE.Mesh }
interface MiniEnv { g: THREE.Group; arm: ArmRig; tube: THREE.Group; tubeCap: THREE.Mesh; cf: THREE.Vector3; home: THREE.Vector3; seed: number }
interface Cell { x: number; z: number; d: number; variant: number; appear: number; idx: number; good: boolean }

export class LabStage {
  renderer: THREE.WebGLRenderer
  scene = new THREE.Scene()
  camera = new THREE.OrthographicCamera(-1, 1, 1, -1, -200, 400)
  mats = new Mats()
  hemi = new THREE.HemisphereLight()
  key = new THREE.DirectionalLight()
  lab = new THREE.Group()
  arm: ArmRig
  ghostArm: ArmRig
  tubes: Tube[] = []
  lid = new THREE.Group()
  rotor = new THREE.Group()
  drawers: THREE.Group[] = []
  chamberGlow: THREE.Mesh[] = []
  detBars: THREE.Mesh[] = []
  scanRing: THREE.Mesh
  entityRings: { m: THREE.Mesh; at: number }[] = []
  sensorPulses: THREE.Mesh[] = []
  failRing: THREE.Mesh
  goodRing: THREE.Mesh
  targetRings: THREE.Mesh[] = []
  pathLine: THREE.Line
  pathCount = 0
  sim = new THREE.Group()
  envs: MiniEnv[] = []
  capsule: THREE.Mesh
  cityGroup = new THREE.Group()
  cells: Cell[] = []
  cityMeshes: { mesh: THREE.InstancedMesh; variant: number }[] = []
  dots: THREE.InstancedMesh
  glow: THREE.Points
  flow: THREE.Points
  flowPairs: { a: Cell; b: Cell; phase: number; speed: number }[] = []
  waves: THREE.Mesh[] = []
  anchors: Record<string, THREE.Vector3> = {}
  build = new THREE.Group()
  scanPlane = new THREE.Group()
  volumes: { g: THREE.Object3D; at: number }[] = []
  joints: { g: THREE.Object3D; at: number }[] = []
  axes: THREE.AxesHelper[] = []
  validRing: THREE.Mesh
  width = 1
  height = 1
  offsetX = 0
  offsetY = 0
  opts: StageOptions
  power = 1
  private dummy = new THREE.Object3D()

  constructor(canvas: HTMLCanvasElement, opts: StageOptions = {}) {
    this.opts = opts
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' })
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    this.renderer.outputColorSpace = THREE.SRGBColorSpace
    this.renderer.toneMapping = THREE.NeutralToneMapping
    this.renderer.shadowMap.enabled = true
    this.renderer.shadowMap.type = THREE.PCFShadowMap
    this.renderer.setClearColor(0x000000, 0)

    this.scene.add(this.hemi, this.key, this.key.target)
    this.key.castShadow = true
    this.key.shadow.mapSize.set(2048, 2048)
    this.key.shadow.radius = 5
    this.key.shadow.blurSamples = 16
    this.key.shadow.bias = -0.0004
    this.key.shadow.normalBias = 0.02
    const sc = this.key.shadow.camera
    sc.left = -5; sc.right = 5; sc.top = 5; sc.bottom = -5; sc.near = 0.5; sc.far = 30

    const m = this.mats
    this.arm = new ArmRig(m.body, m.dark, m.led, m.edge)
    this.ghostArm = new ArmRig(m.ghost, m.ghost, m.ghost, null)
    this.ghostArm.root.traverse((o) => { o.castShadow = false })
    this.scanRing = this.ringMesh(1, 0.012)
    this.failRing = this.ringMesh(0.05, 0.006, m.ledBad)
    this.goodRing = this.ringMesh(0.05, 0.006, m.ledGood)
    this.validRing = this.ringMesh(1, 0.012, m.buildOk)
    this.capsule = new THREE.Mesh(new RoundedBoxGeometry(0.16, 0.08, 0.08, 2, 0.03), m.led)
    this.pathLine = new THREE.Line(new THREE.BufferGeometry(), m.path)
    this.dots = this.glow = this.flow = undefined as never

    this.buildLab()
    this.buildWorld()
    this.buildSim()
    this.buildCity()
    this.scene.add(this.lab, this.sim, this.cityGroup)
    this.applyTheme()
  }

  // ---- build ----------------------------------------------------------------

  private ringMats: THREE.MeshBasicMaterial[] = []
  private accentRing() {
    const m = this.mats.ring.clone()
    this.ringMats.push(m)
    return m
  }

  private ringMesh(r: number, tube: number, mat?: THREE.Material) {
    const ring = new THREE.Mesh(new THREE.TorusGeometry(r, tube, 8, 96), mat ?? this.accentRing())
    ring.rotation.x = -Math.PI / 2
    ring.renderOrder = 3
    return ring
  }

  private buildLab() {
    const m = this.mats
    const E = m.edge
    const lab = this.lab

    // Floor slab, walls and grid.
    lab.add(at(rbox(6.0, 0.16, 4.6, m.floor, E, 0.03), 0, -0.16, 0))
    const side = new THREE.Mesh(new THREE.BoxGeometry(6.0, 0.6, 4.6), m.floorSide)
    side.position.y = -0.46
    lab.add(side)
    lab.add(at(rbox(6.0, 1.55, 0.1, m.wall, E, 0.01), 0, 0, -2.25))
    lab.add(at(rbox(0.1, 1.55, 4.6, m.wall, E, 0.01), -2.95, 0, 0))
    const grid: number[] = []
    for (let x = -2.8; x <= 2.81; x += 0.4) grid.push(x, 0.002, -2.2, x, 0.002, 2.3)
    for (let z = -2.0; z <= 2.31; z += 0.4) grid.push(-2.9, 0.002, z, 3.0, 0.002, z)
    const gg = new THREE.BufferGeometry()
    gg.setAttribute('position', new THREE.Float32BufferAttribute(grid, 3))
    lab.add(new THREE.LineSegments(gg, m.grid))

    // Safety tape around the work cell.
    const tp = [V(-1.55, 0.004, -0.72), V(1.55, 0.004, -0.72), V(1.55, 0.004, 1.05), V(-1.55, 0.004, 1.05), V(-1.55, 0.004, -0.72)]
    const tape = new THREE.Line(new THREE.BufferGeometry().setFromPoints(tp), m.tape)
    tape.computeLineDistances()
    lab.add(tape)

    // Bench.
    lab.add(at(aoBlob(m, 3.3, 1.9, 0.9), 0, 0, 0.05))
    lab.add(at(rbox(2.5, 0.82, 1.15, m.body2, E, 0.02), 0, 0.04, 0.05))
    lab.add(at(rbox(2.42, 0.04, 1.07, m.dark, null, 0.01), 0, 0, 0.05))
    lab.add(at(rbox(2.56, 0.045, 1.2, m.plate, E, 0.012), 0, 0.855, 0.05))
    for (const x of [-0.84, 0, 0.84]) {
      const door = new THREE.Mesh(new THREE.BoxGeometry(0.78, 0.62, 0.005), m.body)
      door.position.set(x, 0.42, 0.63)
      lab.add(door)
      door.add(edgesOf(new THREE.BoxGeometry(0.78, 0.62, 0.005), E))
      const handle = new THREE.Mesh(new THREE.BoxGeometry(0.22, 0.016, 0.02), m.alu)
      handle.position.set(x, 0.66, 0.64)
      lab.add(handle)
    }

    // Arm.
    this.arm.root.position.copy(ARM_BASE)
    this.ghostArm.root.position.copy(ARM_BASE)
    lab.add(this.arm.root, this.ghostArm.root)
    lab.add(at(aoBlob(m, 0.5, 0.5, 1), ARM_BASE.x, 0.902, ARM_BASE.z))

    // Tube rack and tubes.
    const rk = at(rbox(0.4, 0.05, 0.2, m.alu, E, 0.01), RACK.x, RACK.y, RACK.z)
    lab.add(rk)
    for (let i = 0; i < 6; i++) this.tubes.push(this.makeTube(m.glass, m.medium, m.cap, lab))

    // Centrifuge: body, deck, rotor, hinged lid, display.
    lab.add(at(aoBlob(m, 0.8, 0.8, 1), CF.x, 0.902, CF.z))
    lab.add(at(cyl(0.29, 0.3, 0.25, m.body, E, 56), CF.x, CF.y, CF.z))
    lab.add(at(cyl(0.29, 0.29, 0.012, m.dark, null, 56), CF.x, CF.y + 0.25, CF.z))
    lab.add(at(cyl(0.215, 0.215, 0.006, m.steel, null, 48), CF.x, CF.y + 0.258, CF.z))
    this.rotor.position.set(CF.x, CF.y + 0.08, CF.z)
    const rotorDisk = cyl(0.19, 0.19, 0.02, m.alu, null, 48)
    this.rotor.add(rotorDisk)
    for (const a of CF_A) {
      const hole = cyl(0.032, 0.032, 0.022, m.dark, null, 20)
      hole.position.set(CF_R * Math.cos(a), 0.002, -CF_R * Math.sin(a))
      this.rotor.add(hole)
    }
    const hub = cyl(0.04, 0.04, 0.05, m.steel, null, 24)
    this.rotor.add(hub)
    lab.add(this.rotor)
    this.lid.position.set(CF.x, CF.y + 0.262, CF.z - 0.29)
    const lidShell = cyl(0.29, 0.29, 0.034, m.body, E, 56)
    lidShell.position.z = 0.29
    this.lid.add(lidShell)
    const lidWin = cyl(0.16, 0.16, 0.006, m.glass, null, 48)
    lidWin.position.set(0, 0.034, 0.29)
    this.lid.add(lidWin)
    lab.add(this.lid)
    const disp = rbox(0.17, 0.065, 0.012, m.screen, null, 0.006)
    disp.position.set(CF.x, CF.y + 0.13, CF.z + 0.292)
    lab.add(disp)
    const cfLed = new THREE.Mesh(new THREE.SphereGeometry(0.009, 10, 8), m.led)
    cfLed.position.set(CF.x + 0.12, CF.y + 0.165, CF.z + 0.296)
    lab.add(cfLed)
    this.failRing.position.copy(slotPos(cf(0), 0)).setY(CF.y + 0.265)
    this.goodRing.position.copy(this.failRing.position)
    lab.add(this.failRing, this.goodRing)

    // Incubator: four chambers with drawers.
    lab.add(at(aoBlob(m, 1.6, 1.1, 1), INC.x, 0, INC.z))
    lab.add(at(rbox(1.14, 0.08, 0.66, m.dark, null, 0.01), INC.x, 0, INC.z))
    lab.add(at(rbox(1.16, 1.86, 0.7, m.body, E, 0.03), INC.x, 0.08, INC.z))
    lab.add(at(rbox(0.5, 0.06, 0.012, m.screen, null, 0.006), INC.x, 1.74, INC_FRONT + 0.006))
    for (let i = 0; i < 4; i++) {
      const cx = CH_X[i], cy = CH_Y[i]
      const recess = new THREE.Mesh(new THREE.BoxGeometry(0.48, 0.34, 0.01), m.dark)
      recess.position.set(cx, cy, INC_FRONT + 0.002)
      lab.add(recess)
      const glow = new THREE.Mesh(new THREE.PlaneGeometry(0.42, 0.26), m.warmGlow.clone())
      glow.position.set(cx, cy + 0.02, INC_FRONT + 0.008)
      lab.add(glow)
      this.chamberGlow.push(glow)
      const door = new THREE.Mesh(new THREE.BoxGeometry(0.48, 0.34, 0.012), m.glass)
      door.position.set(cx, cy, INC_FRONT + 0.016)
      door.add(edgesOf(new THREE.BoxGeometry(0.48, 0.34, 0.012), E))
      lab.add(door)
      const drawer = new THREE.Group()
      drawer.position.set(cx, cy - 0.17, INC_FRONT)
      const tray = rbox(0.4, 0.016, 0.28, m.alu, null, 0.006)
      tray.position.z = -0.08
      drawer.add(tray)
      const front = rbox(0.46, 0.05, 0.016, m.body2, E, 0.006)
      front.position.z = 0.06
      drawer.add(front)
      lab.add(drawer)
      this.drawers.push(drawer)
    }

    // Biosafety cabinet.
    const bsc = new THREE.Group()
    bsc.position.set(1.85, 0, -1.75)
    bsc.add(aoBlob(m, 1.9, 1.2, 1))
    for (const x of [-0.66, 0.66]) bsc.add(at(rbox(0.06, 0.62, 0.66, m.dark, null, 0.01), x, 0, 0))
    bsc.add(at(rbox(1.5, 1.08, 0.8, m.body, E, 0.03), 0, 0.62, 0))
    bsc.add(at(rbox(1.5, 0.22, 0.72, m.body2, E, 0.02), 0, 1.7, -0.04))
    const opening = new THREE.Mesh(new THREE.BoxGeometry(1.32, 0.62, 0.01), m.dark)
    opening.position.set(0, 1.08, 0.401)
    bsc.add(opening)
    const work = new THREE.Mesh(new THREE.BoxGeometry(1.3, 0.012, 0.5), m.steel)
    work.position.set(0, 0.8, 0.14)
    bsc.add(work)
    const strip = new THREE.Mesh(new THREE.BoxGeometry(1.2, 0.02, 0.02), m.warmGlow)
    strip.position.set(0, 1.36, 0.38)
    bsc.add(strip)
    const sash = new THREE.Mesh(new THREE.BoxGeometry(1.36, 0.5, 0.012), m.glass)
    sash.position.set(0, 1.2, 0.45)
    sash.rotation.x = -0.14
    sash.add(edgesOf(new THREE.BoxGeometry(1.36, 0.5, 0.012), E))
    bsc.add(sash)
    lab.add(bsc)

    // Detector.
    lab.add(at(aoBlob(m, 0.6, 0.5, 1), DET.x, 0.902, DET.z))
    lab.add(at(rbox(0.42, 0.2, 0.3, m.body, E, 0.02), DET.x, DET.y, DET.z))
    lab.add(at(cyl(0.04, 0.04, 0.012, m.dark, null, 24), DET.x, DET.y + 0.2, DET.z))
    const screen = rbox(0.2, 0.09, 0.01, m.screen, null, 0.005)
    screen.position.set(DET.x, DET.y + 0.06, DET.z + 0.152)
    lab.add(screen)
    for (let i = 0; i < 4; i++) {
      const bar = new THREE.Mesh(new THREE.BoxGeometry(0.03, 1, 0.004), m.led)
      bar.position.set(DET.x - 0.06 + i * 0.04, DET.y + 0.025, DET.z + 0.158)
      bar.scale.y = 0.001
      lab.add(bar)
      this.detBars.push(bar)
    }

    // Environment sensors, wall monitor, shelf, plant.
    const sensor = (x: number, y: number, z: number, ry: number, name: string) => {
      const g = new THREE.Group()
      g.position.set(x, y, z)
      g.rotation.y = ry
      g.add(at(rbox(0.12, 0.16, 0.04, m.body, E, 0.012), 0, -0.08, 0.02))
      const led = new THREE.Mesh(new THREE.SphereGeometry(0.012, 10, 8), m.led)
      led.position.set(0, 0.03, 0.045)
      g.add(led)
      const pulse = new THREE.Mesh(new THREE.TorusGeometry(0.1, 0.004, 8, 48), this.accentRing())
      pulse.position.set(0, 0, 0.05)
      g.add(pulse)
      this.sensorPulses.push(pulse)
      lab.add(g)
      this.anchors[name] = V(x, y + 0.14, z).add(V(0.05 * Math.sin(ry), 0, 0.05 * Math.cos(ry)))
    }
    sensor(-2.2, 1.2, -2.2, 0, 'sensorA')
    sensor(-2.9, 1.15, 1.0, Math.PI / 2, 'sensorB')
    const mon = at(rbox(0.86, 0.48, 0.03, m.dark, E, 0.012), -1.3, 0.9, -2.19)
    lab.add(mon)
    const pts: THREE.Vector3[] = []
    for (let i = 0; i <= 24; i++) pts.push(V(-1.66 + i * 0.03, 1.02 + 0.12 * (0.5 + 0.5 * Math.sin(i * 0.55)) * (i / 24), -2.17))
    const ml = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), m.edgeAccent)
    lab.add(ml)
    const shelf = at(rbox(0.26, 0.03, 1.3, m.alu, E, 0.006), -2.76, 1.22, -0.9)
    lab.add(shelf)
    const bottles = [[-1.4, 0.18, m.glass], [-1.25, 0.24, m.cap], [-1.1, 0.14, m.glass], [-0.9, 0.2, m.medium], [-0.72, 0.16, m.glass], [-0.55, 0.22, m.body2], [-0.4, 0.15, m.glass]] as const
    for (const [z, h, mat] of bottles) lab.add(at(cyl(0.045, 0.05, h, mat, E, 20), -2.76, 1.25, z))
    const plant = new THREE.Group()
    plant.position.set(-2.55, 0, 1.85)
    plant.add(aoBlob(m, 0.7, 0.7, 1))
    plant.add(cyl(0.16, 0.12, 0.34, m.clay, E, 28))
    for (let i = 0; i < 7; i++) {
      const leaf = new THREE.Mesh(new THREE.IcosahedronGeometry(0.13 + hash(i) * 0.06, 0), m.leaf)
      const a = (i / 7) * TAU
      leaf.position.set(Math.cos(a) * 0.11, 0.5 + hash(i, 2) * 0.32, Math.sin(a) * 0.11)
      leaf.scale.set(0.8, 1.4, 0.8)
      leaf.castShadow = true
      plant.add(leaf)
    }
    lab.add(plant)

    // Agent perception rings, scan ring, plan path and target markers.
    this.scanRing.position.set(ARM_BASE.x, 0.01, ARM_BASE.z)
    lab.add(this.scanRing)
    const ringAt = (p: THREE.Vector3, r: number, at: number) => {
      const ring = this.ringMesh(r, 0.006)
      ring.position.copy(p)
      lab.add(ring)
      this.entityRings.push({ m: ring, at })
    }
    ringAt(V(RACK.x, RACK.y + 0.006, RACK.z), 0.3, 5.9)
    ringAt(V(ARM_BASE.x, ARM_BASE.y + 0.006, ARM_BASE.z), 0.28, 6.3)
    ringAt(V(CF.x, CF.y + 0.006, CF.z), 0.4, 6.8)
    ringAt(V(INC.x, 0.01, INC.z + 0.1), 0.8, 7.3)
    ringAt(V(1.85, 0.01, -1.6), 0.9, 7.8)
    ringAt(V(DET.x, DET.y + 0.006, DET.z), 0.3, 9.2)
    for (const p of [grasp(rack(0), 13), grasp(cf(0), 13)]) {
      const ring = this.ringMesh(0.05, 0.005)
      ring.position.copy(p).setY(p.y - GRASP + 0.02)
      lab.add(ring)
      this.targetRings.push(ring)
    }
    const pathPts: THREE.Vector3[] = []
    const k = TASKS[0]
    for (let i = 0; i <= 160; i++) pathPts.push(taskTcp(k, i / 160, HOME, k.t0).p)
    this.pathLine.geometry.setFromPoints(pathPts)
    this.pathLine.computeLineDistances()
    this.pathCount = pathPts.length
    lab.add(this.pathLine)

    this.capsule.add(edgesOf(new THREE.BoxGeometry(0.16, 0.08, 0.08), m.edgeAccent))
    this.scene.add(this.capsule)

    this.anchors.rack = V(RACK.x, 1.2, RACK.z)
    this.anchors.centrifuge = V(CF.x, 1.32, CF.z)
    this.anchors.incubator = V(INC.x, 2.06, INC.z)
    this.anchors.chamber37 = V(CH_X[2], CH_Y[2] + 0.2, INC_FRONT + 0.02)
    this.anchors.cabinet = V(1.85, 2.0, -1.7)
    this.anchors.detector = V(DET.x, 1.18, DET.z)
    this.anchors.sim = PLATE.clone().add(V(-1.2, 1.4, 1.2))
    this.anchors.arm = V()
  }

  /** World Building overlays: scan sweep, collision volumes, joint rings and axes. */
  private buildWorld() {
    const m = this.mats
    const scan = new THREE.Mesh(new THREE.PlaneGeometry(4.7, 2.3), m.buildScan)
    scan.add(new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.PlaneGeometry(4.7, 2.3)), m.buildEdge))
    scan.rotation.y = Math.PI / 2
    scan.position.y = 1.15
    scan.renderOrder = 4
    this.scanPlane.add(scan)
    this.build.add(this.scanPlane)

    const volume = (x: number, y: number, z: number, w: number, h: number, d: number, at: number) => {
      const geo = new THREE.BoxGeometry(w, h, d)
      geo.translate(0, h / 2, 0)
      const box = new THREE.Mesh(geo, m.buildFill)
      box.renderOrder = 4
      box.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo), m.buildEdge))
      box.position.set(x, y, z)
      this.build.add(box)
      this.volumes.push({ g: box, at })
    }
    volume(0, 0.03, 0.05, 2.66, 0.9, 1.3, 3.1)
    volume(RACK.x, RACK.y, RACK.z, 0.5, 0.26, 0.3, 3.5)
    volume(CF.x, CF.y, CF.z, 0.7, 0.34, 0.7, 3.7)
    volume(DET.x, DET.y, DET.z, 0.52, 0.28, 0.4, 4.0)
    volume(INC.x, 0.06, INC.z, 1.28, 1.96, 0.82, 4.2)
    volume(1.85, 0, -1.75, 1.62, 1.9, 0.92, 4.5)

    const joint = (parent: THREE.Object3D, y: number, z: number, r: number, at: number, flat = false) => {
      const mat = new THREE.MeshBasicMaterial({ color: m.accent, transparent: true, depthWrite: false })
      const ring = new THREE.Mesh(new THREE.TorusGeometry(r, 0.007, 8, 64), mat)
      ring.position.set(0, y, z)
      if (flat) ring.rotation.x = -Math.PI / 2
      ring.renderOrder = 5
      parent.add(ring)
      this.joints.push({ g: ring, at })
    }
    joint(this.arm.root, 0.05, 0, 0.22, 3.7, true)
    joint(this.arm.yaw, COLUMN - 0.07, -0.085, 0.13, 4.0)
    joint(this.arm.shoulder, L1, -0.075, 0.115, 4.2)
    joint(this.arm.elbow, L2, -0.06, 0.1, 4.5)

    for (const [x, y, z] of [[RACK.x, RACK.y, RACK.z], [CF.x, CF.y, CF.z], [DET.x, DET.y, DET.z], [INC.x, 0.08, INC.z + 0.4]] as const) {
      const ax = new THREE.AxesHelper(0.32)
      ax.position.set(x, y + 0.004, z)
      const mat = ax.material as THREE.LineBasicMaterial
      mat.transparent = true
      mat.depthTest = false
      ax.renderOrder = 6
      this.build.add(ax)
      this.axes.push(ax)
    }

    this.validRing.position.set(0, 0.012, 0)
    this.build.add(this.validRing)
    this.lab.add(this.build)
  }

  private makeTube(glass: THREE.Material, medium: THREE.Material, capMat: THREE.Material, parent: THREE.Object3D): Tube {
    const g = new THREE.Group()
    const shell = cyl(0.026, 0.022, 0.15, glass, null, 18)
    const liquid = cyl(0.02, 0.017, 0.085, medium, null, 16)
    liquid.position.y = 0.008
    const cap = cyl(0.03, 0.03, 0.028, capMat, null, 18)
    cap.position.y = 0.148
    g.add(shell, liquid, cap)
    parent.add(g)
    return { g, liquid }
  }

  private buildSim() {
    const m = this.mats
    this.sim.position.copy(PLATE)
    const plate = rbox(5.8, 0.08, 5.8, m.simFloor, m.edgeAccent, 0.04)
    plate.position.y = -0.08
    this.sim.add(plate)
    const grid: number[] = []
    for (let v = -2.7; v <= 2.71; v += 0.3) grid.push(v, 0.003, -2.8, v, 0.003, 2.8, -2.8, 0.003, v, 2.8, 0.003, v)
    const gg = new THREE.BufferGeometry()
    gg.setAttribute('position', new THREE.Float32BufferAttribute(grid, 3))
    this.sim.add(new THREE.LineSegments(gg, m.simGrid))
    for (let i = 0; i < 16; i++) {
      const g = new THREE.Group()
      const gx = (i % 4) - 1.5, gz = Math.floor(i / 4) - 1.5
      g.position.set(gx * 1.36, 0, gz * 1.36)
      g.add(rbox(1.0, 0.3, 0.56, m.holo, m.edgeAccent, 0.02))
      const arm = new ArmRig(m.holo, m.holo, m.led, m.edgeAccent, 0.42)
      arm.root.position.set(-0.18, 0.3, -0.06)
      g.add(arm.root)
      const cfm = cyl(0.12, 0.12, 0.1, m.holo, m.edgeAccent, 28)
      cfm.position.set(0.26, 0.3, 0.02)
      g.add(cfm)
      const tube = new THREE.Group()
      const shell = cyl(0.026, 0.022, 0.15, m.holo, null, 12)
      const cap = cyl(0.03, 0.03, 0.028, m.ledOk.clone(), null, 12)
      cap.position.y = 0.148
      tube.add(shell, cap)
      tube.scale.setScalar(0.9)
      g.add(tube)
      this.sim.add(g)
      this.envs.push({ g, arm, tube, tubeCap: cap, cf: V(0.26, 0.41, 0.02), home: V(-0.04, 0.56, 0.12), seed: i })
    }
  }

  private buildCity() {
    const R = 12
    for (let i = -R; i <= R; i++) {
      for (let j = -R; j <= R; j++) {
        if (i === 0 && j === 0) continue
        const d = Math.hypot(i, j * 0.85)
        const variant = Math.floor(hash(i, j) * 3)
        this.cells.push({ x: i * 7.6, z: j * 6.2, d, variant, appear: 0, idx: 0, good: hash(j, i) < 0.14 })
      }
    }
    this.cells.sort((a, b) => a.d - b.d)
    const maxD = this.cells[this.cells.length - 1].d
    this.cells.forEach((c, i) => {
      c.idx = i
      c.appear = 53.3 + 4.6 * Math.pow(c.d / maxD, 0.85) + hash(c.x, c.z) * 0.25
    })

    const m = this.mats
    const tiles = [0, 1, 2].map((v) => this.tileParts(v))
    tiles.forEach((parts, variant) => {
      const count = this.cells.filter((c) => c.variant === variant).length
      for (const { geo, mat } of parts) {
        const mesh = new THREE.InstancedMesh(geo, mat, count)
        mesh.castShadow = false
        mesh.receiveShadow = false
        mesh.frustumCulled = false
        this.cityGroup.add(mesh)
        this.cityMeshes.push({ mesh, variant })
      }
    })
    this.dots = new THREE.InstancedMesh(new THREE.SphereGeometry(0.2, 16, 12), new THREE.MeshBasicMaterial({ color: 0xffffff }), this.cells.length + 1)
    this.dots.frustumCulled = false
    this.cityGroup.add(this.dots)

    const spark = document.createElement('canvas')
    spark.width = spark.height = 64
    const g = spark.getContext('2d')!
    const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32)
    grad.addColorStop(0, 'rgba(255,255,255,1)')
    grad.addColorStop(0.25, 'rgba(255,255,255,.8)')
    grad.addColorStop(1, 'rgba(255,255,255,0)')
    g.fillStyle = grad
    g.fillRect(0, 0, 64, 64)
    const tex = new THREE.CanvasTexture(spark)

    const gpos = new Float32Array((this.cells.length + 1) * 3)
    const gg = new THREE.BufferGeometry()
    gg.setAttribute('position', new THREE.BufferAttribute(gpos, 3))
    this.glow = new THREE.Points(gg, new THREE.PointsMaterial({ map: tex, size: 30, sizeAttenuation: false, transparent: true, depthWrite: false, opacity: 0.5 }))
    this.glow.frustumCulled = false
    this.cityGroup.add(this.glow)

    const near = this.cells.filter((c) => c.d < 9)
    for (let i = 0; i < 420; i++) {
      const a = i % 7 === 0 ? null : near[Math.floor(hash(i, 1) * near.length)]
      const pool = a ? near.filter((c) => Math.abs(c.x - a.x) <= 15.2 && Math.abs(c.z - a.z) <= 12.4 && c !== a) : near.filter((c) => c.d < 3)
      const b = pool[Math.floor(hash(i, 2) * pool.length)] ?? near[0]
      const origin: Cell = { x: 0, z: 0, d: 0, variant: 0, appear: 53.0, idx: -1, good: false }
      this.flowPairs.push({ a: a ?? origin, b, phase: hash(i, 3), speed: 0.22 + hash(i, 4) * 0.25 })
    }
    const fg = new THREE.BufferGeometry()
    fg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(420 * 3), 3))
    this.flow = new THREE.Points(fg, new THREE.PointsMaterial({ map: tex, size: 7, sizeAttenuation: false, transparent: true, depthWrite: false }))
    this.flow.frustumCulled = false
    this.cityGroup.add(this.flow)

    for (let i = 0; i < 3; i++) {
      const w = new THREE.Mesh(new THREE.TorusGeometry(1, 0.012, 8, 160), this.accentRing())
      w.rotation.x = -Math.PI / 2
      w.position.y = 0.05
      this.cityGroup.add(w)
      this.waves.push(w)
    }
  }

  /** Low-poly lab for the city, merged per material. */
  private tileParts(variant: number) {
    const m = this.mats
    const parts: [THREE.BufferGeometry, THREE.Material][] = []
    const box = (w: number, h: number, d: number, x: number, y: number, z: number, mat: THREE.Material) => {
      const g = new THREE.BoxGeometry(w, h, d)
      g.translate(x, y + h / 2, z)
      parts.push([g, mat])
    }
    const cy = (r: number, h: number, x: number, y: number, z: number, mat: THREE.Material) => {
      const g = new THREE.CylinderGeometry(r, r, h, 14)
      g.translate(x, y + h / 2, z)
      parts.push([g, mat])
    }
    box(6.0, 0.16, 4.6, 0, -0.16, 0, m.floor)
    box(6.0, 1.55, 0.1, 0, 0, -2.25, m.wall)
    box(0.1, 1.55, 4.6, -2.95, 0, 0, m.wall)
    box(2.5, 0.86, 1.15, 0, 0, 0.05, m.body2)
    box(2.56, 0.045, 1.2, 0, 0.855, 0.05, m.plate)
    cy(0.29, 0.25, CF.x, 0.9, CF.z, m.body)
    cy(0.13, 0.07, ARM_BASE.x, 0.9, ARM_BASE.z, m.dark)
    box(0.12, 0.5, 0.12, ARM_BASE.x, 0.97, ARM_BASE.z, m.body)
    box(0.5, 0.1, 0.1, ARM_BASE.x + 0.2, 1.42, ARM_BASE.z, m.body)
    if (variant !== 1) box(1.16, 1.94, 0.7, INC.x, 0, INC.z, m.body)
    if (variant !== 2) {
      box(1.5, 1.1, 0.8, 1.85, 0.62, -1.75, m.body)
      box(1.5, 0.62, 0.66, 1.85, 0, -1.75, m.dark)
    }
    if (variant === 1) {
      box(1.8, 0.86, 0.9, 1.6, 0, -1.65, m.body2)
      box(1.86, 0.045, 0.94, 1.6, 0.855, -1.65, m.plate)
    }
    if (variant === 2) box(1.16, 1.94, 0.7, -1.6, 0, -1.8, m.body)
    const byMat = new Map<THREE.Material, THREE.BufferGeometry[]>()
    for (const [g, mat] of parts) {
      if (!byMat.has(mat)) byMat.set(mat, [])
      byMat.get(mat)!.push(g)
    }
    return [...byMat].map(([mat, geos]) => ({ mat, geo: mergeGeometries(geos)! }))
  }

  // ---- theme and size -------------------------------------------------------

  applyTheme() {
    this.mats.apply()
    const m = this.mats
    this.hemi.color = col('--m-sky')
    this.hemi.groundColor = col('--m-gnd')
    this.key.color = col('--m-sun')
    this.baseHemi = num('--m-hemi', 0.8) * Math.PI * 0.82
    this.baseKey = num('--m-key', 0.9) * Math.PI * 1.08
    this.aoStrength = num('--m-ao', 0.5)
    m.ao.opacity = this.aoStrength * 0.7
    for (const r of this.ringMats) r.color.copy(m.accent)
    for (const j of this.joints) ((j.g as THREE.Mesh).material as THREE.MeshBasicMaterial).color.copy(m.accent)
    if (!this.dots) return
    this.cells.forEach((c, i) => this.dots.setColorAt(i, c.good ? m.ok : m.accent))
    this.dots.setColorAt(this.cells.length, m.accent)
    if (this.dots.instanceColor) this.dots.instanceColor.needsUpdate = true
    const blend = m.dark01 ? THREE.AdditiveBlending : THREE.NormalBlending
    for (const p of [this.glow, this.flow]) {
      const pm = p.material as THREE.PointsMaterial
      pm.color = m.accent.clone()
      pm.blending = blend
      pm.needsUpdate = true
    }
    ;(this.glow.material as THREE.PointsMaterial).opacity = m.dark01 ? 0.55 : 0.35
  }

  private baseHemi = 1.6
  private baseKey = 2.6
  private aoStrength = 0.5

  resize(w: number, h: number) {
    this.width = Math.max(1, w)
    this.height = Math.max(1, h)
    this.renderer.setSize(this.width, this.height, false)
  }

  /** Shift the lab on screen by a fraction of the view (x right, y up). */
  setOffset(x: number, y = 0) {
    this.offsetX = x
    this.offsetY = y
  }

  // ---- per-frame ------------------------------------------------------------

  update(film: number, now: number) {
    const m = this.mats
    const amb = this.opts.reducedMotion ? 0 : now
    const t = labT(film)

    // Camera.
    const cam = filmCam(t)
    const aspect = this.width / this.height
    const span = cam.span
    const viewH = Math.max(span / 1.78, (span * 0.82) / aspect)
    const viewW = viewH * aspect
    const c = this.camera
    c.left = -viewW / 2 - this.offsetX * viewW
    c.right = viewW / 2 - this.offsetX * viewW
    c.top = viewH / 2 - this.offsetY * viewH
    c.bottom = -viewH / 2 - this.offsetY * viewH
    const az = THREE.MathUtils.degToRad(cam.az + 1.4 * Math.sin(amb * 0.11))
    const el = THREE.MathUtils.degToRad(cam.el)
    const dir = V(Math.sin(az) * Math.cos(el), Math.sin(el), Math.cos(az) * Math.cos(el))
    c.position.copy(cam.target).addScaledVector(dir, 60)
    c.lookAt(cam.target)
    c.updateProjectionMatrix()

    // Lights come on as the agent connects.
    this.power = lerp(0.62, 1, ramp(t, 4.4, 5.8))
    this.hemi.intensity = this.baseHemi * lerp(0.7, 1, this.power)
    this.key.intensity = this.baseKey * this.power
    const lightC = cam.span > 20 ? V(cam.target.x, 0, cam.target.z) : cam.target.clone()
    this.key.position.copy(lightC).add(V(-3.2, 8, 4.6))
    this.key.target.position.copy(lightC)
    const sc = this.key.shadow.camera
    const ext = cam.span > 12 ? 10 : 5
    sc.left = -ext; sc.right = ext; sc.top = ext; sc.bottom = -ext
    sc.updateProjectionMatrix()
    const ledOn = ramp(t, 4.8, 5.4)
    m.led.color.copy(m.accent).lerp(m.dark.color, 1 - ledOn)

    // Arm.
    const tcp = armTcp(t)
    const breathe = t > 47.5 || t < 17 ? V(0.015 * Math.sin(amb * 0.7), 0.012 * Math.sin(amb * 0.9), 0) : V()
    this.arm.solve(tcp.p.clone().add(breathe))
    this.arm.grip(tcp.open)
    const busy = TASKS.some((k) => t >= k.t0 && t < k.t0 + k.dur)
    this.arm.baseRing.visible = t > 4.8
    const tipWorld = this.arm.tcp.getWorldPosition(V())
    this.arm.elbow.getWorldPosition(this.anchors.arm).add(V(0, 0.28, 0))
    void busy

    // Ghost preview walks the first task's path.
    const gp = window01(t, 13.0, 16.9, 0.4)
    this.ghostArm.root.visible = gp > 0.01
    if (gp > 0.01) {
      const s = clamp((t - 13.2) / 3.3)
      const g = taskTcp(TASKS[0], s, HOME, TASKS[0].t0)
      this.ghostArm.solve(g.p)
      this.ghostArm.grip(g.open)
      m.ghost.opacity = 0.2 * gp
    }
    const pv = window01(t, 11.9, 17.6, 0.5)
    this.pathLine.visible = pv > 0.01
    this.pathLine.geometry.setDrawRange(0, Math.floor(this.pathCount * ramp(t, 12.0, 13.6)))
    m.path.opacity = 0.95 * pv
    for (const r of this.targetRings) {
      r.visible = pv > 0.01
      ;(r.material as THREE.MeshBasicMaterial).opacity = pv * (0.6 + 0.4 * Math.sin(amb * 3))
    }

    // Centrifuge.
    this.lid.rotation.x = -1.25 * clamp(lidOpen(t))
    this.rotor.rotation.y = rotorAngle(t)

    // Incubator drawers and chamber glow.
    this.drawers.forEach((d, i) => { d.position.z = INC_FRONT + drawerExt(i, t) * 0.26 })
    const bestGlow = window01(t, 46.8, 51.8)
    this.chamberGlow.forEach((g, i) => {
      const mat = g.material as THREE.MeshBasicMaterial
      const on = ramp(t, 5.0 + i * 0.12, 5.5 + i * 0.12)
      mat.color.copy(m.warmGlow.color).lerp(m.dark.color, 1 - on)
      if (i === 2 && bestGlow > 0) mat.color.lerp(m.accent, bestGlow * 0.7)
      mat.opacity = lerp(0.45, 0.9, on)
    })

    // Tubes.
    this.tubes.forEach((tube, i) => {
      const st = tubeState(i, t)
      tube.g.rotation.set(0, 0, 0)
      if (st.carried) tube.g.position.copy(tipWorld).add(V(0, -GRASP, 0))
      else if (st.slot) {
        tube.g.position.copy(slotPos(st.slot, t))
        if (st.slot.k === 'fail') tube.g.rotation.set(0.25, 0, -0.9)
      }
    })
    const fr = window01(t, 26.95, 31.0, 0.25)
    this.failRing.visible = fr > 0.01
    this.failRing.scale.setScalar(1 + 0.6 * ((amb * 1.4) % 1))
    ;(this.failRing.material as THREE.MeshBasicMaterial).opacity = fr * (1 - ((amb * 1.4) % 1))
    const gr = window01(t, 43.0, 44.6, 0.2)
    this.goodRing.visible = gr > 0.01
    this.goodRing.scale.setScalar(1 + 1.2 * clamp((t - 43.0) / 1.2))
    ;(this.goodRing.material as THREE.MeshBasicMaterial).opacity = gr * (1 - clamp((t - 43.0) / 1.4))

    // Detector bars.
    this.detBars.forEach((b, i) => {
      const h = [0.32, 0.55, 0.9, 0.42][i] * ramp(t, 45.6 + i * 0.15, 46.4 + i * 0.15)
      b.scale.y = Math.max(0.001, h * 0.06)
      b.position.y = DET.y + 0.025 + b.scale.y / 2
    })

    // Perception.
    const scan = clamp((t - 5.2) / 2.2)
    this.scanRing.visible = scan > 0 && scan < 1
    this.scanRing.scale.setScalar(0.2 + scan * 4.6)
    ;(this.scanRing.material as THREE.MeshBasicMaterial).opacity = 0.45 * Math.sin(Math.PI * scan)
    for (const r of this.entityRings) {
      const k = clamp((t - r.at) / 1.1)
      r.m.visible = k > 0 && k < 1
      r.m.scale.setScalar(0.8 + 0.5 * k)
      ;(r.m.material as THREE.MeshBasicMaterial).opacity = Math.sin(Math.PI * k) * 0.9
    }
    const sp = window01(t, 7.9, 11.5)
    this.sensorPulses.forEach((p, i) => {
      const k = (amb * 0.8 + i * 0.5) % 1
      p.scale.setScalar(0.6 + 1.4 * k)
      p.visible = sp > 0.01
      ;(p.material as THREE.MeshBasicMaterial).opacity = sp * (1 - k) * 0.8
    })

    // Simulation plate.
    this.updateSim(t, amb)

    // City.
    this.updateCity(t, amb)

    // World Building.
    this.updateBuild(film)

    // AO strength stays tied to the token.
    m.ao.opacity = this.aoStrength * 0.7
  }

  private updateBuild(film: number) {
    const m = this.mats
    const on = film < BUILT
    this.build.visible = on
    for (const j of this.joints) j.g.visible = on
    // Geometry: every edge in the lab lights up as the scan passes.
    const wire = on ? window01(film, 1.1, 6.2, 0.8) : 0
    m.edge.color.copy(m.edgeBase).lerp(m.accent, 0.6 * wire)
    m.edge.opacity = lerp(m.edgeO, 0.75, wire)
    if (!on) return
    const sweep = clamp((film - 1.0) / 2.3)
    this.scanPlane.visible = sweep > 0 && sweep < 1
    this.scanPlane.position.x = lerp(-3.1, 3.1, smooth(sweep))
    m.buildScan.opacity = 0.45 * Math.sin(Math.PI * sweep)
    // Physics: collision volumes, joint rings and axes, then they hand over to semantics.
    const phys = window01(film, 3.0, 8.0, 0.6)
    m.buildFill.opacity = 0.16 * phys
    m.buildEdge.opacity = Math.max(phys, Math.sin(Math.PI * sweep))
    for (const v of this.volumes) v.g.scale.setScalar(Math.max(0.001, lerp(0.92, 1, ramp(film, v.at, v.at + 0.35)) * (film >= v.at ? 1 : 0)))
    for (const j of this.joints) {
      const k = ramp(film, j.at, j.at + 0.4)
      j.g.scale.setScalar(Math.max(0.001, k))
      ;((j.g as THREE.Mesh).material as THREE.MeshBasicMaterial).opacity = 0.95 * k * window01(film, 0, 8.0, 0.6)
    }
    const axes = window01(film, 3.9, 7.7, 0.5)
    for (const a of this.axes) {
      a.visible = axes > 0.01
      ;(a.material as THREE.LineBasicMaterial).opacity = 0.85 * axes
    }
    // Validation: one pulse across the floor once the scene checks out.
    const v = clamp((film - 7.7) / 1.1)
    this.validRing.visible = v > 0 && v < 1
    this.validRing.scale.setScalar(0.4 + 3.4 * v)
    m.buildOk.opacity = 0.8 * (1 - v)
  }

  private updateSim(t: number, amb: number) {
    const m = this.mats
    const vis = window01(t, 31.0, 42.2, 0.9)
    this.sim.visible = vis > 0.01
    this.capsule.visible = false
    if (!this.sim.visible && !(t > 40.6 && t < 42.2)) return
    m.simFloor.opacity = 0.92 * vis
    m.simGrid.opacity = 0.14 * vis
    m.edgeAccent.opacity = 0.7 * vis
    m.holo.opacity = 0.5 * vis
    const p = lerp(0.41, 0.96, ramp(t, 33.2, 38.4))
    const collapse = ramp(t, 40.0, 40.9)
    this.envs.forEach((e) => {
      const appear = ramp(t, 32.6 + e.seed * 0.05, 33.0 + e.seed * 0.05)
      const s = Math.max(0.001, appear * (1 - collapse))
      e.g.scale.setScalar(s)
      const base = V(((e.seed % 4) - 1.5) * 1.36, 0, (Math.floor(e.seed / 4) - 1.5) * 1.36)
      e.g.position.copy(base.multiplyScalar(1 - collapse * 0.85))
      // One insertion attempt per period; success odds rise with training.
      const period = 1.35 + hash(e.seed, 7) * 0.4
      const local = (t - 33 + hash(e.seed, 9) * period) / period
      const n = Math.floor(local)
      const u = local - n
      const success = t < 33.2 ? false : hash(e.seed, n) < p
      const evalPhase = t > 38.6 && t < 39.8
      const offset = success || evalPhase ? V() : V(0.05 * (hash(e.seed, n + 3) - 0.5) * 2, 0.04, 0.05)
      const top = e.cf.clone().add(V(0, 0.16, 0))
      let target: THREE.Vector3
      if (u < 0.35) target = e.home.clone().lerp(top, smooth(u / 0.35))
      else if (u < 0.6) target = top.clone().lerp(e.cf.clone().add(offset), smooth((u - 0.35) / 0.25))
      else if (u < 0.75) target = e.cf.clone().add(offset)
      else target = e.cf.clone().add(offset).lerp(e.home, smooth((u - 0.75) / 0.25))
      e.arm.solve(target)
      e.arm.grip(u > 0.6 && u < 0.8 ? 1 : 0)
      const tip = target.clone().add(V(0, -GRASP * 0.42, 0))
      e.tube.position.copy(tip)
      const capM = e.tubeCap.material as THREE.MeshBasicMaterial
      const showResult = u > 0.58 && u < 0.92
      capM.color.copy(showResult ? (success || evalPhase ? m.ok : m.bad) : m.accent)
    })
    // The trained policy flies back to the arm.
    const fly = clamp((t - 40.8) / 1.2)
    if (fly > 0 && fly < 1) {
      this.capsule.visible = true
      const a = PLATE.clone().add(V(0, 0.6, 0))
      const b = ARM_BASE.clone().add(V(0, 0.2, 0))
      const mid = a.clone().lerp(b, 0.5).add(V(0, 3.2, 0))
      const e = smooth(fly)
      const p1 = a.clone().lerp(mid, e)
      const p2 = mid.clone().lerp(b, e)
      this.capsule.position.copy(p1.lerp(p2, e))
      this.capsule.rotation.y = amb * 3
      this.capsule.scale.setScalar(1.6 - fly * 0.8)
    }
  }

  private updateCity(t: number, amb: number) {
    const on = t > 52.8
    this.cityGroup.visible = on
    if (!on) return
    const counters = [0, 0, 0]
    const d = this.dummy
    const glowPos = this.glow.geometry.getAttribute('position') as THREE.BufferAttribute
    for (const c of this.cells) {
      const k = clamp((t - c.appear) / 0.8)
      const a = 1 - Math.pow(1 - k, 3)
      const slot = counters[c.variant]++
      d.position.set(c.x, -0.8 * (1 - a), c.z)
      d.rotation.set(0, 0, 0)
      d.scale.setScalar(Math.max(0.001, a))
      d.updateMatrix()
      for (const cm of this.cityMeshes) if (cm.variant === c.variant) cm.mesh.setMatrixAt(slot, d.matrix)
      const pulse = 1 + 0.25 * Math.sin(amb * 2.2 + c.idx)
      d.position.set(c.x + ARM_BASE.x, 2.5, c.z + ARM_BASE.z)
      d.scale.setScalar(Math.max(0.001, a * pulse))
      d.updateMatrix()
      this.dots.setMatrixAt(c.idx, d.matrix)
      glowPos.setXYZ(c.idx, c.x + ARM_BASE.x, a > 0.5 ? 2.5 : -999, c.z + ARM_BASE.z)
    }
    d.position.set(ARM_BASE.x, 2.5, ARM_BASE.z)
    d.scale.setScalar(ramp(t, 53, 53.6) * (1 + 0.25 * Math.sin(amb * 2.2)))
    d.updateMatrix()
    this.dots.setMatrixAt(this.cells.length, d.matrix)
    glowPos.setXYZ(this.cells.length, ARM_BASE.x, t > 53.2 ? 2.5 : -999, ARM_BASE.z)
    glowPos.needsUpdate = true
    for (const cm of this.cityMeshes) cm.mesh.instanceMatrix.needsUpdate = true
    this.dots.instanceMatrix.needsUpdate = true

    const fp = this.flow.geometry.getAttribute('position') as THREE.BufferAttribute
    const flowOn = ramp(t, 54.6, 56.4)
    ;(this.flow.material as THREE.PointsMaterial).opacity = flowOn
    this.flowPairs.forEach((f, i) => {
      const live = t > Math.max(f.a.appear, f.b.appear) + 0.6
      if (!live) { fp.setXYZ(i, 0, -999, 0); return }
      const s = (amb * f.speed + f.phase) % 1
      const A = V(f.a.x + ARM_BASE.x, 2.5, f.a.z + ARM_BASE.z)
      const B = V(f.b.x + ARM_BASE.x, 2.5, f.b.z + ARM_BASE.z)
      const mid = A.clone().lerp(B, 0.5).add(V(0, 2.4 + A.distanceTo(B) * 0.12, 0))
      const p1 = A.clone().lerp(mid, s)
      const p2 = mid.clone().lerp(B, s)
      const p = p1.lerp(p2, s)
      fp.setXYZ(i, p.x, p.y, p.z)
    })
    fp.needsUpdate = true

    this.waves.forEach((w, i) => {
      const k = clamp((t - 53.8 - i * 1.5) / 4.2)
      w.visible = k > 0 && k < 1
      w.scale.setScalar(2 + k * 70)
      ;(w.material as THREE.MeshBasicMaterial).opacity = (1 - k) * 0.65
    })
  }

  render() {
    this.renderer.render(this.scene, this.camera)
  }

  /** Screen position of a named anchor, in CSS pixels from the top-left. */
  project(name: string): Projected {
    const a = this.anchors[name]
    if (!a) return { x: 0, y: 0, visible: false }
    const p = a.clone().project(this.camera)
    const x = (p.x * 0.5 + 0.5) * this.width
    const y = (-p.y * 0.5 + 0.5) * this.height
    return { x, y, visible: x > -40 && x < this.width + 40 && y > -20 && y < this.height + 20 }
  }

  /** Render the lab at `t`, centered and at a fixed size, as an image URL. */
  still(t: number, w: number, h: number) {
    const { width, height, offsetX, offsetY } = this
    const ratio = this.renderer.getPixelRatio()
    this.renderer.setPixelRatio(1)
    this.setOffset(0, 0)
    this.resize(w, h)
    this.update(t, 0)
    this.render()
    const url = this.renderer.domElement.toDataURL('image/webp', 0.86)
    this.renderer.setPixelRatio(ratio)
    this.setOffset(offsetX, offsetY)
    this.resize(width, height)
    return url
  }

  dispose() {
    this.scene.traverse((o) => {
      const mesh = o as THREE.Mesh
      mesh.geometry?.dispose?.()
      const mat = mesh.material as THREE.Material | THREE.Material[] | undefined
      if (Array.isArray(mat)) mat.forEach((x) => x.dispose())
      else mat?.dispose?.()
    })
    this.renderer.dispose()
    this.renderer.forceContextLoss()
  }
}

