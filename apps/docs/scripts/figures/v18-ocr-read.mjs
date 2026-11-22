// V18, the six read stages of a PDF and the worked example's path through them.
// Stages from codify/pipeline/enrich/ocr.py (_read_pages); outcomes from bundle-xa's manifest, events and log.
// Writes v18-ocr-read.svg (wide) and v18-ocr-read.narrow.svg.
import { line, modelMark, rect, svg, text } from './kit.mjs'

const stages = [
  { n: '1', label: 'Whole-document engine', note: 'Mistral OCR, 25-page chunks', opt: true, model: true, run: ['not asked for', ['ocr_model: null', 'code']] },
  { n: '2', label: 'Layout pass', note: 'regions and a rival read', opt: true, model: true, run: ['failed, no second engine', ['layout_pass_failed', 'code']], nrun: ['failed', ['layout_pass_failed', 'code']] },
  { n: '3', label: 'Text-layer gate', note: 'a sound layer is kept', run: ['5 of 5 pages diverted', ['0 kept', 'note']], nrun: ['5 of 5 diverted', ['0 kept', 'note']] },
  { n: '4', label: 'Vision read', note: 'one page image per request', model: true, run: ['5 pages read', ['965 to 2,704 characters', 'note']], nrun: ['5 pages read', ['965–2,704 characters', 'note']] },
  { n: '5', label: 'Backstop', note: 'an empty read takes the rival', opt: true, run: ['not needed', ['no empty read', 'note']] },
  { n: '6', label: 'Combine', note: 'page texts joined in order', run: [['source.txt', 'code'], ['10,990 characters', 'note']] },
]

function draw(W, boxW, runX, railX, outerX, narrow) {
  const o = []
  const top = 28
  const pitch = 64
  const y = (i) => top + i * pitch
  o.push(text(0, 12, 'Stage', 'micro'), text(runX, 12, 'This run', 'micro'))
  stages.forEach((s, i) => {
    const cls = s.opt ? 's2 dash box-lo' : 's box'
    o.push(rect(0, y(i), boxW, 40, cls))
    o.push(text(12, y(i) + 17, `${s.n}. ${s.label}`), text(12, y(i) + 32, s.note, 'note'))
    if (s.model) o.push(modelMark(boxW - 19, y(i) + 12))
    const run = narrow && s.nrun ? s.nrun : s.run
    run.forEach((r, j) => o.push(Array.isArray(r) ? text(runX, y(i) + 17 + j * 15, ...r) : text(runX, y(i) + 17 + j * 15, r)))
    if (i < stages.length - 1 && i !== 2) o.push(line([[40, y(i) + 40], [40, y(i + 1) - 2]], 's', true))
  })
  // The lit path: every page failed the gate as too_short and went to the vision read.
  o.push(line([[40, y(2) + 40], [40, y(3) - 2]], 'em', true))
  o.push(text(52, y(2) + 53, 'too_short', 'code'))
  // A complete whole-document read ends the read; a kept layer skips the vision read and the backstop.
  o.push(line([[boxW, y(0) + 20], [outerX, y(0) + 20], [outerX, y(5) + 28], [boxW + 2, y(5) + 28]], 's2', 'arrow2'))
  o.push(text(outerX - 6, y(0) + 53, 'all pages read', 'note', 'end'))
  o.push(line([[boxW, y(2) + 20], [railX, y(2) + 20], [railX, y(5) + 12], [boxW + 2, y(5) + 12]], 's2', 'arrow2'))
  o.push(text(railX - 6, y(2) + 53, 'kept', 'note', 'end'))
  o.push(modelMark(0, y(6) + 4), text(15, y(6) + 11, 'calls a model', 'note'))
  o.push(rect(116, y(6) + 4, 20, 8, 's2 dash box-lo'), text(144, y(6) + 11, 'needs the second engine', 'note'))
  return { o, W, H: y(6) + 16 }
}

const title =
  'How Codify reads a PDF, in six stages, with the worked example\'s path. Dashed stages need the second engine. 1, whole-document engine (Mistral OCR in 25-page chunks): not asked for, ocr_model null; when it reads every page, the read ends there and stages 2 to 5 are skipped. 2, layout pass for regions and a rival read: failed with layout_pass_failed, no second engine. 3, text-layer gate, which keeps a sound layer: 5 of 5 pages diverted as too_short, 0 kept. 4, vision read, one page image per chat-model request: 5 pages read, 965 to 2,704 characters each. 5, backstop, which runs on every page and gives a page with an empty read the rival read: not needed, no empty read. 6, combine, page texts joined in order: source.txt, 10,990 characters. A kept text layer skips stages 4 and 5. Stages 1, 2 and 4 call a model.'

for (const [name, args] of [['v18-ocr-read', [624, 300, 380, 316, 332]], ['v18-ocr-read.narrow', [358, 196, 220, 204, 212, true]]]) {
  const { o, W, H } = draw(...args)
  svg(name, W, H, o, title)
}
