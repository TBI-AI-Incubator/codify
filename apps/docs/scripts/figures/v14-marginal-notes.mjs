// V14: the anchor scan on the worked example's source.txt, with and without the marginal-note layout declared.
// Lines from bundle source.txt; eIds and passes from bundle anchors.jsonl; totals from scan-corpus run both ways.
import { line, rect, svg, text } from './kit.mjs'

const excerpt = [
  { t: 'PART I — PRELIMINARY', row: 0 },
  null,
  { t: 'Short title and commencement', row: 1 },
  { t: '1.' },
  { t: '(1) This Act may be cited as ...' },
  null,
  { t: 'Interpretation', row: 2 },
  { t: '2.' },
  { t: '(1) In this Act, unless the ...' },
  { t: '...' },
]
const rows = [
  { with: ['part_I', 'regex'], without: ['part_I', 'regex'] },
  { with: ['part_I__sec_1', 'marginal_note_units'], without: null },
  { with: ['part_I__sec_2', 'marginal_note_units'], without: null },
]
const cols = [
  { head: 'Layout declared', sub: ['marginal_note', 'code'], total: '26 anchors', totalNote: '6 parts, 20 sections' },
  { head: 'Not declared', sub: ['no display block', 'note'], total: '6 anchors', totalNote: '6 parts, 0 sections' },
]

const PITCH = 18
const BLANK = 16

function lay(x0) {
  let y = 0
  return excerpt.map((l) => {
    if (!l) { y += BLANK; return null }
    const r = { ...l, y }
    y += PITCH
    return r
  }).filter(Boolean).map((l) => ({ ...l, x0 }))
}

function cell(out, x, y, v) {
  if (v) out.push(text(x, y, v[0], 'code'), `<text x="${x}" y="${y + 16}" class="note"><tspan font-family="'Geist Mono',ui-monospace,monospace">${v[1]}</tspan> pass</text>`)
  else out.push(text(x, y, 'no anchor', 'note'))
}

function head(out, xs, y) {
  cols.forEach((c, i) => {
    out.push(text(xs[i], y, c.head))
    out.push(text(xs[i], y + 16, ...c.sub))
  })
}

function totals(out, xs, y, width, stacked = false) {
  out.push(line([[0, y], [width, y]], 's2'))
  out.push(text(0, y + 21, 'Whole act', 'micro'))
  if (stacked) y += 20
  cols.forEach((c, i) => {
    out.push(text(xs[i], y + 21, c.total, c.em ? 'em' : ''))
    out.push(text(xs[i], y + 37, c.totalNote, 'note'))
  })
  return y + 45
}

function wide() {
  const out = []
  const BW = 264
  const xs = [316, 476]
  out.push(text(0, 32, 'source.txt', 'code'))
  head(out, xs, 16)
  const top = 48
  const lines = lay(12)
  const bh = lines.at(-1).y + PITCH + 14
  out.push(rect(0, top, BW, bh, 's2 box-lo'))
  for (const l of lines) {
    const by = top + 20 + l.y
    out.push(text(12, by, l.t, 'code'))
    if (l.row !== undefined) {
      out.push(line([[BW + 4, by - 4], [xs[0] - 8, by - 4]], 's2 dash'))
      const r = rows[l.row]
      cell(out, xs[0], by, r.with)
      cell(out, xs[1], by, r.without)
    }
  }
  const h = totals(out, xs, top + bh + 16, 624)
  return { out, h }
}

function narrow() {
  const out = []
  const W = 358
  out.push(text(0, 12, 'source.txt', 'code'))
  const top = 20
  const lines = lay(36)
  const bh = lines.at(-1).y + PITCH + 14
  out.push(rect(24, top, W - 24, bh, 's2 box-lo'))
  const marker = (cx, cy, n) => `<circle cx="${cx}" cy="${cy}" r="10" class="marker s box"/><text x="${cx}" y="${cy}" class="marker-n" text-anchor="middle" dominant-baseline="central">${n}</text>`
  for (const l of lines) {
    const by = top + 20 + l.y
    out.push(text(36, by, l.t, 'code'))
    if (l.row !== undefined) out.push(marker(11, by - 4, l.row + 1))
  }
  const xs = [28, 196]
  let y = top + bh + 24
  head(out, xs, y)
  y += 36
  rows.forEach((r, i) => {
    out.push(marker(11, y - 4, i + 1))
    cell(out, xs[0], y, r.with)
    cell(out, xs[1], y, r.without)
    y += 40
  })
  const h = totals(out, xs, y - 8, W, true)
  return { out, h }
}

const title =
  'The anchor scan over the worked example\'s source.txt. With the marginal-note layout declared, PART I is found as part_I by the regex pass and the headings Short title and commencement and Interpretation as part_I__sec_1 and part_I__sec_2 by the marginal_note_units pass: 26 anchors, 6 parts and 20 sections. Without the display block only the parts are found: 6 anchors, 0 sections.'

for (const [name, fn, w] of [['v14-marginal-notes', wide, 624], ['v14-marginal-notes.narrow', narrow, 358]]) {
  const { out, h } = fn()
  svg(name, w, h, out, title)
}
