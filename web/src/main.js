// Squipi — a small flapjack octopus that lives on the desktop.
// Original character for the Squipi project (MIT). The scene scaffolding (transparent canvas,
// toon + outline shading, spring-driven squash and stretch, the pet.* contract) follows the
// pattern of Ribbit's frog by Dmitry Pyanov (MIT), see NOTICE.md.
import * as THREE from 'three'
import { makeClothes } from './wardrobe.js'

// ---------- setup ----------
const params = new URLSearchParams(location.search)
const VSIZE = parseInt(params.get('size') || '0', 10)
const VW = parseInt(params.get('w') || '0', 10) || VSIZE, VH = parseInt(params.get('h') || '0', 10) || VSIZE
const W = VW || 320, H = VH || 320
if (params.get('bg')) document.body.classList.add('bg')
if (params.get('white')) document.body.classList.add('white')

const canvas = document.getElementById('c')
if (VW) { canvas.width = W; canvas.height = H; canvas.style.width = W + 'px'; canvas.style.height = H + 'px'; document.body.style.width = W + 'px'; document.body.style.height = H + 'px' }
const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true })
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
renderer.setSize(W, H, false)
renderer.setClearColor(0x000000, 0)
renderer.outputColorSpace = THREE.SRGBColorSpace

const scene = new THREE.Scene()
const camera = new THREE.PerspectiveCamera(30, W / H, 0.1, 50)
camera.position.set(0, 0.75, 9.2)
camera.lookAt(0, -0.05, 0)

// soft studio light: cool key from the upper left, warm fill, a blue rim from behind
scene.add(new THREE.HemisphereLight(0xfaf6ff, 0xcbb9ef, 0.55))
scene.add(new THREE.AmbientLight(0xffffff, 0.38))
const key = new THREE.DirectionalLight(0xffffff, 1.6); key.position.set(-2.2, 4, 3.2); scene.add(key)
const fill = new THREE.DirectionalLight(0xfff0e6, 0.5); fill.position.set(3, 1, 2.5); scene.add(fill)
const rim = new THREE.DirectionalLight(0xd7edff, 0.9); rim.position.set(1.5, 2, -3.5); scene.add(rim)

function gradientMap(steps) {
  const data = new Uint8Array(steps.length * 4)
  steps.forEach((v, i) => { data[i * 4] = data[i * 4 + 1] = data[i * 4 + 2] = v; data[i * 4 + 3] = 255 })
  const t = new THREE.DataTexture(data, steps.length, 1, THREE.RGBAFormat)
  t.minFilter = t.magFilter = THREE.NearestFilter
  t.needsUpdate = true
  return t
}
const toon = gradientMap([128, 190, 232, 255])

// ---------- palette ----------
const INK = 0x173e68            // navy outlines and glasses, matching the app and Dock icon
const BLUE = 0x77bdf2           // soft blue mantle
const BLUE_DEEP = 0x4598d8      // ocean-blue fins underside and lids in shade
const BLUE_LIGHT = 0xbce5ff     // highlights and arm tips
const CREAM = 0xe7f5ff          // belly / web underside
const FIN_INNER = 0xe9b8dc      // pale sea-glass inside the ear-fins
const BLUSH = 0xf7a9c9
const EYE_INK = 0x152e4e
const MATCHA = 0x7fb069
const BOWL = 0xe8e0d2

const THEMES = {
  blue: { body: 0x77bdf2, deep: 0x4598d8, light: 0xbce5ff, cream: 0xe7f5ff, inner: 0xe9b8dc, skirt: 0x285c96 },
  lavender: { body: 0xb6a0df, deep: 0x75539d, light: 0xe3d6f7, cream: 0xf2eafa, inner: 0xd9c8ec },
  lime: { body: 0xa9d64d, deep: 0x527d0b, light: 0xdfefa9, cream: 0xf1f7da, inner: 0xd1e6a7 },
}
let themeName = 'blue'
let playPose = 'idle'
const hexColour = n => '#' + n.toString(16).padStart(6, '0')

const toonMat = (color) => new THREE.MeshToonMaterial({ color, gradientMap: toon })
const outlineMat = new THREE.MeshBasicMaterial({ color: INK, side: THREE.BackSide })
function withOutline(mesh, thick = 1.04) {
  const o = new THREE.Mesh(mesh.geometry, outlineMat)
  o.scale.setScalar(thick)
  o.name = 'outline'
  mesh.add(o)
  return mesh
}

// ---------- the octopus ----------
const pet = new THREE.Group()           // squash/stretch, hover, slide
const head = new THREE.Group()          // tilts toward the cursor
pet.add(head)
scene.add(pet)

// mantle: one soft dome, a touch wider than tall
const MX = 1.08, MY = 1.0, MZ = 1.0, MYOFF = 0.12
const mantle = withOutline(new THREE.Mesh(new THREE.SphereGeometry(1, 96, 96), toonMat(BLUE)), 1.03)
mantle.scale.set(MX, MY, MZ); mantle.position.y = MYOFF
head.add(mantle)

// a point on the mantle surface, pushed out a little
function facePoint(x, y, push = 0.02) {
  const yy = y - MYOFF
  const z = Math.sqrt(Math.max(0.0001, 1 - (x / MX) ** 2 - (yy / MY) ** 2)) * MZ + push
  return new THREE.Vector3(x, y, z)
}

// ear-fins: the flapjack's signature. Two soft paddles high on the sides, pale on the inside.
function makeFin(side) {
  const g = new THREE.Group()
  const paddle = withOutline(new THREE.Mesh(new THREE.SphereGeometry(1, 40, 40), toonMat(BLUE)), 1.05)
  paddle.scale.set(0.47, 0.32, 0.13)
  const inner = new THREE.Mesh(new THREE.SphereGeometry(1, 32, 32), toonMat(FIN_INNER))
  inner.scale.set(0.36, 0.22, 0.06); inner.position.set(side * -0.02, 0.0, 0.1)
  g.add(paddle, inner)
  // pivot sits where the fin meets the head
  const pivot = new THREE.Group()
  pivot.position.set(side * 0.9, 0.62 + MYOFF, -0.12)
  g.position.set(side * 0.42, 0.12, 0)
  pivot.add(g)
  pivot.userData = { side, base: { z: side * 0.55, y: side * 0.35, x: -0.1 } }
  head.add(pivot)
  return pivot
}
const finL = makeFin(-1), finR = makeFin(1)

// a little flower tucked in front of her left fin
const hairFlower = new THREE.Group()
{
  const petalMat = toonMat(0xffb3c6), centreMat = new THREE.MeshBasicMaterial({ color: 0xffd166 })
  for (let i = 0; i < 5; i++) {
    const a = i * Math.PI * 2 / 5
    const p = withOutline(new THREE.Mesh(new THREE.SphereGeometry(1, 16, 16), petalMat), 1.08)
    p.scale.set(0.085, 0.06, 0.035)
    p.position.set(Math.cos(a) * 0.1, Math.sin(a) * 0.1, 0); p.rotation.z = a
    hairFlower.add(p)
  }
  const centre = withOutline(new THREE.Mesh(new THREE.SphereGeometry(0.05, 14, 14), centreMat), 1.1)
  centre.position.z = 0.02
  hairFlower.add(centre)
  const p = facePoint(-0.66, 0.9, 0.03)
  hairFlower.position.copy(p); hairFlower.lookAt(p.clone().multiplyScalar(3)); hairFlower.scale.setScalar(1.7)
  head.add(hairFlower)
}

// arm skirt: eight short arms joined by a web, built as a lathe that we scallop by hand.
const ARMS = 8
function makeSkirt() {
  const profile = []
  const n = 14
  for (let i = n; i >= 0; i--) {         // hem first, so the lathe's normals face outward
    const u = i / n                      // 0 at the mantle, 1 at the hem
    const r = 0.58 + 0.72 * Math.sin(u * Math.PI * 0.5)        // flares out
    const y = -0.22 - 0.78 * u + 0.12 * Math.sin(u * Math.PI)   // sags in the middle
    profile.push(new THREE.Vector2(r, y))
  }
  const geo = new THREE.LatheGeometry(profile, 128)
  const pos = geo.attributes.position
  const v = new THREE.Vector3()
  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i)
    const th = Math.atan2(v.z, v.x)
    const r = Math.hypot(v.x, v.z)
    const u = Math.max(0, Math.min(1, (-0.22 - v.y) / 0.9))     // 0 top .. 1 hem
    const lobe = Math.pow(0.5 + 0.5 * Math.cos(th * ARMS), 1.6)  // 1 at each arm, 0 between, narrow petals
    const w = u * u
    const rr = r * (1 + 0.22 * lobe * w - 0.09 * (1 - lobe) * w)
    v.x = Math.cos(th) * rr; v.z = Math.sin(th) * rr
    v.y -= 0.2 * lobe * w                                       // arm tips hang below the web
    v.y += 0.05 * (1 - lobe) * w                                // the web between them lifts a little
    pos.setXYZ(i, v.x, v.y, v.z)
  }
  geo.computeVertexNormals()
  return geo
}
const skirtGeo = makeSkirt()

