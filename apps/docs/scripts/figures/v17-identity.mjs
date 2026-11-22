// V17, one document's identity before and after set_work_uri. Values are the shipped fixture
// data/fixtures/synthetic/xa/legislation-hcontainer.akn.xml and the output of guide/interoperate's restate.py.
// Writes v17-identity.svg (wide) and v17-identity.narrow.svg.
import { line, svg, text } from './kit.mjs'

const W = '/akn/xa/act/1992-05-14/7'
const groups = [
  ['Work', [
    ['FRBRthis', W, '/akn/xa/act/1992/7/!main'],
    ['FRBRuri', W, '/akn/xa/act/1992/7', true],
    ['FRBRalias', 'Untitled', null, false, 'unchanged; edit by hand'],
    ['FRBRdate', '1992-05-14', null],
  ]],
  ['Expression', [
    ['FRBRthis', `${W}/eng`, '/akn/xa/act/1992/7/eng@1992-05-02', false, 'no /!main, unlike the work; edit by hand'],
    ['FRBRuri', `${W}/eng`, '/akn/xa/act/1992/7/eng@1992-05-02'],
    ['FRBRdate', '2026-08-24', '1992-05-02'],
  ]],
]
const manifestation = 'FRBRthis, FRBRuri and FRBRdate change exactly as the expression’s do'
const footer = ["The schedule's work FRBRthis becomes", '/akn/xa/act/1992/7/!schedule_1']
const title = 'Identity of the Legislation Act extract before and after set_work_uri'

function wide() {
  const out = []
  const A = 112
  const B = 348
  out.push(text(A, 13, 'Before'), text(A, 29, 'legislation-hcontainer.akn.xml', 'code'))
  out.push(text(B, 13, 'After set_work_uri'), text(B, 29, 'legislation-hcontainer.fixed.xml', 'code'))
  out.push(line([[A + 196, 9], [B - 14, 9]], 's2', 'arrow2'))
  let y = 44
  for (const [name, rows] of groups) {
    out.push(line([[0, y], [624, y]], 's2'))
    y += 20
    out.push(text(0, y, name, 'micro'))
    y += 20
    for (const [field, before, after, em, note] of rows) {
      out.push(text(0, y, field, 'code'), text(A, y, before, 'code'))
      if (after) out.push(text(B, y, after, em ? 'code em' : 'code'))
      if (after && note) out.push(text(B, (y += 16), note, 'note'))
      if (!after) out.push(text(B, y, note ?? 'unchanged', 'note'))
      y += 20
    }
  }
  out.push(line([[0, y], [624, y]], 's2'))
  y += 20
  out.push(text(0, y, 'Manifestation', 'micro'), text(A, y, manifestation, 'note'))
  y += 12
  out.push(line([[0, y], [624, y]], 's2'))
  y += 20
  out.push(text(0, y, footer[0], 'note'), text(B, y, footer[1], 'code'))
  return svg('v17-identity', 624, y + 8, out, title)
}

function narrow() {
  const out = []
  out.push(text(0, 13, 'was', 'note'), text(32, 13, 'legislation-hcontainer.akn.xml', 'code'))
  out.push(text(0, 29, 'now', 'note'), text(32, 29, 'legislation-hcontainer.fixed.xml', 'code'))
  let y = 44
  for (const [name, rows] of groups) {
    out.push(line([[0, y], [358, y]], 's2'))
    y += 20
    out.push(text(0, y, name, 'micro'))
    y += 24
    for (const [field, before, after, em, note] of rows) {
      out.push(text(0, y, field, 'code'))
      out.push(text(16, y + 16, 'was', 'note'), text(48, y + 16, before, 'code'))
      out.push(text(16, y + 32, 'now', 'note'))
      if (after) out.push(text(48, y + 32, after, em ? 'code em' : 'code'))
      if (after && note) out.push(text(48, (y += 16) + 32, note, 'note'))
      if (!after) out.push(text(48, y + 32, note ?? 'unchanged', 'note'))
      y += 56
    }
  }
  out.push(line([[0, y - 8], [358, y - 8]], 's2'))
  y += 12
  out.push(text(0, y, 'Manifestation', 'micro'))
  out.push(text(0, y + 20, 'FRBRthis, FRBRuri and FRBRdate change', 'note'), text(0, y + 36, 'exactly as the expression’s do', 'note'))
  y += 56
  out.push(line([[0, y - 8], [358, y - 8]], 's2'))
  y += 12
  out.push(text(0, y, footer[0], 'note'))
  out.push(text(0, y + 16, footer[1], 'code'))
  return svg('v17-identity.narrow', 358, y + 24, out, title)
}

wide()
narrow()
