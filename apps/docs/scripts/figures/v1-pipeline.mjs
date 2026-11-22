// V1, the pipeline. Counts come from the worked example's bundle (manifest.json, events.jsonl); see the figure kit.
// Writes v1-pipeline-summary.svg (all widths), v1-pipeline.svg (detail, wide) and v1-pipeline.narrow.svg.
import { line, modelMark, rect, svg, text } from './kit.mjs'

const stages = [
  { label: 'Source PDF', note: '5 scanned pages', files: [] },
  { label: 'Read pages', note: '5 pages, one read each', model: true, config: true, files: ['pages/page-001.png …', 'source.txt', 'page_spans.json', 'segmentation.json'] },
  { label: 'Read metadata', note: '1 call: title, date, number', model: true, config: true, files: [] },
  { label: 'Find anchors', note: '26 found by the rules', config: true, files: ['anchors.jsonl', 'ambiguity.jsonl'] },
  { label: 'Build scaffold', note: '26 headings, bodies empty', config: true, files: ['scaffold.bluebell'] },
  { label: 'Fill text', note: '3 windows, one call each', model: true, config: true, files: ['coverage.json'] },
  { label: 'Parse', note: 'Bluebell to Akoma Ntoso', config: true, files: [] },
  { label: 'Enrich', note: '11 passes, one with a model', model: true, config: true, files: [] },
  { label: 'Validate', note: '1 finding, grade warning', files: ['validator.json'], join: true },
  { label: 'Akoma Ntoso XML', note: '/akn/xa/act/1992/7', noteClass: 'code', files: ['final.akn.xml', ['for the whole run:', 'note'], 'manifest.json', 'events.jsonl'], em: true },
  { label: 'Store', note: 'PostgreSQL, by codify load', files: [] },
]
const surfaces = [
  ['Command line', 'codify'],
  ['HTTP API', 'codify serve'],
  ['MCP server', 'codify mcp'],
  ['Web app', 'apps/web'],
]

const BX = 136 // spine boxes start
const BW = 200
const RAIL = 120
const GAP = 16

function draw({ width, files }) {
  const out = []
  // Header: the config, and the legend.
  out.push(rect(BX, 0, BW, 40))
  out.push(text(BX + 12, 17, 'Jurisdiction config'))
  out.push(text(BX + 12, 32, 'xa/config.json', 'code'))
  out.push(modelMark(0, 7), text(14, 14, 'calls a model', 'note'))
  if (files === 'column') out.push(text(352, 32, 'In the bundle', 'micro'))

  let y = 60
  const rows = []
  for (const s of stages) {
    const inner = files === 'inline' ? s.files : []
    const h = files === 'column' ? 40 + Math.max(0, s.files.length - 2) * 16 : 40 + inner.length * 16
    rows.push({ ...s, y, h })
    y += h + GAP
  }
  const cx = BX + BW / 2
  rows.forEach((r, i) => {
    out.push(rect(BX, r.y, BW, r.h, r.em ? 'em box' : 's box'))
    out.push(text(BX + 12, r.y + 17, r.label, r.em ? 'em' : ''))
    out.push(text(BX + 12, r.y + 32, r.note, r.noteClass ?? 'note'))
    if (r.model) out.push(modelMark(BX + BW - 19, r.y + 9))
    const fileText = (x, y, f) => (Array.isArray(f) ? text(x, y, ...f) : text(x, y, f, 'code'))
    if (files === 'inline') r.files.forEach((f, j) => out.push(fileText(BX + 12, r.y + 50 + j * 16, f)))
    if (files === 'column' && r.files.length) {
      out.push(line([[BX + BW, r.y + 13], [346, r.y + 13]], 's2'))
      r.files.forEach((f, j) => out.push(fileText(352, r.y + 17 + j * 16, f)))
    }
    const next = rows[i + 1]
    if (next) out.push(line([[cx, r.y + r.h], [cx, next.y - 2]], 's', true))
  })

  // Config rail: from the config box down the inline-start side, a tick into each stage that reads it.
  const fed = rows.filter((r) => r.config)
  const last = fed.at(-1)
  out.push(line([[BX, 20], [RAIL, 20], [RAIL, last.y + 20]], 's2'))
  for (const r of fed) out.push(line([[RAIL, r.y + 20], [BX - 2, r.y + 20]], 's2', 'arrow2'))

  // Publisher XML skips every stage before Validate and joins there.
  const src = rows[0]
  const join = rows.find((r) => r.join)
  out.push(rect(0, src.y, 108, 40))
  out.push(text(10, src.y + 17, 'Publisher XML'))
  out.push(text(10, src.y + 32, 'no model', 'note'))
  out.push(line([[54, src.y + 40], [54, join.y + 20], [BX - 2, join.y + 20]], 's', true))

  // Read surfaces: one container under the store, four across when wide, two by two when narrow.
  const store = rows.at(-1)
  const top = store.y + store.h + GAP
  const cols = width >= 600 ? 4 : 2
  const cw = width / cols
  const ch = 28 + Math.ceil(surfaces.length / cols) * 40
  out.push(line([[cx, store.y + store.h], [cx, top - 2]], 's', true))
  out.push(rect(0, top, width, ch))
  out.push(text(12, top + 20, 'Read through', 'micro'))
  surfaces.forEach(([label, cmd], k) => {
    const sx = (k % cols) * cw + 12
    const sy = top + 28 + Math.floor(k / cols) * 40
    out.push(text(sx, sy + 14, label))
    out.push(text(sx, sy + 30, cmd, 'code'))
  })
  return { out, height: top + ch + 1, width }
}

const title = (detail) =>
  `The Codify pipeline, top to bottom: a source PDF is read, its metadata read, anchors found by the jurisdiction config's rules, a scaffold built and filled, parsed to Akoma Ntoso, enriched and validated, then stored and read through the command line, HTTP API, MCP server and web app.${detail ? ' Each stage is shown with the bundle files it leaves; manifest.json and events.jsonl cover the whole run.' : ''}`

for (const [name, opts] of [
  ['v1-pipeline-summary', { width: 358, files: null }],
  ['v1-pipeline', { width: 624, files: 'column' }],
  ['v1-pipeline.narrow', { width: 358, files: 'inline' }],
]) {
  const { out, height, width } = draw(opts)
  svg(name, width, height, out, title(!!opts.files))
}