// Her skirt is a wardrobe choice: plain blue, a small floral print, deep ocean blue, ocean blue
// with flowers, or a star chart. Every pattern is drawn once on a canvas, nothing is loaded.
const SKIRT_LOOKS = ['plain', 'floral', 'dark', 'darkFloral', 'stars']
const DARK_SKIRT = '#2b73a6'
function skirtTexture(look) {
  const c = document.createElement('canvas'); c.width = 1024; c.height = 512
  const ctx = c.getContext('2d')
  const dark = look === 'dark' || look === 'darkFloral' || look === 'stars'
  ctx.fillStyle = hexColour(dark ? (THEMES[themeName].skirt || THEMES[themeName].deep) : THEMES[themeName].body); ctx.fillRect(0, 0, c.width, c.height)
  let seed = 7
  const rnd = () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646 }
  if (look === 'floral' || look === 'darkFloral') {
    const petals = ['#ffb3c6', '#ffd166', '#ff9f7a', '#9ad0ff', '#c9f2b7', '#ffffff', '#ffc8dd']
    const centres = ['#ffe08a', '#ffb347', '#fff1b8', '#ff8fab']
    const flower = (x, y, r, col, cen, rot) => {
      for (let i = 0; i < 5; i++) {
        const a = rot + i * Math.PI * 2 / 5
        ctx.save(); ctx.translate(x + Math.cos(a) * r * 0.62, y + Math.sin(a) * r * 0.62); ctx.rotate(a)
        ctx.beginPath(); ctx.ellipse(0, 0, r * 0.52, r * 0.36, 0, 0, Math.PI * 2); ctx.fillStyle = col; ctx.fill(); ctx.restore()
      }
      ctx.beginPath(); ctx.arc(x, y, r * 0.3, 0, Math.PI * 2); ctx.fillStyle = cen; ctx.fill()
    }
    // the hem is v = 0 (y = 512 side), the top under the mantle is v = 1: keep the top band plain
    const placed = []
    for (let n = 0; n < 60 && placed.length < 26; n++) {
      const x = rnd() * c.width, y = 130 + rnd() * 330, r = 16 + rnd() * 12
      if (placed.some(p => Math.hypot(p.x - x, p.y - y) < p.r + r + 26)) continue
      placed.push({ x, y, r })
      const col = petals[Math.floor(rnd() * petals.length)], cen = centres[Math.floor(rnd() * centres.length)], rot = rnd() * Math.PI
      flower(x, y, r, col, cen, rot)
      if (x < r * 1.2) flower(x + c.width, y, r, col, cen, rot)       // wrap the seam
      if (x > c.width - r * 1.2) flower(x - c.width, y, r, col, cen, rot)
    }
    for (let n = 0; n < 40; n++) {
      const x = rnd() * c.width, y = 150 + rnd() * 300
      ctx.beginPath(); ctx.arc(x, y, 3 + rnd() * 2, 0, Math.PI * 2); ctx.fillStyle = rnd() < 0.5 ? (dark ? '#75b6eb' : '#c2e7f8') : '#c9f2b7'; ctx.fill()
    }
  }
  if (look === 'stars') {
    // a star chart: small pale points, a few brighter four-point stars, faint lines between some of them
    const pts = []
    for (let n = 0; n < 70; n++) pts.push({ x: rnd() * c.width, y: 120 + rnd() * 360, r: 1.5 + rnd() * 2.2 })
    ctx.strokeStyle = 'rgba(188,229,255,0.35)'; ctx.lineWidth = 1.5
    for (let n = 0; n < 14; n++) {
      const a = pts[Math.floor(rnd() * pts.length)], b = pts[Math.floor(rnd() * pts.length)]
      if (Math.hypot(a.x - b.x, a.y - b.y) < 140) { ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke() }
    }
    for (const p of pts) { ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2); ctx.fillStyle = rnd() < 0.8 ? '#e4f5ff' : '#ffd166'; ctx.fill() }
    for (let n = 0; n < 9; n++) {
      const x = rnd() * c.width, y = 140 + rnd() * 320, r = 7 + rnd() * 6
      ctx.beginPath()
      for (let i = 0; i < 8; i++) { const a = i * Math.PI / 4, rr = i % 2 ? r * 0.3 : r; ctx.lineTo(x + Math.cos(a) * rr, y + Math.sin(a) * rr) }
      ctx.closePath(); ctx.fillStyle = '#fff3c4'; ctx.fill()
    }
  }
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace
  t.wrapS = THREE.RepeatWrapping; t.anisotropy = 4
  return t
}
const skirtTextures = {}
function skirtTex(look) { const key = themeName + ':' + look; return skirtTextures[key] || (skirtTextures[key] = skirtTexture(look)) }
const DEFAULT_LOOK = 'stars'
const skirtMat = new THREE.MeshToonMaterial({ color: 0xffffff, map: skirtTex(DEFAULT_LOOK), gradientMap: toon })
const skirt = new THREE.Mesh(skirtGeo, skirtMat)
const skirtOutlineMat = new THREE.MeshBasicMaterial({ color: INK, side: THREE.BackSide })
let skirtLook = DEFAULT_LOOK
function applySkirtLook(look) {
  if (!SKIRT_LOOKS.includes(look)) return
  skirtLook = look
  skirtMat.map = skirtTex(look); skirtMat.needsUpdate = true
  const dark = look === 'dark' || look === 'darkFloral' || look === 'stars'
  skirtOutlineMat.color.set(dark ? INK : INK)
  tips.forEach(t => t.userData.setDark && t.userData.setDark(dark))
  recolourPet()
}
const skirtOutline = new THREE.Mesh(skirtGeo, skirtOutlineMat); skirtOutline.scale.set(1.035, 1.035, 1.035); skirtOutline.position.y = -0.01
const skirtUnder = new THREE.Mesh(skirtGeo, new THREE.MeshToonMaterial({ color: CREAM, gradientMap: toon, side: THREE.BackSide }))
skirtUnder.scale.setScalar(0.985)
const skirtGroup = new THREE.Group()
skirtGroup.add(skirt, skirtOutline, skirtUnder)
head.add(skirtGroup)

// arm tips: a soft knob at the end of each arm, curling outward. The two front ones can hold things.
const tips = []
for (let i = 0; i < ARMS; i++) {
  const th = (i + 0.5) * (Math.PI * 2 / ARMS)                   // offset so two arms face the camera
  const g = new THREE.Group()
  // local +x points away from the body; the tip is a soft teardrop that curls up at the end
  const knobMat = toonMat(BLUE), curlMat = toonMat(BLUE_LIGHT)
  const knob = withOutline(new THREE.Mesh(new THREE.SphereGeometry(1, 24, 24), knobMat), 1.06)
  knob.scale.set(0.27, 0.12, 0.17); knob.position.x = 0.1
  const curl = withOutline(new THREE.Mesh(new THREE.SphereGeometry(1, 20, 20), curlMat), 1.08)
  curl.scale.set(0.11, 0.1, 0.12); curl.position.set(0.34, 0.07, 0)
  const r = 1.33
  g.position.set(Math.cos(th) * r, -1.12, Math.sin(th) * r)
  g.rotation.set(0, -th, 0.3)
  g.userData = { th, base: g.position.clone(), setDark: (d) => { knobMat.color.set(d ? BLUE_DEEP : BLUE); curlMat.color.set(d ? BLUE_LIGHT : BLUE_LIGHT) } }
  g.add(knob, curl)
  head.add(g)
  tips.push(g)
}
// the two arms nearest the camera (largest z)
const frontTips = tips.slice().sort((a, b) => b.position.z - a.position.z).slice(0, 2)
const frontL = frontTips.find(t => t.position.x < 0), frontR = frontTips.find(t => t.position.x > 0)

