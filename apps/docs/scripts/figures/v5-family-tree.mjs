// V5, one work and its expressions across dates and languages, from the worked example and the amendments and translate guides.
// Writes v5-family-tree.svg (wide) and v5-family-tree.narrow.svg.
import { line, rect, svg, text } from './kit.mjs'

const work = { uri: '/akn/xa/act/1992/7', note: 'Legislation Act, 1992: one URI for its whole life' }
const eng1992 = { uri: '/eng@1992-05-02', notes: ['As enacted', 'URI fixed, still citable'], file: 'final.akn.xml' }
const eng2012 = { uri: '/eng@2012-01-01', notes: ['Current text', ['Amended by ', '/akn/xa/act/2011/5']], file: 'consolidated-2012.akn.xml', em: true }
const fra2012 = { uri: '/fra@2012-01-01', notes: ['Translation, not authoritative', 'Never current'], file: 'translated-fra.akn.xml' }
const key = ['Each box is one expression: one text,', 'in one language, from one date.', 'Shaded strip: its file, a manifestation.']
const keyWide = ['Each box is one expression: one text, in one language, from one date.', 'Shaded strip: its file, a manifestation.']
const link = 'same work, translated from it'
const H = 84
const title = 'One work, /akn/xa/act/1992/7, with three expressions: English as enacted on 1992-05-02, English as amended from 2012-01-01 (current), and a French translation of the 2012 text, each with its file.'

function note(x, y, n) {
  if (!Array.isArray(n)) return text(x, y, n, 'note')
  return `<text x="${x}" y="${y}" class="note">${n[0]}<tspan class="code">${n[1]}</tspan></text>`
}

function expression(out, x, y, w, e) {
  out.push(rect(x, y, w, H, 'box'))
  out.push(`<rect x="${x}" y="${y + 56}" width="${w}" height="${H - 56}" class="box-lo"/>`)
  out.push(line([[x, y + 56], [x + w, y + 56]], 's2'))
  out.push(rect(x, y, w, H, e.em ? 'em' : 's'))
  out.push(text(x + 12, y + 17, e.uri, e.em ? 'code em' : 'code'))
  e.notes.forEach((n, i) => out.push(note(x + 12, y + 33 + i * 16, n)))
  out.push(text(x + 12, y + 75, e.file, 'code'))
}

function wide() {
  const out = []
  const C1 = 72, C2 = 356, W = 264
  out.push(text(0, 28, 'Work', 'micro'))
  out.push(rect(C1, 4, C2 + W - C1, 40))
  out.push(text(C1 + 12, 21, work.uri, 'code'))
  out.push(text(C1 + 12, 37, work.note, 'note'))
  // Time runs left to right; each trunk from the work marks its expressions' date where it crosses the axis.
  out.push(text(0, 76, 'Date', 'micro'))
  out.push(line([[C1, 72], [C2 + W, 72]], 's2', 'arrow2'))
  for (const [x, d] of [[C1 + 24, '1992-05-02'], [C2 + 24, '2012-01-01']]) {
    out.push(line([[x, 44], [x, 94]], 's2'))
    out.push(text(x + 8, 64, d, 'code'))
  }
  out.push(text(0, 124, 'English', 'micro'))
  expression(out, C1, 96, W, eng1992)
  expression(out, C2, 96, W, eng2012)
  out.push(text(0, 244, 'French', 'micro'))
  out.push(line([[C2 + 24, 180], [C2 + 24, 214]], 's2', 'arrow2'))
  out.push(text(C2 + 32, 202, link, 'note'))
  expression(out, C2, 216, W, fra2012)
  keyWide.forEach((k, i) => out.push(text(C1, 328 + i * 16, k, 'note')))
  return svg('v5-family-tree', 624, 352, out, title)
}

function narrow() {
  const out = []
  const X = 24, W = 332, T = 12
  out.push(rect(0, 4, 356, 56))
  out.push(text(12, 21, 'Work', 'micro'))
  out.push(text(12, 37, work.uri, 'code'))
  out.push(text(12, 53, work.note, 'note'))
  // The trunk runs down the inline-start side: time runs top to bottom.
  const rows = [
    [88, '1992-05-02  ·  English', eng1992],
    [208, '2012-01-01  ·  English', eng2012],
    [348, '2012-01-01  ·  French', fra2012],
  ]
  for (const [y, head, e] of rows) {
    out.push(text(X, y, head, 'micro'))
    out.push(line([[T, y + 28], [X - 2, y + 28]], 's2'))
    expression(out, X, y + 8, W, e)
  }
  out.push(line([[T, 60], [T, 376]], 's2'))
  // The translation link runs down the inline-end side, clear of the French heading.
  out.push(line([[X + W - 24, 300], [X + W - 24, 354]], 's2', 'arrow2'))
  out.push(text(X + W - 32, 326, link, 'note', 'end'))
  key.forEach((k, i) => out.push(text(0, 468 + i * 16, k, 'note')))
  return svg('v5-family-tree.narrow', 358, 508, out, title)
}

wide()
narrow()
