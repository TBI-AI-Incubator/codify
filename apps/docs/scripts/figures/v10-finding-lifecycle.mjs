// V10, a finding's lifecycle, as two figures: V10a structure gate to grade, V10b the repair loop.
// Sources: structure.py halt_policy, structural_quality_grade.py, repair/loop.py, edit_ops.py apply_plan,
// sandbox.py preview_plan, ops.py plan_risk, agent.py REVISION_BUDGET; worked example from bundle-xa.
import { line, modelMark, rect, svg, text } from './kit.mjs'

const N = (s) => [s, 'note']
const C = (s) => [s, 'code']
const MONO = `font-family="'Geist Mono',ui-monospace,monospace"`
const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

// A note line may carry `identifiers` in backticks, set in mono.
function line_(x, y, s, cls) {
  if (cls !== 'note' || !s.includes('`')) return text(x, y, s, cls)
  const parts = s.split('`').map((p, i) => (i % 2 ? `<tspan ${MONO}>${esc(p)}</tspan>` : esc(p)))
  return `<text x="${x}" y="${y}" class="note">${parts.join('')}</text>`
}

const gradeStages = [
  {
    label: 'Structure gates',
    lines: [N('A gate that fires lands the document;'), N('adds `structure_halted` (error)')],
    exit: { when: 'cannot land', label: 'Run fails', lines: [N('No `final.akn.xml`; grade ungraded'), C('AnchorCoverageError'), C('BodyFillError')] },
  },
  {
    label: 'Validate',
    lines: [N('Each finding: check, severity,'), N('usually an `eId`')],
    exit: { aside: true, label: 'Worked example', lines: [C('missing_container_title'), C('warning, part_II')] },
  },
  {
    label: 'Grade',
    lines: [N('Any error: blocking. Any warning: warning.'), N('Otherwise clean; info never counts.')],
    exit: { aside: true, label: 'Worked example', lines: [C('grade: warning')] },
  },
]

const repairStages = [
  {
    label: 'Repair permitted?',
    lines: [N('The check must allow an operation')],
    down: 'yes',
    exit: { when: 'no', label: 'Skipped', lines: [C('skipped_non_actionable'), N('The worked example stops here:'), N('fix the config, ingest again')] },
  },
  {
    label: 'Plan',
    lines: [N('Typed operations on named `eIds`,'), N('from a model or a person')],
    model: true,
    plan: true,
  },
  {
    label: 'Six guards, on a copy',
    em: true,
    lines: [
      N('Operation permitted, `eIds` in scope'),
      N('Passes the lenient schema'),
      N('Finding cleared, no new finding'),
      N('Body text does not shrink'),
      N('`meta` and other text unchanged'),
      N('Written body shares enough words'),
      N('with the page'),
    ],
    down: 'all pass',
    exit: {
      when: 'fail',
      label: 'Rejected',
      lines: [N('Reasons go back to the model,'), N('up to 3 revisions; after 3:'), C('revision_budget_exhausted')],
      loop: true,
    },
  },
  {
    label: 'Risk',
    lines: [N('Its riskiest operation’s class, raised'), N('to high for a body with no page'), N('evidence, a changed source, or a'), N('rewritten `duplicate_number` body')],
    down: 'low, medium',
    exit: { when: 'high', label: 'Queued', lines: [C('queued_for_approval'), N('Proposal pending; document unchanged')] },
  },
  {
    label: 'Applied',
    lines: [C('cleared'), N('The edit stays in the document')],
  },
]

const boxH = (n) => 40 + (n - 1) * 16
const GAP = 24

function draw(stages, width, gap = 48) {
  const wide = width >= 600
  const SW = wide ? 296 : width
  const EX = SW + gap
  const EW = width - EX
  const hasModel = stages.some((s) => s.model)
  const out = hasModel ? [modelMark(width - 7 - 92, 7), text(width - 88, 14, 'calls a model', 'note')] : []
  let y = hasModel ? 32 : 1
  const rows = []
  for (const s of stages) {
    const inner = !wide && s.exit
    let n = s.lines.length
    if (inner) n += 1 + s.exit.lines.length
    let h = boxH(n)
    if (inner) h += 16
    const eh = wide && s.exit ? boxH(s.exit.lines.length) : 0
    rows.push({ ...s, y, h: Math.max(h, eh), sh: h, eh })
    y += Math.max(h, eh) + GAP
  }
  const cx = 40
  rows.forEach((r, i) => {
    const h = wide ? r.sh : r.h
    out.push(rect(0, r.y, SW, h, r.em ? 'em box' : 's box'))
    out.push(text(12, r.y + 17, r.label, r.em ? 'em' : ''))
    if (r.model) out.push(modelMark(SW - 19, r.y + 9))
    let ly = r.y + 32
    for (const [s, cls] of r.lines) (out.push(line_(12, ly, s, cls)), (ly += 16))
    if (!wide && r.exit) {
      const e = r.exit
      out.push(line([[12, ly - 4], [SW - 12, ly - 4]], 's2'))
      ly += 12
      out.push(text(12, ly, e.aside ? e.label : `If ${e.when}: ${e.label.toLowerCase()}`, 'micro'))
      ly += 16
      for (const [s, cls] of e.lines) (out.push(line_(12, ly, s, cls)), (ly += 16))
    }
    if (wide && r.exit) {
      const e = r.exit
      const ey = r.y
      if (e.aside) {
        out.push(line([[SW, ey + 13], [EX - 8, ey + 13]], 's2 dash'))
        out.push(text(EX, ey + 17, e.label, 'micro'))
      } else {
        out.push(rect(EX, ey, EW, r.eh))
        out.push(text(EX + 12, ey + 17, e.label))
        out.push(line([[SW, ey + 20], [EX - 2, ey + 20]], 's', true))
        out.push(text(SW + gap / 2, ey + 14, e.when, 'note', 'middle'))
      }
      e.lines.forEach(([s, cls], j) => out.push(line_(e.aside ? EX : EX + 12, ey + (e.aside ? 33 : 32) + j * 16, s, cls)))
      if (e.loop) {
        const plan = rows.find((p) => p.plan)
        const lx = EX + EW / 2
        out.push(line([[lx, ey], [lx, plan.y + 20], [SW + 2, plan.y + 20]], 's2', 'arrow2'))
        out.push(text(SW + 12, plan.y + 14, 'revise', 'note'))
      }
    }
    const next = rows[i + 1]
    if (next) {
      out.push(line([[cx, r.y + h], [cx, next.y - 2]], 's', true))
      if (r.down) out.push(text(cx + 10, r.y + h + 16, r.down, 'note'))
    }
  })
  const last = rows.at(-1)
  return { out, height: last.y + (wide ? last.sh : last.h) + 1 }
}

const figures = [
  [
    'v10a-structure-to-grade',
    gradeStages,
    80,
    'From structure gate to grade: a gate that fires lands the document with a structure_halted error, and only AnchorCoverageError or BodyFillError fails the run; the validator writes findings; the grade folds them, warning for the worked example.',
  ],
  [
    'v10b-repair-loop',
    repairStages,
    48,
    'The repair loop: a check with no permitted operation is skipped; otherwise a plan of typed operations passes six guards on a copy, a rejected model plan is revised up to 3 times, and a low or medium risk plan is applied while a high risk plan, which includes any body written with no page evidence, is queued for approval.',
  ],
]

for (const [name, stages, gap, title] of figures)
  for (const [suffix, width] of [['', 624], ['.narrow', 358]]) {
    const { out, height } = draw(stages, width, gap)
    svg(name + suffix, width, height, out, title)
  }