// eyes: big, slightly apart, with lids that can droop and a fully closed version
function makeEye(r, pupilR, derpX, derpY, lazy) {
  const g = new THREE.Group()
  const white = withOutline(new THREE.Mesh(new THREE.SphereGeometry(r, 40, 40), toonMat(0xffffff)), 1.05)
  const pupil = new THREE.Group()
  const iris = new THREE.Mesh(new THREE.SphereGeometry(pupilR * 1.25, 32, 32), new THREE.MeshBasicMaterial({ color: 0x3471a6 }))
  const dark = new THREE.Mesh(new THREE.SphereGeometry(pupilR, 32, 32), new THREE.MeshBasicMaterial({ color: EYE_INK }))
  dark.position.z = pupilR * 0.3
  const shine = new THREE.Mesh(new THREE.SphereGeometry(pupilR * 0.42, 16, 16), new THREE.MeshBasicMaterial({ color: 0xffffff }))
  shine.position.set(-pupilR * 0.4, pupilR * 0.45, pupilR * 1.0)
  const shine2 = new THREE.Mesh(new THREE.SphereGeometry(pupilR * 0.18, 12, 12), new THREE.MeshBasicMaterial({ color: 0xffffff }))
  shine2.position.set(pupilR * 0.45, -pupilR * 0.35, pupilR * 1.0)
  pupil.add(iris, dark, shine, shine2)
  pupil.position.z = r * 0.78
  // lid: a blue cap that slides down over the eye
  const lid = new THREE.Mesh(new THREE.SphereGeometry(r * 1.07, 32, 16, 0, Math.PI * 2, 0, 1.3), toonMat(BLUE))
  lid.rotation.x = 0.55
  // fully closed: a blue ball with a happy arc
  const closed = new THREE.Group()
  const ball = withOutline(new THREE.Mesh(new THREE.SphereGeometry(r * 1.03, 32, 32), toonMat(BLUE)), 1.05)
  const arc = new THREE.Mesh(new THREE.TorusGeometry(r * 0.5, 0.03, 8, 24, Math.PI), new THREE.MeshBasicMaterial({ color: INK }))
  arc.position.set(0, -r * 0.08, r * 1.0)
  closed.add(ball, arc); closed.visible = false
  g.add(white, pupil, lid, closed)
  g.userData = { r, pupil, lid, closed, arc, derpX, derpY, lazy, base: null }
  return g
}
const eyeL = makeEye(0.31, 0.16, -0.02, 0.02, 0.22), eyeR = makeEye(0.31, 0.16, 0.02, 0.02, 0.22)
eyeL.position.copy(facePoint(-0.44, 0.42, -0.04)); eyeR.position.copy(facePoint(0.44, 0.42, -0.04))
eyeL.userData.base = eyeL.position.clone(); eyeR.userData.base = eyeR.position.clone()
head.add(eyeL, eyeR)
let eyePop = 0, eyePopV = 0

// mouth: a tiny "w" smile. A dark oval takes over when the mouth opens.
const mouthPts = []
for (let i = 0; i <= 40; i++) {
  const u = i / 40 - 0.5
  const x = u * 0.5
  const y = -0.02 - Math.abs(Math.sin(u * Math.PI * 2)) * 0.03 + (u * u) * 0.12
  mouthPts.push(facePoint(x, y, 0.025))
}
const mouth = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(mouthPts), 60, 0.022, 8, false), new THREE.MeshBasicMaterial({ color: INK }))
head.add(mouth)
const mouthO = new THREE.Mesh(new THREE.SphereGeometry(1, 24, 24), new THREE.MeshBasicMaterial({ color: INK }))
mouthO.scale.set(0.1, 0.08, 0.05); mouthO.position.copy(facePoint(0, -0.03, 0)); mouthO.visible = false
head.add(mouthO)

// blush
const cheekMat = new THREE.MeshBasicMaterial({ color: BLUSH, transparent: true, opacity: 0.75 })
;[-1, 1].forEach(s => {
  const c = new THREE.Mesh(new THREE.CircleGeometry(0.1, 32), cheekMat)
  c.position.copy(facePoint(s * 0.66, 0.12, 0.012)); c.lookAt(c.position.clone().multiplyScalar(3))
  head.add(c)
})
// a few pale freckle-dots on the mantle, like a real flapjack
;[[-0.2, 0.95], [0.12, 1.0], [0.3, 0.86], [-0.42, 0.82], [0.0, 0.78]].forEach(([x, y]) => {
  const d = new THREE.Mesh(new THREE.CircleGeometry(0.035, 16), new THREE.MeshBasicMaterial({ color: BLUE_LIGHT, transparent: true, opacity: 0.7 }))
  d.position.copy(facePoint(x, y, 0.01)); d.lookAt(d.position.clone().multiplyScalar(3))
  head.add(d)
})

// Reversible wardrobe: generated geometry stays local, follows the animated head,
// and never replaces the laptop/document used to communicate research state.
const wardrobe = new THREE.Group(); head.add(wardrobe)
const {clothes, accessories} = makeClothes(toon)
const hats = {}
const cloth = new THREE.MeshToonMaterial({ color: 0xf7f6f2, gradientMap: toon })
const trim = new THREE.MeshToonMaterial({ color: 0xe1b85c, gradientMap: toon })
const beret = new THREE.Group()
const beretTop = new THREE.Mesh(new THREE.SphereGeometry(1, 32, 20), trim)
beretTop.scale.set(.69, .18, .52); beretTop.position.set(.13, 1.10, 0); beretTop.rotation.z = -.13; beret.add(beretTop)
const stem = new THREE.Mesh(new THREE.CylinderGeometry(.04, .04, .12, 12), trim); stem.position.set(.18, 1.29, 0); beret.add(stem)
hats.beret = beret
const beanie = new THREE.Group()
const cap = new THREE.Mesh(new THREE.SphereGeometry(1, 32, 24, 0, Math.PI * 2, 0, Math.PI / 2), toonMat(BLUE_DEEP))
cap.scale.set(.67, .38, .57); cap.position.y = .99; beanie.add(cap)
const pom = new THREE.Mesh(new THREE.SphereGeometry(.13, 20, 20), cloth); pom.position.y = 1.4; beanie.add(pom)
hats.beanie = beanie
Object.values({...clothes, ...accessories, ...hats}).forEach(item => { item.visible = false; wardrobe.add(item) })
function recolourPet() {
  const t = THEMES[themeName]
  const mapping = new Map([[BLUE,t.body],[BLUE_DEEP,t.deep],[BLUE_LIGHT,t.light],[CREAM,t.cream],[FIN_INNER,t.inner]])
  head.traverse(obj => {
    const mats = Array.isArray(obj.material) ? obj.material : [obj.material]
    for (const mat of mats) {
      if (!mat?.color || mat.map) continue
      if (mat.userData.squipiBase === undefined) mat.userData.squipiBase = mat.color.getHex()
      const next = mapping.get(mat.userData.squipiBase)
      if (next !== undefined) mat.color.setHex(next)
    }
  })
}

// ---------- props ----------
// Round navy glasses are part of her everyday look, including while idle.
const glasses = new THREE.Group()
{
  const frame = new THREE.MeshBasicMaterial({ color: INK })
  const r = 0.3
  const lensL = new THREE.Mesh(new THREE.TorusGeometry(r, 0.032, 10, 48), frame)
  const lensR = lensL.clone()
  const glassMat = new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.16 })
  const gL = new THREE.Mesh(new THREE.CircleGeometry(r - 0.02, 32), glassMat), gR = gL.clone()
  const pL = facePoint(-0.44, 0.42, 0.34), pR = facePoint(0.44, 0.42, 0.34)
  lensL.position.copy(pL); lensR.position.copy(pR); gL.position.copy(pL); gR.position.copy(pR)
  const bridge = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3([pL.clone().add(new THREE.Vector3(r, 0.02, 0)), new THREE.Vector3(0, pL.y + 0.1, pL.z + 0.02), pR.clone().add(new THREE.Vector3(-r, 0.02, 0))]), 16, 0.022, 8, false), frame)
  const temple = (s) => new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3([facePoint(s * 0.74, 0.44, 0.3), facePoint(s * 1.0, 0.46, 0.0), new THREE.Vector3(s * 1.05, 0.48, -0.5)]), 16, 0.02, 8, false), frame)
  glasses.add(lensL, lensR, gL, gR, bridge, temple(-1), temple(1))
  glasses.scale.setScalar(1)
  head.add(glasses)
}
let gl = 1, glV = 0

// a small matcha bowl, held in a front arm now and then while idle
const bowl = new THREE.Group()
{
  const cup = withOutline(new THREE.Mesh(new THREE.CylinderGeometry(0.21, 0.15, 0.19, 24, 1, true), new THREE.MeshToonMaterial({ color: BOWL, gradientMap: toon, side: THREE.DoubleSide })), 1.06)
  const base = new THREE.Mesh(new THREE.CircleGeometry(0.15, 24), toonMat(BOWL)); base.rotation.x = Math.PI / 2; base.position.y = -0.095
  const tea = new THREE.Mesh(new THREE.CircleGeometry(0.19, 24), new THREE.MeshBasicMaterial({ color: MATCHA })); tea.rotation.x = -Math.PI / 2; tea.position.y = 0.06
  const foam = new THREE.Mesh(new THREE.CircleGeometry(0.075, 16), new THREE.MeshBasicMaterial({ color: 0xa8cf8f })); foam.rotation.x = -Math.PI / 2; foam.position.set(0.05, 0.065, -0.04)
  bowl.add(cup, base, tea, foam)
  bowl.scale.setScalar(0.001)
  head.add(bowl)
}
let bw = 0, bwV = 0, bowlUntil = 0, sipT = 0, matchaT = 22 + Math.random() * 20

// the approval card: a little document Squipi holds up when something needs a yes
function cardTexture() {
  const c = document.createElement('canvas'); c.width = 256; c.height = 176
  const ctx = c.getContext('2d')
  const rr = (x, y, w, h, r) => { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath() }
  rr(4, 4, 248, 168, 22); ctx.fillStyle = '#ffffff'; ctx.fill(); ctx.lineWidth = 8; ctx.strokeStyle = '#172b3a'; ctx.stroke()
  ctx.fillStyle = '#0f766e'; rr(26, 28, 120, 18, 9); ctx.fill()
  ctx.fillStyle = '#dbede8'; rr(26, 60, 200, 12, 6); ctx.fill(); rr(26, 82, 170, 12, 6); ctx.fill(); rr(26, 104, 190, 12, 6); ctx.fill()
  ctx.fillStyle = '#172b3a'; ctx.font = 'bold 54px -apple-system, Helvetica, sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText('?', 196, 42)
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace
  return t
}
const card = new THREE.Mesh(new THREE.PlaneGeometry(0.78, 0.54), new THREE.MeshBasicMaterial({ map: cardTexture(), transparent: true }))
card.position.set(0, -0.35, 1.45); card.rotation.x = -0.15
card.scale.setScalar(0.001)
head.add(card)
let cd = 0, cdV = 0

