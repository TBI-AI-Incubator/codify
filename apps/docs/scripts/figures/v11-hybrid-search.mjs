// V11, hybrid search. Constants: candidate_pool 30 (codify/retrieve/hybrid.py), RRF_K 60 and the fused SUM (codify/storage/retrieval.py).
// Writes v11-hybrid-search.svg (wide) and v11-hybrid-search.narrow.svg.
import { line, modelMark, rect, svg, text } from './kit.mjs'

const arms = [
  { label: 'Lexical arm', notes: { wide: ['BM25 on the query’s words; rare words', 'weigh more than common ones'], narrow: ['BM25 on the query’s', 'words; rare words', 'outweigh common ones'] }, pool: 'Up to 30 best, ranked' },
  { label: 'Dense arm', model: true, notes: { wide: ['An embedding of the query against', 'each provision’s embedding'], narrow: ['Query embedding', 'against each', 'provision’s embedding'] }, pool: 'Up to 30 nearest, ranked' },
]

function draw(width) {
  const narrow = width < 600
  const cut = narrow ? 'narrow' : 'wide'
  const out = []
  const cx = width / 2
  out.push(modelMark(0, 7), text(14, 14, 'calls a model', 'note'))

  let y = 32
  out.push(rect(0, y, width, 40))
  out.push(text(12, y + 17, 'Query'))
  out.push(text(12, y + 32, '"right of access to the official text"', 'code'))
  y += 40
  out.push(line([[cx, y], [cx, y + 14]], 's', true))
  y += 16

  const scopeNotes = narrow
    ? ['One jurisdiction, the current version', 'of each law, normative provisions only']
    : ['One jurisdiction, the current version of each law, normative provisions only']
  const sh = 24 + scopeNotes.length * 16
  out.push(rect(0, y, width, sh, 's box-lo'))
  out.push(text(12, y + 17, 'Scope, applied inside each arm'))
  scopeNotes.forEach((n, i) => out.push(text(12, y + 32 + i * 16, n, 'note')))
  y += sh

  const aw = (width - 16) / 2
  const lines = arms[0].notes[cut].length
  const ah = 56 + lines * 16
  const ay = y + 24
  const fusionY = ay + ah + 24
  arms.forEach((a, i) => {
    const ax = i * (aw + 16)
    const mid = ax + aw / 2
    out.push(line([[mid, y], [mid, ay - 2]], 's', true))
    out.push(rect(ax, ay, aw, ah))
    out.push(text(ax + 12, ay + 17, a.label))
    if (a.model) out.push(modelMark(ax + aw - 19, ay + 9))
    a.notes[cut].forEach((n, j) => out.push(text(ax + 12, ay + 32 + j * 16, n, 'note')))
    out.push(text(ax + 12, ay + 32 + lines * 16 + 8, a.pool, 'note'))
    out.push(line([[mid, ay + ah], [mid, fusionY - 2]], 's', true))
  })

  const formula = narrow ? ['1 / (60 + lexical rank)', '+ 1 / (60 + dense rank)'] : ['1 / (60 + lexical rank) + 1 / (60 + dense rank)']
  const fh = 68 + formula.length * 16
  out.push(rect(0, fusionY, width, fh, 'em box'))
  out.push(text(12, fusionY + 17, 'Reciprocal rank fusion', 'em'))
  out.push(text(12, fusionY + 32, 'Each provision scores', 'note'))
  formula.forEach((f, i) => out.push(text(12, fusionY + 52 + i * 16, f, 'code')))
  out.push(text(12, fusionY + 52 + formula.length * 16 + 4, 'An arm that did not find it adds nothing.', 'note'))
  y = fusionY + fh
  out.push(line([[cx, y], [cx, y + 14]], 's', true))
  y += 16
  out.push(rect(0, y, width, 40))
  out.push(text(12, y + 17, 'Matches, best first'))
  out.push(text(12, y + 32, 'eid, score, text', 'code'))
  return { out, height: y + 41 }
}

const title =
  'Hybrid search, top to bottom: a query passes the scope (one jurisdiction, the current version of each law, normative provisions only) into two arms. The lexical arm ranks provisions by BM25 on the query’s words; the dense arm, which calls a model to embed the query, ranks them by nearness of embeddings. Each passes on up to 30, ranked. Reciprocal rank fusion scores each provision 1 / (60 + lexical rank) + 1 / (60 + dense rank), with no term from an arm that did not find it, and matches come out best first with eid, score and text.'

for (const [name, width] of [['v11-hybrid-search', 624], ['v11-hybrid-search.narrow', 358]]) {
  const { out, height } = draw(width)
  svg(name, width, height, out, title)
}
