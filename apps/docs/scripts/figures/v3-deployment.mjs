// V3, a deployment and what leaves it. Schematic: ports, routes and variables come from source; see the figure kit.
// Writes v3-deployment.svg (wide) and v3-deployment.narrow.svg.
import { line, rect, svg, text } from './kit.mjs'

const users = [
  ['Browser', 'people reading the law'],
  ['AI agent', 'an MCP client'],
]
const proxy = ['Reverse proxy with sign-in', 'Yours to provide, and the only part that authenticates']
const procs = [
  { label: 'Web app', route: '/', code: ['apps/web/dist/'], notes: ['Static files,', 'keeps nothing'] },
  {
    label: 'HTTP server', route: '/api/proxy/*', code: ['codify serve', '127.0.0.1:8000'],
    notes: ['Runs in memory, uploads', 'in a temporary directory,', 'gone after a restart'], db: true,
  },
  {
    label: 'MCP server', route: '/mcp', code: ['codify mcp --http', '127.0.0.1:8001'],
    notes: ['Sessions in memory,', 'gone after a restart'], db: true,
  },
]
const db = ['PostgreSQL', 'POSTGRES_URL', 'All stored law, the only state that survives a restart']
const egress = [
  { label: 'Chat model', code: 'LLM_BASE_URL', notes: ["Each PDF ingest's page", 'images and text; each MCP', "comparison's provisions"], wide: ['Page images and text of each PDF ingest;', "each MCP comparison's provisions"] },
  { label: 'Embeddings', code: 'EMBEDDING_BASE_URL', notes: ['Each search query and', 'each MCP comparison'], wide: ['Each search query and each MCP comparison'] },
  { label: 'Tracing, if set', code: 'LANGFUSE_HOST', notes: ["Each call's model, tokens", "and cost; each ingest's", 'source path. No prompts', 'or replies.'], wide: ["Each call's model, tokens and cost; each", "ingest's source path. No prompts or replies."] },
]
const cli = ['Command line', 'codify ingest-one', ['Also sends to the chat', 'model and tracing']]
const azure = ['Azure OCR, if set', 'AZURE_OPENAI_ENDPOINT', ['The whole PDF, for a layout pass.', 'The servers never call it.']]

// Box with a label row, then code and note rows; returns its height.
function box(out, x, y, w, label, rows, cls = 's box', labelCls = '', right = null) {
  out.push(rect(x, y, w, 40 + Math.max(0, rows.length - 1) * 16, cls))
  out.push(text(x + 12, y + 17, label, labelCls))
  if (right) out.push(text(x + w - 12, y + 17, right, 'code', 'end'))
  rows.forEach(([s, c], i) => out.push(text(x + 12, y + 32 + i * 16, s, c)))
  return 40 + Math.max(0, rows.length - 1) * 16
}
const rowsOf = (code, notes) => [...code.map((c) => [c, 'code']), ...notes.map((n) => [n, 'note'])]

function wide() {
  const W = 624
  const out = []
  users.forEach(([l, n], i) => {
    box(out, i * 216, 0, 192, l, [[n, 'note']])
    out.push(line([[i * 216 + 96, 40], [i * 216 + 96, 70]], 's', true))
  })
  box(out, 0, 72, W, proxy[0], [[proxy[1], 'note']], 'em box', 'em')

  // Private network: the three processes and the database.
  const top = 152
  const py = 168
  const ph = 104
  out.push(rect(0, top, W, 216, 's2 dash'))
  procs.forEach((p, i) => {
    const x = 12 + i * 208
    const cx = x + 92
    out.push(line([[cx, 112], [cx, py - 2]], 's', true))
    out.push(text(cx + 8, 140, p.route, 'code'))
    out.push(rect(x, py, 184, ph))
    out.push(text(x + 12, py + 17, p.label))
    rowsOf(p.code, p.notes).forEach(([s, c], j) => out.push(text(x + 12, py + 32 + j * 16, s, c)))
    if (p.db) out.push(line([[cx, py + ph], [cx, 294]], 's', true))
  })
  box(out, 244, 296, 344, db[0], [[db[1], 'code'], [db[2], 'note']])
  out.push(text(24, 316, 'Private network', 'micro'))
  out.push(text(24, 332, 'Both servers bind', 'note'))
  out.push(text(24, 348, '127.0.0.1 by default', 'note'))

  // What leaves: dashed, from both servers down a rail out of the private network.
  const ry = top + 232
  const ey = 420
  for (const x of [232, 600]) out.push(line([[x, py + ph], [x, ry]], 's2 dash'))
  out.push(line([[96, ry], [600, ry]], 's2 dash'))
  out.push(text(108, 404, 'Leaves your network', 'micro'))
  egress.forEach((e, i) => {
    const x = i * 216
    out.push(line([[x + 96, ry], [x + 96, ey - 2]], 's2 dash', 'arrow2'))
    box(out, x, ey, 192, e.label, rowsOf([e.code], e.notes))
  })

  const cy = 560
  out.push(text(0, cy - 12, 'From the command line', 'micro'))
  box(out, 0, cy, 192, cli[0], rowsOf([cli[1]], cli[2]))
  box(out, 216, cy, 408, azure[0], rowsOf([azure[1]], azure[2]))
  out.push(line([[192, cy + 20], [214, cy + 20]], 's2 dash', 'arrow2'))
  return { out, width: W, height: cy + 73 }
}