// Work props are actual 3D objects: a hinged laptop and a little research paper.
// They share the character's soft outlines and never cover her eyes or glasses.
function roundedPanel(width, height, depth, color, radius = 0.08) {
  const x = -width / 2, y = -height / 2, r = Math.min(radius, width / 2, height / 2)
  const shape = new THREE.Shape()
  shape.moveTo(x + r, y); shape.lineTo(x + width - r, y)
  shape.quadraticCurveTo(x + width, y, x + width, y + r)
  shape.lineTo(x + width, y + height - r); shape.quadraticCurveTo(x + width, y + height, x + width - r, y + height)
  shape.lineTo(x + r, y + height); shape.quadraticCurveTo(x, y + height, x, y + height - r)
  shape.lineTo(x, y + r); shape.quadraticCurveTo(x, y, x + r, y)
  const geometry = new THREE.ExtrudeGeometry(shape, { depth, bevelEnabled: false, curveSegments: 8 })
  geometry.translate(0, 0, -depth / 2)
  return withOutline(new THREE.Mesh(geometry, toonMat(color)), 1.025)
}

const laptop = new THREE.Group()
const laptopLid = new THREE.Group()
const laptopLights = []
{
  const base = roundedPanel(1.72, 0.88, 0.09, 0xc7e5f7)
  base.rotation.x = -Math.PI / 2
  laptop.add(base)
  const keyboard = new THREE.Mesh(new THREE.BoxGeometry(1.42, 0.012, 0.45), toonMat(0x526674))
  keyboard.position.set(0, 0.055, -0.08); laptop.add(keyboard)
  const keyMat = toonMat(0xe9f7ff)
  for (let row = 0; row < 4; row++) {
    for (let col = 0; col < 10; col++) {
      const keycap = new THREE.Mesh(new THREE.BoxGeometry(0.105, 0.015, 0.065), keyMat)
      keycap.position.set((col - 4.5) * 0.137, 0.068, -0.24 + row * 0.1)
      laptop.add(keycap)
    }
  }
  const trackpad = new THREE.Mesh(new THREE.BoxGeometry(0.35, 0.012, 0.12), toonMat(0x8ebbdc))
  trackpad.position.set(0, 0.058, -0.35); laptop.add(trackpad)
  const lid = roundedPanel(1.72, 0.96, 0.07, BLUE_DEEP)
  lid.position.y = 0.48; laptopLid.add(lid)
  // The back of the display faces us, as it would when Squipi is using it.
  const sticker = new THREE.Mesh(new THREE.CircleGeometry(0.17, 40), toonMat(0xeaf8ff))
  sticker.position.set(0, 0.5, 0.043); laptopLid.add(sticker)
  const stickerInk = new THREE.MeshBasicMaterial({ color: BLUE_DEEP })
  ;[-0.05, 0.05].forEach(x => {
    const eye = new THREE.Mesh(new THREE.CircleGeometry(0.025, 16), stickerInk)
    eye.position.set(x, 0.52, 0.047); laptopLid.add(eye)
  })
  const smile = new THREE.Mesh(new THREE.TorusGeometry(0.065, 0.012, 8, 24, Math.PI), stickerInk)
  smile.rotation.z = Math.PI; smile.position.set(0, 0.49, 0.047); laptopLid.add(smile)
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(1.48, 0.72), new THREE.MeshBasicMaterial({ color: INK }))
  screen.position.set(0, 0.49, -0.041); screen.rotation.y = Math.PI; laptopLid.add(screen)
  for (let i = 0; i < 3; i++) {
    const light = new THREE.Mesh(new THREE.CircleGeometry(0.023, 16), new THREE.MeshBasicMaterial({ color: 0xbff4e3 }))
    light.position.set(0.51 + i * 0.09, 0.13, 0.043); laptopLid.add(light); laptopLights.push(light)
  }
  laptopLid.position.z = 0.31; laptopLid.rotation.x = -0.17
  laptop.add(laptopLid)
  laptop.position.set(0, -1.0, 1.5); laptop.rotation.y = -0.28
  laptop.scale.setScalar(0.001); laptop.visible = false
  head.add(laptop)
}
let workProp = 0

function researchPaperTexture() {
  const c = document.createElement('canvas'); c.width = 384; c.height = 512
  const ctx = c.getContext('2d')
  ctx.fillStyle = '#f7f6f2'; ctx.fillRect(0, 0, 384, 512)
  ctx.fillStyle = '#0f766e'; ctx.fillRect(36, 42, 44, 11)
  ctx.fillStyle = '#172b3a'; ctx.fillRect(36, 74, 266, 18); ctx.fillRect(36, 102, 208, 18)
  ctx.fillStyle = '#a7c7db'
  for (let row = 0; row < 5; row++) ctx.fillRect(36, 150 + row * 24, row === 4 ? 228 : 306, 8)
  ctx.fillStyle = '#e6f2f9'; ctx.fillRect(36, 292, 306, 112)
  ;[50, 72, 91, 60, 82].forEach((height, i) => {
    ctx.fillStyle = ['#58aadd', '#2b73a6', '#0f766e', '#82b8b0', '#526674'][i]
    ctx.fillRect(61 + i * 51, 391 - height, 29, height)
  })
  ctx.fillStyle = '#a7c7db'; ctx.fillRect(36, 438, 306, 8); ctx.fillRect(36, 461, 246, 8)
  const texture = new THREE.CanvasTexture(c); texture.colorSpace = THREE.SRGBColorSpace
  return texture
}
const readingPaper = new THREE.Group()
const readingHighlight = new THREE.Mesh(new THREE.PlaneGeometry(0.82, 0.052), new THREE.MeshBasicMaterial({ color: 0xffd775, transparent: true, opacity: 0.42, depthWrite: false }))
{
  const back = roundedPanel(1.04, 1.28, 0.025, 0xc9e2f2, 0.035)
  back.position.set(0.045, -0.035, -0.035); back.rotation.z = -0.06
  const page = roundedPanel(1.04, 1.28, 0.028, 0xf7f6f2, 0.035)
  const print = new THREE.Mesh(new THREE.PlaneGeometry(1.015, 1.25), new THREE.MeshBasicMaterial({ map: researchPaperTexture() }))
  print.position.z = 0.019
  readingHighlight.position.set(0, 0.16, 0.024)
  readingPaper.add(back, page, print, readingHighlight)
  readingPaper.position.set(0, -0.58, 1.55); readingPaper.rotation.set(-0.12, -0.05, -0.08)
  readingPaper.scale.setScalar(0.001); readingPaper.visible = false
  head.add(readingPaper)
}
let readProp = 0

// These curved links extend the existing front two arms, rather than adding limbs.
const holdingArms = [-1, 1].map(side => {
  const curve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(side * 0.77, -0.91, 0.87), new THREE.Vector3(side * 0.94, -0.72, 1.13),
    new THREE.Vector3(side * 0.78, -0.49, 1.39), new THREE.Vector3(side * 0.56, -0.49, 1.6),
  ])
  const arm = withOutline(new THREE.Mesh(new THREE.TubeGeometry(curve, 28, 0.095, 10, false), toonMat(BLUE)), 1.05)
  arm.visible = false; head.add(arm)
  return arm
})

// recording light: a small red dot that breathes near the right fin
const recDot = new THREE.Mesh(new THREE.SphereGeometry(0.05, 12, 12), new THREE.MeshBasicMaterial({ color: 0xff4d4d }))
recDot.position.set(1.05, 1.2, 0.2); recDot.visible = false
head.add(recDot)

applySkirtLook(DEFAULT_LOOK)

