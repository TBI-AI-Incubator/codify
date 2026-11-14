// V2, page 1 of the worked example's scan beside the Akoma Ntoso it became (bundle pages/page-001.png, final.akn.xml).
// Writes v2-page-to-akn.svg (wide) and v2-page-to-akn.narrow.svg. The crop is embedded so the inlined SVG needs no relative URL.
import { readFileSync } from 'node:fs'
import { figuresDir, line, text, svg } from './kit.mjs'

// v2-page-001-crop.jpg: page-001.png (1654x2339) cropped at x 120, y 140, 1420x1170, scaled to 640 wide.
const CROP = { x: 120, y: 140, w: 1420, h: 1170 }
const href = `data:image/jpeg;base64,${readFileSync(`${figuresDir}v2-page-001-crop.jpg`).toString('base64')}`

// Rows on page-001.png, in source pixels, and the XML line each marker matches.
const targets = [
  { n: 1, y: 283 }, // THE ATLANTIS GAZETTE
  { n: 2, y: 464 }, // No. 7 of 1992
  { n: 3, y: 913 }, // WHEREAS ... AS FOLLOWS:
  { n: 4, y: 1152 }, // PART I - PRELIMINARY
  { n: 5, y: 1218 }, // 1. (Short title and commencement)
  { n: 6, y: 1281 }, // (1) This Act may be cited
]
// final.akn.xml lines 3, 7, 32-33, 41, 43-44, 46-48, 50, abridged. The formula has no marker: Codify inserts it from the jurisdiction config.
const xml = [
  ['<meta>'],
  ['  … <FRBRuri value="/akn/xa/act/1992/7"/>', 2],
  ['<preface>'],
  ['  <p>THE ATLANTIS GAZETTE</p> …', 1],
  ['  <p>WHEREAS … AS FOLLOWS:</p>', 3],
  ['<preamble>'],
  ['  <formula refersTo="#codify">', 0, true],
  ['    from the config, not the page', 0, false, 'note'],
  ['<body>'],
  ['  <part eId="part_I">', 4],
  ['    <heading>PRELIMINARY</heading>'],
  ['    <section eId="part_I__sec_1">', 5],
  ['      <num>1</num>'],
  ['      <point eId="part_I__sec_1__point_1">', 6],
]

const markerStyle = `<style>.marker{fill:#fff;stroke:currentColor;stroke-width:1}text.marker-n{font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;font-weight:500;text-anchor:middle;dominant-baseline:central}@media (prefers-color-scheme:dark){.marker{fill:#131615}}</style>`
const marker = (cx, cy, n) => `<circle cx="${cx}" cy="${cy}" r="8" class="marker"/>${text(cx, cy, n, 'marker-n', 'middle')}`

const PITCH = 20
function spread(ys) {
  const p = [...ys]
  for (let k = 0; k < 200; k++)
    for (let i = 1; i < p.length; i++) {
      const d = PITCH - (p[i] - p[i - 1])
      if (d > 0) (p[i - 1] -= d / 2), (p[i] += d / 2)
    }
  return p.map((v) => Math.round(v))
}

function page(out, x, y, w) {
  const h = Math.round((w * CROP.h) / CROP.w)
  out.push(`<image href="${href}" x="${x}" y="${y}" width="${w}" height="${h}" preserveAspectRatio="none" class="no-mirror"/>`)
  out.push(`<rect x="${x}" y="${y}" width="${w}" height="${h}" class="s2"/>`)
  const ty = targets.map((t) => y + ((t.y - CROP.y) * h) / CROP.h)
  const my = spread(ty)
  targets.forEach((t, i) => {
    out.push(line([[17, my[i]], [24, my[i]], [x - 2, Math.round(ty[i])]], 's2'))
    out.push(marker(8, my[i], t.n))
  })
  return y + h
}

function code(out, x, y) {
  xml.forEach(([s, n, em, cls = 'code'], i) => {
    const by = y + 12 + i * 16
    if (n) out.push(marker(x - 18, by - 4, n))
    const indent = s.length - s.trimStart().length // SVG collapses leading spaces; indent by the mono advance instead
    out.push(text(x + indent * 7.2, by, s.trimStart(), em ? `${cls} em` : cls))
  })
  return y + xml.length * 16
}

const head = (out, x, y, label, file) => out.push(text(x, y + 13, label), text(x, y + 29, file, 'code'))

// Stacked at every width: side by side, the scan drew at 0.16 scale and its lower lines could not be read.
function stacked(w) {
  const out = []
  head(out, 40, 0, 'Page 1 of the scan', 'pages/page-001.png')
  const b1 = page(out, 40, 40, w - 40)
  head(out, 26, b1 + 32, 'The Akoma Ntoso, abridged', 'final.akn.xml')
  return { out, height: code(out, 26, b1 + 72) + 8 }
}

const title =
  'Page 1 of the worked example scan beside the Akoma Ntoso it became: the masthead and the recital with its enacting words go to preface, the act number to the work URI /akn/xa/act/1992/7, PART I to part_I, section 1 to part_I__sec_1 and subsection (1) to part_I__sec_1__point_1.'

for (const [name, fn, width] of [
  ['v2-page-to-akn', () => stacked(624), 624],
  ['v2-page-to-akn.narrow', () => stacked(358), 358],
]) {
  const { out, height } = fn()
  svg(name, width, height, [markerStyle, ...out], title)
}
