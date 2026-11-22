// V6, the open-core line. Schematic: every row comes from docs/open-core/what-is-open.md's status tables.
// Writes v6-open-core.svg (wide) and v6-open-core.narrow.svg.
import { line, rect, svg, text } from './kit.mjs'

const available = [
  ['Read, structure, validate', 'codify ingest-one'],
  ['Validation and grade', 'in every bundle', 'note'],
  ['Bulk publisher indexes', 'codify index-*, crawl-ocs'],
  ['Storage in PostgreSQL', 'codify load'],
  ['Hybrid search', 'codify search'],
  ['Directive-to-law comparison', 'codify compare'],
  ['Read API', 'codify serve'],
  ['MCP server for agents', 'codify mcp'],
  ['Web app, source checkout', 'apps/web'],
  ['Jurisdiction configs', 'synthetic, public_reference'],
]
const libraryOnly = [
  ['Repair agent', 'codify.repair'],
  ['Acquisition adapters', 'codify.acquisition'],
  ['Translation', 'codify.translate'],
  ['Lens interface', 'codify.lenses'],
]
const outside = [
  'Commercial lens detectors and catalogues',
  'Customer corpora and real documents',
  'Other jurisdiction configs',
  'Sign-in, roles, tenancy, audit trail',
  'Durable job orchestration',
  'Hosting, backup, upgrade, support',
]

const PITCH = 34
const chip = (x, y, w, s) => [rect(x, y, w, 20, 's2 box'), text(x + 8, y + 14, s, 'micro')]

function items(list, x, y, cols, colW) {
  const out = []
  list.forEach(([label, sub, cls], i) => {
    const ix = x + (i % cols) * colW
    const iy = y + Math.floor(i / cols) * PITCH
    out.push(text(ix, iy + 12, label), text(ix, iy + 27, sub, cls ?? 'code'))
  })
  return { out, end: y + Math.ceil(list.length / cols) * PITCH }
}

const endpointBox = (x, y, w) => [
  rect(x, y, w, 92),
  text(x + 12, y + 17, 'Your model endpoint'),
  text(x + 12, y + 32, 'OpenAI-compatible,', 'note'),
  text(x + 12, y + 48, 'used to read scans,', 'note'),
  text(x + 12, y + 64, 'structure, compare,', 'note'),
  text(x + 12, y + 80, 'embed and translate', 'note'),
]
const lensBox = (x, y, w) => [
  rect(x, y, w, 72),
  text(x + 12, y + 17, 'Your lens'),
  text(x + 12, y + 32, 'a package with an', 'note'),
  text(x + 12, y + 48, 'entry point in', 'note'),
  text(x + 12, y + 64, 'codify.lenses', 'code'),
]

function notInCore(y, width, cols) {
  const colW = width / cols
  const rows = Math.ceil(outside.length / cols)
  const h = 44 + (rows - 1) * 20 + 16
  const out = [rect(0, y, width, h, 's2 dash box'), text(12, y + 20, 'Not in the core', 'micro')]
  outside.forEach((s, i) => out.push(text(12 + (i % cols) * colW, y + 44 + Math.floor(i / cols) * 20, s)))
  return { out, end: y + h }
}

function draw(wide) {
  const W = wide ? 624 : 358
  const CW = wide ? 424 : 358 // core width
  const cols = wide ? 2 : 1
  const colW = wide ? 204 : 0
  const out = []
  const body = []
  body.push(text(12, 20, 'Codify core'), text(12, 36, 'Apache-2.0; codify-core on PyPI', 'code'))
  body.push(...chip(12, 52, 84, 'Available'))
  const a = items(available, 12, 84, cols, colW)
  body.push(...a.out)
  body.push(...chip(12, a.end + 8, 108, 'Library only'))
  const l = items(libraryOnly, 12, a.end + 40, cols, colW)
  body.push(...l.out)
  const coreH = l.end + 8
  const lensRow = l.end - PITCH
  out.push(rect(0, 0, CW, coreH, 'em box'), ...body)

  let y
  if (wide) {
    // Seam: an opening in the core's inline-end edge beside the lens interface.
    const gy = lensRow - 4
    out.push(`<rect x="${CW - 3}" y="${gy}" width="6" height="28" class="box"/>`)
    out.push(line([[CW - 6, gy], [CW + 6, gy]], 's'), line([[CW - 6, gy + 28], [CW + 6, gy + 28]], 's'))
    out.push(...endpointBox(456, 0, 168), line([[CW, 20], [454, 20]], 's', true))
    out.push(...lensBox(456, lensRow, 168), line([[456, lensRow + 18], [CW - 80, lensRow + 18]], 's', true))
    y = Math.max(coreH, lensRow + 72) + 24
  } else {
    // Seam: an opening in the core's bottom edge under the lens interface.
    const gx = 36
    out.push(`<rect x="${gx}" y="${coreH - 3}" width="28" height="6" class="box"/>`)
    out.push(line([[gx, coreH - 6], [gx, coreH + 6]], 's'), line([[gx + 28, coreH - 6], [gx + 28, coreH + 6]], 's'))
    const by = coreH + 24
    out.push(...lensBox(0, by, 171), line([[gx + 14, by], [gx + 14, coreH - 4]], 's', true))
    out.push(...endpointBox(187, by, 171), line([[272, coreH], [272, by - 2]], 's', true))
    y = by + 92 + 24
  }
  const n = notInCore(y, W, wide ? 2 : 1)
  out.push(...n.out)
  return { out, width: W, height: n.end + 1 }
}

const title =
  'The open-core line. Inside, the Codify core (under the Apache License 2.0; the Python package is codify-core on PyPI): available as commands and apps are reading, structuring and validating a PDF (codify ingest-one), validation and the quality grade in every bundle, bulk publisher indexes (codify index-* and crawl-ocs), storage in PostgreSQL (codify load), hybrid search (codify search), directive-to-law comparison (codify compare), the read API (codify serve), the MCP server for agents (codify mcp), the web app (apps/web, run from a source checkout, not in the PyPI package) and the jurisdiction configs flagged synthetic or public_reference; library only are the repair agent (codify.repair), acquisition adapters (codify.acquisition), translation (codify.translate) and the lens interface (codify.lenses). The core calls your own OpenAI-compatible model endpoint to read scans, structure, compare, embed and translate. Your lens, a package with an entry point in codify.lenses, plugs in through an opening in the line beside the lens interface. Not in the core: the commercial lens detectors and their catalogues, customer corpora and real documents, other jurisdiction configs, sign-in, roles, tenancy and an audit trail, durable job orchestration, and hosting, backup, upgrade and support.'

for (const [name, wide] of [['v6-open-core', true], ['v6-open-core.narrow', false]]) {
  const { out, width, height } = draw(wide)
  svg(name, width, height, out, title)
}