// the counterfactual twin: a translucent second Squipi. Appears on request.
const twin = new THREE.Group()
{
  const ghostMat = new THREE.MeshToonMaterial({ color: BLUE_LIGHT, gradientMap: toon, transparent: true, opacity: 0.38, depthWrite: false })
  const ghostLine = new THREE.MeshBasicMaterial({ color: INK, side: THREE.BackSide, transparent: true, opacity: 0.3, depthWrite: false })
  const m = new THREE.Mesh(new THREE.SphereGeometry(1, 48, 48), ghostMat); m.scale.set(MX, MY, MZ); m.position.y = MYOFF
  const mo = new THREE.Mesh(new THREE.SphereGeometry(1, 48, 48), ghostLine); mo.scale.set(MX * 1.03, MY * 1.03, MZ * 1.03); mo.position.y = MYOFF
  const s = new THREE.Mesh(skirtGeo, ghostMat)
  const so = new THREE.Mesh(skirtGeo, ghostLine); so.scale.setScalar(1.035)
  ;[-1, 1].forEach(side => {
    const f = new THREE.Mesh(new THREE.SphereGeometry(1, 24, 24), ghostMat); f.scale.set(0.5, 0.34, 0.13)
    f.position.set(side * 1.3, 0.75 + MYOFF, -0.12); f.rotation.z = side * 0.55; f.rotation.y = side * 0.35
    twin.add(f)
  })
  ;[-0.44, 0.44].forEach(x => {
    const e = new THREE.Mesh(new THREE.SphereGeometry(0.31, 24, 24), ghostMat); e.position.copy(facePoint(x, 0.42, -0.04)); twin.add(e)
    const p = new THREE.Mesh(new THREE.SphereGeometry(0.14, 16, 16), new THREE.MeshBasicMaterial({ color: EYE_INK, transparent: true, opacity: 0.35, depthWrite: false })); p.position.copy(facePoint(x, 0.42, 0.22)); twin.add(p)
  })
  twin.add(m, mo, s, so)
  twin.position.set(1.42, 0, -0.55); twin.scale.setScalar(0.001)
  scene.add(twin)
}
let tw = 0, twV = 0, twinOn = false

// ---------- effects ----------
function radialTexture(inner, outer) {
  const c = document.createElement('canvas'); c.width = c.height = 128
  const ctx = c.getContext('2d')
  const g = ctx.createRadialGradient(64, 64, 4, 64, 64, 64)
  g.addColorStop(0, inner); g.addColorStop(1, outer)
  ctx.fillStyle = g; ctx.fillRect(0, 0, 128, 128)
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace
  return t
}
const shadow = new THREE.Mesh(new THREE.PlaneGeometry(2.6, 2.6), new THREE.MeshBasicMaterial({ map: radialTexture('rgba(40,30,70,0.32)', 'rgba(40,30,70,0)'), transparent: true, depthWrite: false }))
shadow.rotation.x = -Math.PI / 2; shadow.position.y = -1.28; shadow.scale.set(1.9, 0.7, 1)
scene.add(shadow)

const halo = new THREE.Mesh(new THREE.PlaneGeometry(4.8, 4.8), new THREE.MeshBasicMaterial({ map: radialTexture('rgba(205,185,255,0.85)', 'rgba(205,185,255,0)'), transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }))
halo.position.z = -1.3
scene.add(halo)

// a soft pulse ring while waiting for approval
const ring = new THREE.Mesh(new THREE.RingGeometry(1.5, 1.58, 64), new THREE.MeshBasicMaterial({ color: 0x82b8b0, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false }))
ring.position.z = -0.6
scene.add(ring)

// particles: sparkles (success) and ink (error)
function makeParticles(n, color, size, tex) {
  const geo = new THREE.BufferGeometry()
  const pos = new Float32Array(n * 3)
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3))
  const mat = new THREE.PointsMaterial({ color, size, transparent: true, opacity: 0, map: tex, depthWrite: false })
  const pts = new THREE.Points(geo, mat)
  scene.add(pts)
  return { pts, pos, vel: new Float32Array(n * 3), life: 0, n }
}
const softTex = radialTexture('rgba(255,255,255,1)', 'rgba(255,255,255,0)')
const sparks = makeParticles(44, 0xfff0b8, 0.26, softTex); sparks.pts.material.blending = THREE.AdditiveBlending
const ink = makeParticles(36, INK, 0.7, softTex)
function burst(p, { origin = [0, 0.2, 0.6], speed = [1.6, 2.6], lift = 1.4, spread = 1 }) {
  for (let i = 0; i < p.n; i++) {
    const a = Math.random() * Math.PI * 2, b = (Math.random() - 0.5) * Math.PI * spread
    const sp = speed[0] + Math.random() * (speed[1] - speed[0])
    p.pos[i * 3] = origin[0]; p.pos[i * 3 + 1] = origin[1]; p.pos[i * 3 + 2] = origin[2]
    p.vel[i * 3] = Math.cos(a) * Math.cos(b) * sp
    p.vel[i * 3 + 1] = Math.sin(b) * sp + lift
    p.vel[i * 3 + 2] = Math.sin(a) * Math.cos(b) * sp * 0.5
  }
  p.life = 1
}
function stepParticles(p, dt, gravity, fade) {
  if (p.life <= 0) { p.pts.material.opacity = 0; return }
  p.life -= dt * fade
  for (let i = 0; i < p.n; i++) {
    p.vel[i * 3 + 1] -= gravity * dt
    p.pos[i * 3] += p.vel[i * 3] * dt; p.pos[i * 3 + 1] += p.vel[i * 3 + 1] * dt; p.pos[i * 3 + 2] += p.vel[i * 3 + 2] * dt
  }
  p.pts.geometry.attributes.position.needsUpdate = true
  p.pts.material.opacity = Math.max(0, p.life)
}

// sprites: z's, a question mark, thinking dots, a tiny chart
function textSprite(txt, size = 0.45, color = '#ffffff') {
  const c = document.createElement('canvas'); c.width = 96; c.height = 96
  const ctx = c.getContext('2d')
  ctx.font = 'bold 58px -apple-system, Helvetica, sans-serif'
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
  ctx.lineWidth = 8; ctx.strokeStyle = '#172b3a'; ctx.strokeText(txt, 48, 52)
  ctx.fillStyle = color; ctx.fillText(txt, 48, 52)
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, transparent: true, opacity: 0, depthWrite: false }))
  s.scale.setScalar(size)
  return s
}
const zs = [textSprite('z'), textSprite('z'), textSprite('z')]
zs.forEach(z => scene.add(z))
const qmark = textSprite('?', 0.7); scene.add(qmark)
const dots = [textSprite('·', 0.5, '#dbede8'), textSprite('·', 0.5, '#dbede8'), textSprite('·', 0.5, '#dbede8')]
dots.forEach(d => scene.add(d))
function chartSprite() {
  const c = document.createElement('canvas'); c.width = 96; c.height = 96
  const ctx = c.getContext('2d')
  const rr = (x, y, w, h) => { ctx.fillRect(x, y, w, h) }
  ctx.fillStyle = '#ffffff'; ctx.strokeStyle = '#172b3a'; ctx.lineWidth = 6
  ctx.beginPath(); ctx.roundRect(8, 8, 80, 80, 16); ctx.fill(); ctx.stroke()
  ctx.fillStyle = '#0f766e'; rr(22, 50, 12, 24); rr(42, 34, 12, 40); rr(62, 42, 12, 32)
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, transparent: true, opacity: 0, depthWrite: false }))
  s.scale.setScalar(0.5)
  return s
}
const chart = chartSprite(); scene.add(chart)

// ---------- sounds ----------
// Soft, watery and short. Nothing plays unless sounds are on.
const sfx = {
  enabled: true, ctx: null,
  ac() { if (!this.ctx) this.ctx = new (window.AudioContext || window.webkitAudioContext)(); if (this.ctx.state === 'suspended') this.ctx.resume(); return this.ctx },
  tone({ type = 'sine', from = 440, to = from, dur = 0.12, vol = 0.22, delay = 0, attack = 0.006, curve = 'exp' }) {
    const c = this.ac(), t0 = c.currentTime + delay
    const o = c.createOscillator(), g = c.createGain()
    o.type = type
    o.frequency.setValueAtTime(from, t0)
    if (curve === 'exp') o.frequency.exponentialRampToValueAtTime(Math.max(20, to), t0 + dur)
    else o.frequency.linearRampToValueAtTime(to, t0 + dur)
    g.gain.setValueAtTime(0.0001, t0)
    g.gain.exponentialRampToValueAtTime(vol, t0 + attack)
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur)
    o.connect(g).connect(c.destination)
    o.start(t0); o.stop(t0 + dur + 0.02)
  },
  noise({ dur = 0.08, vol = 0.12, delay = 0, freq = 1400, q = 1.0 }) {
    const c = this.ac(), t0 = c.currentTime + delay
    const n = Math.floor(c.sampleRate * dur), buf = c.createBuffer(1, n, c.sampleRate), d = buf.getChannelData(0)
    for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / n)
    const src = c.createBufferSource(); src.buffer = buf
    const f = c.createBiquadFilter(); f.type = 'bandpass'; f.frequency.value = freq; f.Q.value = q
    const g = c.createGain(); g.gain.setValueAtTime(vol, t0); g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur)
    src.connect(f).connect(g).connect(c.destination); src.start(t0)
  },
  play(name) {
    if (!this.enabled) return
    try {
      switch (name) {
        case 'blub':    this.tone({ from: 320, to: 140, dur: 0.14, vol: 0.2 }); this.noise({ dur: 0.04, vol: 0.05, freq: 900 }); break
        case 'bubble':  this.tone({ from: 500, to: 760, dur: 0.09, vol: 0.14 }); this.tone({ from: 760, to: 640, dur: 0.08, vol: 0.1, delay: 0.09 }); break
        case 'gulp':    this.tone({ from: 240, to: 100, dur: 0.15, vol: 0.18 }); break
        case 'ding':    this.tone({ type: 'triangle', from: 988, dur: 0.2, vol: 0.14 }); this.tone({ type: 'triangle', from: 1319, dur: 0.3, vol: 0.14, delay: 0.1 }); this.tone({ type: 'triangle', from: 1976, dur: 0.22, vol: 0.05, delay: 0.19 }); break
        case 'huh':     this.tone({ type: 'triangle', from: 400, to: 300, dur: 0.15, vol: 0.14, curve: 'lin' }); this.tone({ type: 'triangle', from: 300, to: 430, dur: 0.17, vol: 0.12, delay: 0.16, curve: 'lin' }); break
        case 'inkpuff': this.noise({ dur: 0.22, vol: 0.14, freq: 500, q: 0.6 }); this.tone({ from: 200, to: 60, dur: 0.25, vol: 0.1 }); break
        case 'ask':     this.tone({ type: 'triangle', from: 660, dur: 0.1, vol: 0.1 }); this.tone({ type: 'triangle', from: 880, dur: 0.16, vol: 0.1, delay: 0.12 }); break
        case 'tick':    this.tone({ type: 'square', from: 1400, dur: 0.03, vol: 0.04 }); break
        case 'boop':    this.tone({ from: 560, to: 740, dur: 0.07, vol: 0.16 }); this.tone({ from: 740, to: 400, dur: 0.1, vol: 0.12, delay: 0.07 }); break
        case 'sip':     this.noise({ dur: 0.12, vol: 0.05, freq: 2600, q: 1.5 }); this.tone({ type: 'sine', from: 180, to: 220, dur: 0.1, vol: 0.06, delay: 0.1 }); break
        case 'recOn':   this.tone({ type: 'sine', from: 880, dur: 0.06, vol: 0.08 }); this.tone({ type: 'sine', from: 1175, dur: 0.1, vol: 0.08, delay: 0.08 }); break
        case 'recOff':  this.tone({ type: 'sine', from: 1175, dur: 0.06, vol: 0.08 }); this.tone({ type: 'sine', from: 880, dur: 0.1, vol: 0.08, delay: 0.08 }); break
      }
    } catch (e) { /* audio not available */ }
  },
}