function narrow() {
  const W = 358
  const out = []
  users.forEach(([l, n], i) => {
    box(out, i * 187, 0, 171, l, [[n, 'note']])
    out.push(line([[i * 187 + 85, 40], [i * 187 + 85, 70]], 's', true))
  })
  box(out, 0, 72, W, proxy[0], [['Yours to provide, and the only', 'note'], ['part that authenticates', 'note']], 'em box', 'em')

  // Ingress rail on the inline-start side, the database rail on the inline-end side.
  const top = 144
  let y = top + 16
  const ys = []
  for (const p of procs) {
    const h = box(out, 28, y, 290, p.label, rowsOf(p.code, p.notes), 's box', '', p.route)
    ys.push({ y, h, db: p.db })
    y += h + 16
  }
  out.push(line([[12, 128], [12, ys.at(-1).y + 20]], 's'))
  for (const r of ys) out.push(line([[12, r.y + 20], [26, r.y + 20]], 's', true))
  const dbY = y
  for (const r of ys.filter((r) => r.db)) out.push(line([[318, r.y + 20], [334, r.y + 20]], 's'))
  out.push(line([[334, ys.find((r) => r.db).y + 20], [334, dbY - 2]], 's', true))
  const dh = box(out, 28, dbY, 318, db[0], [[db[1], 'code'], ['All stored law, the only state', 'note'], ['that survives a restart', 'note']])
  y = dbY + dh + 16
  out.push(text(28, y + 12, 'Private network', 'micro'))
  out.push(text(28, y + 28, 'Both servers bind 127.0.0.1 by default', 'note'))
  const bottom = y + 44
  out.push(rect(0, top, W, bottom - top, 's2 dash'))

  // Egress rail, dashed, out of the private network.
  y = bottom + 24
  out.push(text(28, y, 'Leaves your network', 'micro'))
  y += 16
  const ticks = []
  for (const e of egress) {
    const h = box(out, 28, y, 318, e.label, rowsOf([], e.wide), 's box', '', e.code)
    ticks.push(y + 20)
    y += h + 16
  }
  out.push(line([[12, bottom], [12, ticks.at(-1)]], 's2 dash'))
  for (const t of ticks) out.push(line([[12, t], [26, t]], 's2 dash', 'arrow2'))

  y += 12
  out.push(text(0, y, 'From the command line', 'micro'))
  y += 12
  const ch = box(out, 0, y, W, cli[0], [[cli[2].join(' '), 'note']], 's box', '', cli[1])
  const ay = y + ch + 32
  out.push(line([[179, y + ch], [179, ay - 2]], 's2 dash', 'arrow2'))
  const ah = box(out, 0, ay, W, azure[0], [[azure[1], 'code'], ...azure[2].map((n) => [n, 'note'])])
  return { out, width: W, height: ay + ah + 1 }
}

const title =
  'A Codify deployment: a browser and an AI agent reach a reverse proxy with sign-in, the only part that authenticates, which forwards to the web app, codify serve and codify mcp on a private network beside PostgreSQL; dashed arrows show what leaves the network: the chat model, embeddings, tracing if set, and Azure OCR from the command line only.'

for (const [name, draw] of [['v3-deployment', wide], ['v3-deployment.narrow', narrow]]) {
  const { out, width, height } = draw()
  svg(name, width, height, out, title)
}
