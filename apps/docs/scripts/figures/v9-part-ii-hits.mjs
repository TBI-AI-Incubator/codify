// V9, the three Part II hits. Lines are grep -n line numbers in the worked example's source.txt; the
// recorded Parts are scaffold.bluebell; dropped and kept come from ambiguity.jsonl and anchors.jsonl.
// Writes v9-part-ii-hits.svg (wide) and v9-part-ii-hits.narrow.svg.
import { line, svg, text } from './kit.mjs'

const rows = [
  { n: '15', src: 'PART I — PRELIMINARY', note: 'sections 1 to 3', rec: ['PART I - PRELIMINARY', 'sections 1 to 8'] },
  { n: '30', src: '…as published under Part II;', note: 'in section 2', status: 'dropped' },
  { n: '48', src: 'PART II — PUBLICATION OF LEGISLATION', note: 'sections 4 to 8', status: 'dropped' },
  { n: '83', src: 'PART III — THE PUBLISHING AUTHORITY', note: 'sections 9 to 11', rec: ['PART III - THE PUBLISHING AUTHORITY', 'sections 9 and 10'] },
  { n: '93', src: '…with Part II, to keep the Register…', note: 'in section 10', status: 'kept', rec: ['PART II', 'section 11', 'warning: no heading', 'missing_container_title'] },
]
const PITCH = 48
const TOP = 64
const GUT = 300 // status labels
const REC = 364 // recorded column (wide)

// Recorded entry: code line, then notes; the last line of the Part II entry is the check's name.
const recLines = (x, y, rec) =>
  rec.map((s, j) => text(x, y + j * 16, s, j === 0 || s.includes('_') ? 'code' : 'note'))

function draw(wide) {
  const out = [text(24, 12, 'As printed', 'micro'), text(24, 30, 'source.txt with line numbers', 'code')]
  if (wide) out.push(text(REC, 12, 'Recorded', 'micro'), text(REC, 30, 'scaffold.bluebell', 'code'))
  rows.forEach((r, i) => {
    const y = TOP + i * PITCH
    out.push(text(16, y, r.n, 'code', 'end'), text(24, y, r.src, 'code'), text(24, y + 16, r.note, 'note'))
    if (r.status) out.push(text(GUT, y, r.status, r.status === 'kept' ? 'em' : 'note'))
    if (wide && r.rec) {
      const x0 = r.status ? GUT + 34 : GUT
      out.push(line([[x0, y - 4], [REC - 6, y - 4]], r.status ? 's' : 's2', r.status ? true : 'arrow2'))
      out.push(...recLines(REC, y, r.rec))
    }
  })
  if (wide) return out
  // Narrow: the recorded list follows; a leader runs from "kept" down to the Part it became.
  const top = TOP + rows.length * PITCH + 12
  out.push(text(24, top, 'Recorded', 'micro'), text(24, top + 18, 'scaffold.bluebell', 'code'))
  let y = top + 48
  for (const r of rows.filter((r) => r.rec)) {
    out.push(...recLines(24, y, r.rec))
    if (r.status) {
      const ky = TOP + rows.indexOf(r) * PITCH
      out.push(line([[GUT + 14, ky + 6], [GUT + 14, y - 4], [84, y - 4]], 's', true))
    }
    y += r.rec.length * 16 + 16
  }
  return out
}

const title = 'Three mentions of Part II in the source: two dropped, one kept, and the Parts the scaffold recorded'
svg('v9-part-ii-hits', 624, 316, draw(true), title)
const narrow = draw(false)
svg('v9-part-ii-hits.narrow', 358, 524, narrow, title)