// ---------- state ----------
// States mirror real app events: idle, listening, thinking, working, reading, speaking (via setTalking), analysing,
// approving (awaiting approval), recording, success, error, sleeping, loading.
// Ribbit's names are accepted as aliases so the inherited Swift paths keep working.
const ALIAS = { done: 'success', noting: 'recording', confused: 'error' }
let state = 'idle'
let stateT = 0
let level = 0, levelSmooth = 0
const look = { x: 0, y: 0, tx: 0, ty: 0 }
let sq = 0, sqV = 0
let blinkT = 2.5, blink = 0
let hopT = 7 + Math.random() * 6
let talking = false
let reduced = false
const slide = { x: 0, y: 0, tx: 0, ty: 0, speed: 3 }
const cam = { tz: camera.position.z, speed: 0 }
let calm = 0
const vel = { x: 0, y: 0 }
let propGlasses = null   // null = everyday glasses; true/false explicitly overrides
let flowerOn = false

function impulse(v) { if (reduced) v *= 0.25; sqV += v * (1 - 0.5 * calm) }

window.pet = {
  name: 'squipi',
  setPlayPose(value) { playPose = ['chasing','holding','windup','throwing'].includes(value) ? value : 'idle' },
  setWardrobe(options = {}) {
    if (Object.hasOwn(THEMES, options.theme)) themeName = options.theme
    if ('outfit' in options) Object.entries(clothes).forEach(([name, item]) => { item.visible = name === options.outfit })
    if ('accessory' in options) Object.entries(accessories).forEach(([name, item]) => { item.visible = name === options.accessory })
    if ('hat' in options) Object.entries(hats).forEach(([name, item]) => { item.visible = name === options.hat })
    if ('glasses' in options) propGlasses = !!options.glasses
    applySkirtLook(skirtLook)
    recolourPet()
  },
  setState(raw) {
    const s = ALIAS[raw] || raw
    if (s === state) return
    const prev = state
    state = s; stateT = 0
    if (s === 'listening') { impulse(-3.5); sfx.play('blub') }
    if (s === 'thinking' && prev === 'listening') sfx.play('gulp')
    if (s === 'analysing') { impulse(-2); sfx.play('bubble') }
    if (s === 'approving') { impulse(-2); sfx.play('ask') }
    if (s === 'success') { impulse(8); if (!reduced) burst(sparks, { origin: [0, 0.3, 0.6], speed: [1.6, 2.8], lift: 1.6 }); sfx.play('ding') }
    if (s === 'error') { impulse(3); if (!reduced) burst(ink, { origin: [0, -0.9, 0.2], speed: [0.6, 1.4], lift: 0.4, spread: 0.6 }); sfx.play('inkpuff'); sfx.play('huh') }
    if (s === 'recording') { impulse(-2); sfx.play('recOn') }
    if (prev === 'recording' && s !== 'recording') sfx.play('recOff')
    if (s === 'idle') { impulse(1.5); if (prev === 'loading' || prev === 'sleeping') sfx.play('bubble') }
    if (s === 'sleeping' || s === 'loading') bowlUntil = 0
  },
  setLevel(v) { level = Math.max(0, Math.min(1, v)) },
  lookAt(x, y) { look.tx = x; look.ty = y },
  setSounds(on) { sfx.enabled = !!on },
  setQuiet(on) { sfx.enabled = !on },
  setReducedMotion(on) { reduced = !!on },
  setTalking(on) { talking = !!on },
  setVelocity(x, y) { vel.x = +x || 0; vel.y = +y || 0 },
  setCalm(c) { calm = Math.max(0, Math.min(1, +c || 0)) },
  setOffset(x, y, secs) { slide.tx = x; slide.ty = y; slide.speed = secs ? 1 / Math.max(0.05, secs) : 3 },
  /** look: { skirt: 'plain'|'floral'|'dark'|'darkFloral'|'stars', flower: true|false } */
  setLook(l = {}) {
    if (l.skirt) applySkirtLook(l.skirt)
    if ('flower' in l) flowerOn = !!l.flower
  },
  /** props: { glasses: true|false|null, matcha: true|false, twin: true|false } */
  setProps(p = {}) {
    if ('glasses' in p) propGlasses = p.glasses
    if ('matcha' in p) bowlUntil = p.matcha ? 1e9 : 0
    if ('twin' in p) { twinOn = !!p.twin; slide.tx = twinOn ? -0.7 : 0; slide.speed = 2 }
  },
  ui(html) { let u = document.getElementById('ui'); if (!u) { u = document.createElement('div'); u.id = 'ui'; document.body.appendChild(u) } u.innerHTML = html || '' },
  setCaption(text) {
    let c = document.getElementById('cap')
    if (!c) {
      c = document.createElement('div'); c.id = 'cap'
      c.setAttribute('role', 'status'); c.setAttribute('aria-live', 'polite'); c.setAttribute('aria-atomic', 'true')
      document.body.appendChild(c)
    }
    c.textContent = text || ''; c.title = text || ''; c.style.opacity = text ? 1 : 0
    c.setAttribute('aria-hidden', text ? 'false' : 'true')
  },
  setZoom(z) { camera.position.z = z; cam.tz = z; cam.speed = 0 },
  zoomTo(z, secs) { cam.tz = z; cam.speed = 1 / Math.max(0.05, secs) },
  boop() { impulse(5); sfx.play('boop') },
  state: () => state,
}

