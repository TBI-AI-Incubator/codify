// V4, section 1 of the worked example in four bundle files. Every line is copied from the run's bundle; see the figure kit.
// Writes v4-four-panels.svg (wide) and v4-four-panels.narrow.svg.
import { line, modelMark, rect, svg, text } from './kit.mjs'

const EID = 'part_I__sec_1'
const CH = 7.2 // Geist Mono advance at 12px

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

// A code line: leading spaces become an x offset; the section's own eId is set in ink at 600.
function code(x, y, s) {
  const indent = s.length - s.trimStart().length
  const body = esc(s.trimStart()).replace(
    new RegExp(`"${EID}"`, 'g'),
    `"<tspan font-weight="600" fill="currentColor">${EID}</tspan>"`,
  )
  return `<text x="${x + indent * CH}" y="${y}" class="code">${body}</text>`
}

const panels = (narrow) => [
  {
    file: 'source.txt',
    where: 'lines 17–19',
    gutter: ['17', '18', '19'],
    lines: [
      'Short title and commencement',
      '1.',
      narrow ? '(1) This Act may be cited as the ...' : '(1) This Act may be cited as the Legislation Act, 1992.',
    ],
  },
  {
    file: 'anchors.jsonl',
    where: 'line 2',
    lines: narrow
      ? [
          '{"kind": "section",',
          ' "keyword": "Short title and commencement",',
          ' "number": "1", "char_offset": 959,',
          ' "line": 17, ..., "parent_eid": "part_I",',
          ` "akn_eid": "${EID}", ...,`,
          ' "source_pass": "marginal_note_units"}',
        ]
      : [
          '{"kind": "section", "keyword": "Short title and commencement",',
          ' "number": "1", "char_offset": 959, "line": 17, ...,',
          ` "parent_eid": "part_I", "akn_eid": "${EID}", ...,`,
          ' "source_pass": "marginal_note_units"}',
        ],
  },
  {
    file: 'scaffold.bluebell',
    where: 'lines 11–15',
    lines: ['  PART I - PRELIMINARY', '', '    SECTION 1 - Short title and commencement', '', '    SECTION 2 - Interpretation'],
    // The eId is not in the file: scaffold_from_anchors returns it beside the text as eid_to_anchor.
    mark: 2,
    margin: [`"${EID}"`, 'held in memory with this header, not in the file'],
  },
  {
    file: 'final.akn.xml',
    where: 'from line 47',
    lines: [
      `<section eId="${EID}">`,
      '  <num>1</num>',
      ...(narrow ? ['  <heading>Short title and', '    commencement</heading>'] : ['  <heading>Short title and commencement</heading>']),
      `  <point eId="${EID}__point_1">`,
      '    <num>(1)</num>',
      '    <content>',
      `      <p eId="${EID}__point_1__p_1">`,
      ...(narrow
        ? ['        This Act may be cited as the', '        Legislation Act, 1992.</p>']
        : ['        This Act may be cited as the Legislation Act, 1992.</p>']),
      '  ...',
    ],
  },
]

const markerStyle = `<style>.marker{fill:#fff;stroke:currentColor;stroke-width:1}text.marker-n{font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;font-weight:500;text-anchor:middle;dominant-baseline:central}@media (prefers-color-scheme:dark){.marker{fill:#131615}}</style>`

const steps = [
  { label: 'Find anchors', note: 'by rule, from the config; the eId is set here' },
  { label: 'Build scaffold', note: 'a header per anchor, no body; the eId stays in memory' },
  { label: 'Fill text, then parse', note: 'the model returns the lines for each eId', model: true },
]

function draw(width) {
  const narrow = width < 600
  const out = [modelMark(0, 7), text(14, 14, 'calls a model', 'note')]
  let y = 28
  panels(narrow).forEach((p, i) => {
    const h = 44 + (p.lines.length + (p.margin ? 2.5 : 0)) * 16
    out.push(rect(0, y, width, h))
    out.push(text(12, y + 21, p.file, 'code'))
    out.push(text(width - 12, y + 21, p.where, 'note', 'end'))
    out.push(line([[0, y + 32], [width, y + 32]], 's2'))
    const x0 = p.gutter ? 36 : 12
    p.lines.forEach((l, j) => {
      const by = y + 52 + j * 16
      if (p.gutter) out.push(text(12, by, p.gutter[j], 'note'))
      if (l) out.push(code(x0, by, l))
    })
    if (p.margin) {
      const my = y + 52 + p.mark * 16 - 4
      const ny = y + 52 + (p.lines.length + 0.5) * 16
      out.push(line([[20, my + 8], [20, ny - 4], [30, ny - 4]], 's2 dash'))
      out.push(`<circle cx="20" cy="${my}" r="8" class="marker"/>`, text(20, my, '1', 'marker-n', 'middle'))
      out.push(code(36, ny, p.margin[0]), text(36, ny + 16, p.margin[1], 'note'))
    }
    y += h
    const s = steps[i]
    if (!s) return
    out.push(line([[16, y], [16, y + 38]], 's', true))
    const lx = s.model ? 46 : 32
    if (s.model) out.push(modelMark(32, y + 9))
    out.push(text(lx, y + 17, s.label))
    out.push(text(lx, y + 32, s.note, 'note'))
    y += 40
  })
  return { out, height: y + 1 }
}

const title = `Section 1 of the worked example, Short title and commencement, in four bundle files. In source.txt, line 17 is the marginal note Short title and commencement, line 18 the number 1 alone, and line 19 begins (1) This Act may be cited as the Legislation Act, 1992. Find anchors, by rule from the config, records it as line 2 of anchors.jsonl: kind section, keyword Short title and commencement, number 1, char_offset 959, line 17, parent_eid part_I, akn_eid part_I__sec_1, source_pass marginal_note_units; the eId is set here. Build scaffold writes a header line per anchor with no body text, and keeps the eId in memory beside each header rather than in the file: scaffold.bluebell lines 11 to 15 read PART I - PRELIMINARY, SECTION 1 - Short title and commencement and SECTION 2 - Interpretation, with blank lines where the bodies go; a note on the SECTION 1 line reads part_I__sec_1, held in memory with this header, not in the file. Fill text calls a model, which returns the lines for each eId, and the parse gives final.akn.xml from line 47: a section with eId part_I__sec_1, num 1, heading Short title and commencement, and a point with eId part_I__sec_1__point_1, num (1), whose paragraph part_I__sec_1__point_1__p_1 holds the text the model filled: This Act may be cited as the Legislation Act, 1992.`

for (const [name, width] of [
  ['v4-four-panels', 624],
  ['v4-four-panels.narrow', 358],
]) {
  const { out, height } = draw(width)
  svg(name, width, height, [markerStyle, ...out], title)
}
