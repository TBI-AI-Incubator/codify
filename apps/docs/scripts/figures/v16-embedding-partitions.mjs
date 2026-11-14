// V16, how provision_embeddings is partitioned. Names and index states from migration 0020 and codify/storage/partitions.py.
// Writes v16-embedding-partitions.svg (wide) and v16-embedding-partitions.narrow.svg.
import { line, rect, svg, text } from './kit.mjs'

const parent = ['Embeddings table, split by jurisdiction', 'provision_embeddings']
const rows = [['Index', 'provision_embeddings_hnsw_idx'], ['Partitions', 'provision_embeddings_p_*']]
const parts = [
  { label: 'Jurisdictions at 0020', name: 'provision_embeddings_p_<id>', notes: ['HNSW: build, then attach', 'Exact scan until attached'] },
  { label: 'Added after 0020', name: 'provision_embeddings_p_default', notes: ['HNSW from creation', 'Shared until promoted'], em: true },
  { label: 'Promoted from DEFAULT', name: 'provision_embeddings_p_<id>', notes: ['HNSW copied from parent', 'Searched on its own'] },
]

function box(out, x, y, w, p, wide) {
  // Wide boxes are too narrow for the full name; the parent box carries the prefix once.
  const lines = [wide ? p.name.slice('provision_embeddings'.length) : p.name]
  const h = 40 + (lines.length + p.notes.length - 1) * 16
  out.push(rect(x, y, w, h, p.em ? 'em box' : 's box'))
  out.push(text(x + 12, y + 17, p.label, p.em ? 'em' : ''))
  lines.forEach((l, i) => out.push(text(x + 12, y + 32 + i * 16, l, 'code')))
  p.notes.forEach((n, i) => out.push(text(x + 12, y + 32 + (lines.length + i) * 16, n, 'note')))
  return h
}

function parentBox(out, x, w, wide) {
  const rs = wide ? rows : rows.slice(0, 1)
  const h = 40 + rs.length * 16
  out.push(rect(x, 0, w, h))
  out.push(text(x + 12, 17, parent[0]))
  out.push(text(x + 12, 32, parent[1], 'code'))
  rs.forEach(([k, v], i) => out.push(text(x + 12, 48 + i * 16, k, 'note'), text(x + 80, 48 + i * 16, v, 'code')))
  return h
}

const promoteLabel = (out, x, y, anchor) => {
  out.push(text(x, y, 'Promote, writers stopped', '', anchor))
  out.push(text(x, y + 16, 'promote_sql', 'code', anchor))
}

function wide() {
  const out = []
  const ph = parentBox(out, 156, 312, true)
  const W = 192, G = 24, R = ph + 20, Y = ph + 40
  const cx = parts.map((_, i) => i * (W + G) + W / 2)
  out.push(line([[312, ph], [312, R]], 's2'), line([[cx[0], R], [cx[2], R]], 's2'))
  let h = 0
  parts.forEach((p, i) => {
    out.push(line([[cx[i], R], [cx[i], Y]], 's2'))
    h = box(out, i * (W + G), Y, W, p, true)
  })
  const b = Y + h
  out.push(line([[cx[1], b], [cx[1], b + 24], [cx[2], b + 24], [cx[2], b + 2]], 's', true))
  promoteLabel(out, (cx[1] + cx[2]) / 2, b + 44, 'middle')
  return { out, width: 624, height: b + 68 }
}

function narrow() {
  const out = []
  const ph = parentBox(out, 0, 358, false)
  const X = 24, W = 334
  let y = ph + 16
  const tops = []
  parts.forEach((p, i) => {
    tops.push(y)
    const h = box(out, X, y, W, p, false)
    if (i === 1) {
      out.push(line([[X + 24, y + h], [X + 24, y + h + 54]], 's', true))
      promoteLabel(out, X + 40, y + h + 26, 'start')
      y += h + 56
    } else y += h + 16
  })
  out.push(line([[12, ph], [12, tops[2] + 17]], 's2'))
  for (const t of tops) out.push(line([[12, t + 17], [X, t + 17]], 's2'))
  return { out, width: 358, height: y - 16 }
}

const title =
  'The embeddings table provision_embeddings is split by jurisdiction. Jurisdictions that existed when migration 0020 ran each get a partition whose HNSW index is built by hand and attached; every jurisdiction added later shares the DEFAULT partition, indexed from creation, until promote_sql moves it to a partition of its own.'

for (const [name, draw] of [['v16-embedding-partitions', wide], ['v16-embedding-partitions.narrow', narrow]]) {
  const { out, width, height } = draw()
  svg(name, width, height, out, title)
}