// ---------- loop ----------
const clock = new THREE.Clock()
const tmpV = new THREE.Vector3()
function frame() {
  requestAnimationFrame(frame)
  const dt = Math.min(clock.getDelta(), 1 / 20)
  const t = clock.elapsedTime
  stateT += dt
  const motion = reduced ? 0.3 : 1

  // squash spring
  const k = 150 - 60 * calm, d = 12 + 12 * calm
  sqV += (-k * sq - d * sqV) * dt
  sq += sqV * dt

  const lk = 6 - 4 * calm
  look.x += (look.tx - look.x) * Math.min(1, dt * lk)
  look.y += (look.ty - look.y) * Math.min(1, dt * lk)
  levelSmooth += (level - levelSmooth) * Math.min(1, dt * 14)

  let hover = Math.sin(t * (1.3 - 1.0 * calm)) * (0.035 - 0.025 * calm) * motion
  let breathe = 1 + Math.sin(t * (1.8 - 1.5 * calm)) * (0.012 + 0.02 * calm)
  let rotX = -look.y * 0.16, rotY = look.x * 0.3, rotZ = 0
  let eyeOpen = 1
  let mouthOpen = 0
  let haloOpacity = 0, ringOpacity = 0
  let zOpacity = 0, qOpacity = 0, dotOpacity = 0, chartOpacity = 0
  let showSmile = true
  let finFlap = Math.sin(t * 1.6) * 0.08 * motion          // slow idle flutter
  let finSpread = 0                                          // 0 relaxed .. 1 wide
  let finDroop = 0
  let glassesTarget = 1, cardTarget = 0, laptopTarget = 0, paperTarget = 0
  let skirtWave = 0.02 * motion
  let pupilRoll = 0, scan = 0
  let happy = false
  let bodyTilt = 0

  // blink
  blinkT -= dt
  if (blinkT <= 0) { blink = 0.14; blinkT = 2.5 + Math.random() * 4 }
  if (blink > 0) { blink -= dt; eyeOpen = blink > 0.07 ? 0 : 1 }

  // the occasional idle hop: small, and never while reduced motion is on
  hopT -= dt
  if (hopT <= 0 && state === 'idle' && !reduced) { impulse(-5 + 2 * calm); hopT = calm ? 12 + Math.random() * 6 : 8 + Math.random() * 9 }

  // matcha break: now and then while idle, Squipi lifts the bowl and takes a sip
  if (state === 'idle' && bowlUntil < 1e8) {
    matchaT -= dt
    if (matchaT <= 0) { bowlUntil = 7.5; sipT = 2.2; matchaT = 35 + Math.random() * 40 }
  }
  bowlUntil -= dt
  const usingResearchProp = ['thinking', 'analysing', 'working', 'reading'].includes(state)
  const bowlOn = !usingResearchProp && bowlUntil > 0 && (state === 'idle' || bowlUntil > 1e8)
  if (bowlOn && bowlUntil < 1e8) {
    sipT -= dt
    if (sipT < 0) { sipT = 2.6 + Math.random() * 1.5; sfx.play('sip') }
    if (sipT > 2.0 && sipT < 2.45) { eyeOpen = 0; happy = true; rotX += 0.08 }   // eyes closed during the sip
  }

  if (talking && (state === 'idle' || state === 'success' || state === 'approving')) {
    const m = 0.5 + 0.5 * (0.6 * Math.sin(t * (6.2 - 1.6 * calm)) + 0.4 * Math.sin(t * (3.9 - 1.1 * calm) + 1.0))
    mouthOpen = 0.1 + m * 0.5
    showSmile = false
    finFlap += Math.sin(t * 7) * 0.05 * motion
  }

  switch (state) {
    case 'listening': {
      mouthOpen = 0.15 + levelSmooth * 0.5
      showSmile = false
      rotX += 0.08
      finSpread = 0.7 + levelSmooth * 0.3
      finFlap += Math.sin(t * (9 - 5 * calm)) * (0.06 + levelSmooth * 0.22) * motion
      hover += Math.sin(t * 6) * 0.015 * levelSmooth * motion
      haloOpacity = 0.14 + levelSmooth * 0.5
      halo.scale.setScalar(1 + levelSmooth * 0.25 + Math.sin(t * 5) * 0.02)
      break
    }
    case 'thinking': {
      laptopTarget = 1
      rotZ = Math.sin(t * 2.4) * 0.035 * motion
      rotY += 0.08 + Math.sin(t * 1.2) * 0.05 * motion
      rotX += 0.09
      scan = 0.55
      dotOpacity = Math.min(1, stateT * 3)
      finSpread = 0.2; finFlap += Math.sin(t * 2.2) * 0.1 * motion
      skirtWave = 0.05 * motion
      break
    }
    case 'working':
    case 'analysing': {
      laptopTarget = 1
      rotX += 0.1
      scan = 1
      chartOpacity = Math.min(1, stateT * 3)
      finSpread = 0.15; finFlap += Math.sin(t * 3) * 0.05 * motion
      skirtWave = 0.08 * motion
      hover += Math.sin(t * 3) * 0.01 * motion
      break
    }
    case 'reading': {
      paperTarget = 1
      rotX += 0.1
      rotY += Math.sin(t * 1.6) * 0.035 * motion
      rotZ = -0.035 + Math.sin(t * 1.1) * 0.025 * motion
      scan = 1
      finSpread = 0.12; finFlap = Math.sin(t * 1.8) * 0.045 * motion
      skirtWave = 0.025 * motion
      break
    }
    case 'approving': {
      cardTarget = 1
      ringOpacity = 0.28 + Math.sin(t * 3) * 0.14
      ring.scale.setScalar(1 + Math.sin(t * 3) * 0.04)
      rotX += 0.04; rotZ = Math.sin(t * 1.4) * 0.04 * motion
      finSpread = 0.4; finFlap += Math.sin(t * 2) * 0.08 * motion
      eyeOpen = Math.min(eyeOpen, 1); // wide, hopeful
      break
    }
    case 'recording': {
      recDot.visible = true
      recDot.scale.setScalar(0.9 + Math.max(0, Math.sin(t * 4)) * 0.5)
      rotX += 0.05 + Math.sin(t * 2.0) * 0.04
      rotY += Math.sin(t * 0.7) * 0.1
      finSpread = 0.3; finFlap += Math.sin(t * 2.5) * 0.04 * motion
      break
    }
    case 'success': {
      happy = true
      finSpread = 0.9; finFlap += Math.sin(t * 10) * 0.12 * motion
      hover += Math.abs(Math.sin(stateT * 6)) * 0.05 * motion
      if (stateT > 1.25) window.pet.setState('idle')
      break
    }
    case 'error': {
      rotZ = 0.18 + Math.sin(t * 9) * 0.03 * motion
      qOpacity = Math.min(1, stateT * 4)
      showSmile = false; mouthOpen = 0.1
      finDroop = 0.8
      if (stateT > 2.0) window.pet.setState('idle')
      break
    }
    case 'loading':
    case 'sleeping': {
      eyeOpen = 0
      breathe = 1 + Math.sin(t * 1.3) * 0.03
      rotZ = Math.sin(t * 1.3) * 0.04 * motion
      rotX = 0.12; rotY *= 0.3
      zOpacity = 1
      finDroop = 1
      finFlap = Math.sin(t * 1.3) * 0.03 * motion
      break
    }
    default: break
  }
  if (state !== 'recording') recDot.visible = false
  if (propGlasses === true) glassesTarget = 1
  if (propGlasses === false) glassesTarget = 0

  // eyes bulge forward while listening
  const popTarget = state === 'listening' ? 1 : (state === 'thinking' ? 0.25 : 0)
  eyePopV += ((popTarget - eyePop) * 110 - eyePopV * 7) * dt
  eyePop += eyePopV * dt

  hairFlower.visible = flowerOn
  // props spring in and out
  glV += ((glassesTarget - gl) * 130 - glV * 9) * dt; gl += glV * dt
  glasses.scale.setScalar(Math.max(0.001, gl))
  cdV += ((cardTarget - cd) * 130 - cdV * 9) * dt; cd += cdV * dt
  card.scale.setScalar(Math.max(0.001, cd))
  card.position.y = -0.3 + Math.sin(t * 2.2) * 0.03 * motion + (1 - cd) * -0.3
  workProp += (laptopTarget - workProp) * Math.min(1, dt * 12)
  readProp += (paperTarget - readProp) * Math.min(1, dt * 12)
  laptop.visible = workProp > 0.01
  laptop.scale.setScalar(Math.max(0.001, workProp))
  laptop.position.y = -1.0 + Math.sin(t * 5.2) * 0.009 * motion
  laptop.rotation.z = Math.sin(t * 3.4) * 0.012 * motion
  laptopLid.rotation.x = -0.17 + Math.sin(t * 2.1) * 0.018 * motion
  laptopLights.forEach((light, i) => { light.material.color.set((Math.sin(t * 4 - i * 1.2) > 0) ? 0xc8ffea : 0x80b8d6) })
  readingPaper.visible = readProp > 0.01
  readingPaper.scale.setScalar(Math.max(0.001, readProp))
  readingPaper.position.y = -0.58 + Math.sin(t * 1.8) * 0.02 * motion
  readingPaper.rotation.z = -0.08 + Math.sin(t * 1.5) * 0.025 * motion
  const linePhase = reduced ? 0.35 : (t * 0.18) % 1
  readingHighlight.position.y = 0.23 - Math.floor(linePhase * 5) * 0.06
  holdingArms.forEach((arm, i) => {
    const amount = Math.min(1, workProp + readProp)
    arm.visible = amount > 0.01
    arm.scale.set(amount, Math.max(0.001, readProp + workProp * 0.35), Math.max(0.001, readProp + workProp * 0.84))
    arm.position.y = workProp * -0.58 + Math.sin(t * 8 + i * Math.PI) * 0.018 * workProp * motion
  })
  const bwTarget = bowlOn ? 1 : 0
  bwV += ((bwTarget - bw) * 120 - bwV * 9) * dt; bw += bwV * dt
  bowl.scale.setScalar(Math.max(0.001, bw))
  const sip = bowlOn && sipT > 2.0 && sipT < 2.45 ? 1 : 0
  bowl.position.set(0.72 - sip * 0.5, -0.5 + sip * 0.42, 1.15 + sip * 0.05)
  bowl.rotation.z = sip * 0.35
  const twTarget = twinOn ? 1 : 0
  twV += ((twTarget - tw) * 90 - twV * 9) * dt; tw += twV * dt
  twin.scale.setScalar(Math.max(0.001, tw) * 0.8)
  twin.position.y = Math.sin(t * 1.1 + 1) * 0.03 * motion - 0.02
  twin.rotation.y = -0.25

  // apply body transforms
  const s = breathe
  pet.scale.set(s * (1 + sq * 0.5), s * (1 - sq), s * (1 + sq * 0.5))
  if (cam.speed > 0) camera.position.z += (cam.tz - camera.position.z) * Math.min(1, dt * cam.speed * 2.5)
  slide.x += (slide.tx - slide.x) * Math.min(1, dt * slide.speed * 2.2)
  slide.y += (slide.ty - slide.y) * Math.min(1, dt * slide.speed * 2.2)
  pet.position.x = slide.x
  pet.position.y = hover - Math.max(0, sq) * 0.15 + slide.y
  shadow.position.x = slide.x; shadow.position.y = -1.28 + slide.y
  // lean into the direction of travel when wandering
  bodyTilt = -vel.x * 0.12 * motion
  head.rotation.set(rotX, rotY, rotZ + bodyTilt)

  // fins
  ;[finL, finR].forEach(f => {
    const { side, base } = f.userData
    const spread = finSpread * 0.45
    const droop = finDroop * 0.6
    f.rotation.z = side * (base.z + spread - droop) + finFlap * side
    f.rotation.y = side * (base.y - spread * 0.4)
    f.rotation.x = base.x - finDroop * 0.2 + levelSmooth * 0.1
  })

  // skirt: a slow travelling ripple around the hem, stronger when busy
  skirtGroup.rotation.y = Math.sin(t * 0.9) * 0.02 * motion
  skirtGroup.scale.set(1 + Math.sin(t * 2.1) * skirtWave, 1 - Math.sin(t * 2.1) * skirtWave * 0.6, 1 + Math.cos(t * 2.1) * skirtWave)
  tips.forEach((g, i) => {
    const { th, base } = g.userData
    const wave = Math.sin(t * 2.4 - th * 1.5) * (0.02 + skirtWave * 1.5)
    g.position.set(base.x, base.y + wave, base.z)
    g.rotation.x = Math.sin(t * 1.7 + i) * 0.15 * motion
  })
  // the front arms rise to hold the card
  if (frontL && frontR) {
    const lift = cd * 0.55
    frontL.position.y += lift; frontL.position.x -= cd * 0.45; frontL.position.z += cd * 0.1
    frontR.position.y += lift; frontR.position.x += cd * 0.45; frontR.position.z += cd * 0.1
    // and the right one cradles the bowl
    if (bw > 0.05) { frontR.position.y += bw * (0.5 + sip * 0.35); frontR.position.x -= bw * (0.35 + sip * 0.3); frontR.position.z += bw * 0.15 }
    // Alternate the existing two front arms on the keys; hold the page at its sides.
    ;[frontL, frontR].forEach((arm, i) => {
      const side = i === 0 ? -1 : 1
      const typing = Math.sin(t * 9 + i * Math.PI) * 0.045 * motion
      const amount = Math.min(1, workProp + readProp)
      const target = new THREE.Vector3(side * (0.56 * readProp + 0.58 * workProp),
        (-0.49 + Math.sin(t * 1.8) * 0.02 * motion) * readProp + (-0.86 + typing) * workProp,
        1.60 * readProp + 1.34 * workProp)
      if (amount > 0.001) {
        target.divideScalar(amount)
        arm.position.lerp(target, amount)
        arm.rotation.x *= 1 - amount
      }
    })
  }

  if (frontL && frontR && ['holding','windup','throwing'].includes(playPose)) {
    for (const [i, arm] of [frontL,frontR].entries()) {
      arm.position.set((i===0?-1:1)*.28, playPose==='throwing'?-.15:-.45, playPose==='windup'?1.35:1.6)
    }
  }

  // eyes
  const closed = eyeOpen < 0.15
  ;[eyeL, eyeR].forEach((e, i) => {
    const { r, pupil, lid, closed: cl, arc, derpX, derpY, base, lazy } = e.userData
    e.position.set(base.x, base.y + eyePop * 0.07, base.z + eyePop * 0.14)
    e.scale.setScalar(1 + eyePop * 0.12)
    cl.visible = closed
    e.children[0].visible = !closed; pupil.visible = !closed; lid.visible = !closed
    arc.rotation.z = happy || sip ? 0 : Math.PI          // closed-eye arc: ∪ sleepy, ∩ happy
    let px = look.x * r * 0.42 + derpX, py = look.y * r * 0.38 + derpY
    if (pupilRoll) { px += Math.cos(t * 1.6) * r * 0.22 + r * 0.15; py += Math.sin(t * 1.6) * r * 0.12 + r * 0.22 }
    if (scan) {
      px += (reduced ? 0 : Math.sin(t * (state === 'reading' ? 2.5 : 1.6))) * r * 0.3 * scan
      py -= r * (state === 'reading' ? 0.34 + linePhase * 0.12 : 0.30) * scan
    }
    pupil.position.x = closed ? 0 : px
    pupil.position.y = closed ? 0 : py
    let droop = state === 'error' ? (i === 1 ? 0.75 : 0.25) : (state === 'listening' || state === 'success' || state === 'approving' ? 0 : lazy)
    if (state === 'analysing' || state === 'working') droop = 0.3
    if (state === 'reading') droop = 0.18
    if (happy) droop = 0.35
    lid.visible = !closed && droop > 0.04
    lid.position.y = r * (0.7 - droop * 0.85)
  })
  mouth.visible = showSmile
  mouthO.visible = !showSmile
  mouthO.scale.set(0.09 + mouthOpen * 0.08, 0.06 + mouthOpen * 0.16, 0.05)

  halo.material.opacity += (haloOpacity - halo.material.opacity) * Math.min(1, dt * 10)
  halo.rotation.z = t * 0.25
  ring.material.opacity += (ringOpacity - ring.material.opacity) * Math.min(1, dt * 8)
  shadow.material.opacity = 1 - Math.max(0, hover) * 3
  shadow.scale.set(1.9 - hover * 1.5, 0.7 - hover * 0.6, 1)

  stepParticles(sparks, dt, 6, 1.1)
  stepParticles(ink, dt, -0.8, 0.7)   // ink drifts up and spreads

  zs.forEach((z, i) => {
    const phz = (t * 0.45 + i / 3) % 1
    z.position.set(slide.x + 1.1 + phz * 0.5 + i * 0.05, 0.9 + phz * 1.1, 0.5)
    z.scale.setScalar(0.3 + phz * 0.35)
    z.material.opacity += ((zOpacity * (1 - phz) * Math.min(1, phz * 4)) - z.material.opacity) * Math.min(1, dt * 8)
  })
  qmark.position.set(slide.x + 1.2 + Math.sin(t * 5) * 0.04, 1.3, 0.5)
  qmark.material.opacity += (qOpacity - qmark.material.opacity) * Math.min(1, dt * 8)
  dots.forEach((d, i) => {
    const ph = ((t * 1.4 - i * 0.33) % 1 + 1) % 1
    d.position.set(slide.x + 1.0 + i * 0.26, 1.35 + Math.max(0, Math.sin(ph * Math.PI)) * 0.12, 0.6)
    d.material.opacity += (dotOpacity * (0.45 + 0.55 * Math.max(0, Math.sin(ph * Math.PI))) - d.material.opacity) * Math.min(1, dt * 10)
  })
  chart.position.set(slide.x + 1.3, 1.25 + Math.sin(t * 2) * 0.04, 0.5)
  chart.material.opacity += (chartOpacity - chart.material.opacity) * Math.min(1, dt * 8)

  renderer.render(scene, camera)
}
frame()

