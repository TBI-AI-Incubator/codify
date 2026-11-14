// V19, a translation run with each check beside the stage that raises it, with a narrow cut.
// Sources: translate.py translate_document, anchors.py walk_source, notes.py, translate_bodies.py,
// repair.py MAX_REPAIR_ITERATIONS, audit.py audit_translation, clause_parity.py, judge.py, quality_grade.py, delivery.py.
import { line, modelMark, rect, svg, text } from './kit.mjs'

const MONO = `font-family="'Geist Mono',ui-monospace,monospace"`
const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

// A note may carry `identifiers` in backticks, set in mono.
function note(x, y, s, cls = 'note') {
  if (!s.includes('`')) return text(x, y, s, cls)
  const parts = s.split('`').map((p, i) => (i % 2 ? `<tspan ${MONO}>${esc(p)}</tspan>` : esc(p)))
  return `<text x="${x}" y="${y}" class="${cls}">${parts.join('')}</text>`
}

// Each line may carry the flag it raises; `flags` sit beside the stage as a whole.
const stages = [
  { label: 'Units by eId', lines: ['Each element with an `eId` is one unit'], flags: ['translate_skipped_without_eid'] },
  {
    label: 'Glossary',
    model: true,
    lines: ['One call over the whole act; a', '`PinnedTerm` replaces the model’s entry'],
    flags: ['notes_partial', 'notes_failed'],
  },
  {
    label: 'Translate',
    model: true,
    lines: ['Batches of 8 units by chapter; numbers', 'and citations go as placeholders; earlier', 'answers in a chapter go in as examples'],
    flags: ['translate_sentinel_missing'],
  },
  {
    label: 'Repair',
    model: true,
    lines: ['A unit with a lost placeholder or an', 'untranslated line goes again, up to 3 times'],
    flags: ['translate_sentinel_unrepaired'],
  },
  { label: 'Write', lines: ['Answers patched onto a copy of the source'] },
  {
    label: 'Deterministic audit',
    em: true,
    lines: [
      ['Defined terms present in the output'],
      ['Volume against the source', 'translate_low_coverage'],
      ['Placeholders restored'],
      ['Money amounts neither lost nor doubled'],
      ['Sentences per provision', 'translate_clause_gap'],
      ['Source script left in the text'],
      ['One drafting voice for obligations', 'register_deontic_mixed'],
      ['Gazette headers, titles, front matter'],
    ],
  },
  {
    label: 'Judge, optional',
    model: true,
    dash: true,
    lines: ['Scores up to 20 sampled provisions; its', 'repairs are counted, not written'],
    flags: ['judge_fidelity', 'and one code per dimension'],
  },
]

const boxH = (n) => 40 + (n - 1) * 16
const GAP = 24

function draw(width) {
  const wide = width >= 600
  const SW = wide ? 296 : width
  const EX = SW + 48
  const out = [modelMark(width - 99, 7), text(width - 88, 14, 'calls a model', 'note')]
  if (wide) out.push(text(EX, 14, 'Flags raised', 'micro'))
  let y = 32
  const cx = 40
  stages.forEach((s) => {
    const lines = s.lines.map((l) => (Array.isArray(l) ? l : [l]))
    const inlineFlags = lines.filter((l) => l[1]).map((l) => l[1])
    const narrowFlags = wide ? [] : [...inlineFlags, ...(s.flags || [])]
    let h = boxH(lines.length)
    if (narrowFlags.length) h += 28 + narrowFlags.length * 16
    out.push(rect(0, y, SW, h, s.em ? 'em box' : s.dash ? 's box dash' : 's box'))
    out.push(text(12, y + 17, s.label, s.em ? 'em' : ''))
    if (s.model) out.push(modelMark(SW - 19, y + 9))
    let ly = y + 32
    for (const [l, flag] of lines) {
      out.push(note(12, ly, l))
      if (wide && flag) {
        out.push(line([[SW, ly - 4], [EX - 8, ly - 4]], 's2 dash'))
        out.push(text(EX, ly, flag, 'code'))
      }
      ly += 16
    }
    if (wide && s.flags) {
      out.push(line([[SW, y + 13], [EX - 8, y + 13]], 's2 dash'))
      s.flags.forEach((f, j) => out.push(note(EX, y + 17 + j * 16, f, f.includes(' ') ? 'note' : 'code')))
    }
    if (narrowFlags.length) {
      out.push(line([[12, ly - 4], [SW - 12, ly - 4]], 's2'))
      ly += 12
      out.push(text(12, ly, 'Flags raised', 'micro'))
      ly += 16
      for (const f of narrowFlags) (out.push(note(12, ly, f, f.includes(' ') ? 'note' : 'code')), (ly += 16))
    }
    out.push(line([[cx, y + h], [cx, y + h + GAP - 2]], 's', true))
    y += h + GAP
  })
  // The result, and the two functions a caller runs on it.
  out.push(rect(0, y, width, 40))
  out.push(text(12, y + 17, 'TranslationResult'))
  out.push(note(12, y + 32, '`akn_xml`, `notes`, `audit`, `flags`'))
  const ry = y + 40
  const outs = [
    ['translation_quality_grade', 'A, B, C or ungraded, from audit and flags'],
    ['delivery_findings', 'Per eId: review or advisory, from audit'],
  ]
  y = ry + GAP
  // Wide: one box per function, side by side. Narrow: both in one box.
  const boxes = wide ? [[0, SW, [outs[0]]], [EX, width - EX, [outs[1]]]] : [[0, width, outs]]
  let bottom = y
  for (const [bx, bw, items] of boxes) {
    const h = 40 + (items.length - 1) * 48
    out.push(line([[bx + 40, ry], [bx + 40, y - 2]], 's', true))
    out.push(rect(bx, y, bw, h))
    items.forEach(([fn, what], j) => {
      out.push(text(bx + 12, y + 17 + j * 48, fn, 'code'))
      out.push(note(bx + 12, y + 32 + j * 48, what))
    })
    bottom = Math.max(bottom, y + h)
  }
  return { out, height: bottom + 1 }
}

const title =
  'A translation run: units walked by eId; a model glossary in which pinned terms replace the model’s entries; model translation in chapter batches with numbers and citations as placeholders; up to 3 model repair passes; a deterministic write and audit; an optional sampled model judge; and a result that a caller grades and turns into per-provision findings.'

for (const [suffix, width] of [['', 624], ['.narrow', 358]]) {
  const { out, height } = draw(width)
  svg('v19-translate-run' + suffix, width, height, out, title)
}