// ---------- browser-only demo (?demo=1) ----------
if (params.get('demo')) {
  const states = ['idle', 'listening', 'thinking', 'working', 'reading', 'analysing', 'approving', 'success', 'recording', 'error', 'sleeping']
  let i = 0
  setInterval(() => { i = (i + 1) % states.length; window.pet.setState(states[i]) }, 2800)
  setInterval(() => { if (state === 'listening') window.pet.setLevel(Math.abs(Math.sin(performance.now() / 180)) * 0.9) }, 33)
  window.addEventListener('mousemove', e => window.pet.lookAt((e.clientX / W - 0.5) * 2, -(e.clientY / H - 0.5) * 2))
}
const forced = params.get('state')
if (forced) { window.pet.setState(forced); if (forced === 'listening') window.pet.setLevel(0.7) }
const props = params.get('props')
if (props) { const p = {}; props.split(',').forEach(k => { p[k] = true }); window.pet.setProps(p) }
if (params.get('talking')) window.pet.setTalking(true)
if (params.get('look') || params.get('flower')) window.pet.setLook({ skirt: params.get('look') || undefined, ...(params.has('flower') ? { flower: params.get('flower') !== '0' } : {}) })
if (params.get('reduced')) window.pet.setReducedMotion(true)

if (params.has('theme') || params.has('outfit') || params.has('hat')) window.pet.setWardrobe({theme: params.get('theme') || 'blue', outfit: params.get('outfit') || 'classic', hat: params.get('hat') || 'none', accessory: params.get('accessory') || 'none'})

if (params.has('glasses')) window.pet.setWardrobe({glasses:params.get('glasses') !== '0'})
