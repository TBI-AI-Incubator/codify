// V8: where each part of the worked example's work URI and one eId comes from. Values from the bundle's final.akn.xml,
// manifest.json and anchors.jsonl, and data/jurisdictions/xa/config.json; see the figure kit. One 358px cut.
import { line, modelMark, rect, svg, text } from './kit.mjs'

const W = 358
const CH = 7.2 // Geist Mono advance at 12px; textLength holds the string to it whatever font renders
const X0 = 12

const blocks = [
  {
    head: 'Work URI',
    aside: 'from final.akn.xml',
    id: '/akn/xa/act/1992/7',
    parts: [
      { from: 0, to: 12, lines: [['frbr.uri_patterns.act', 'code'], ['/akn/xa/act/{year}/{number}', 'code']] },
      { from: 12, to: 18, model: true, lines: [['Read from the text', ''], ['metadata: year 1992, number 7', 'note']] },
    ],
  },
  {
    head: 'eId',
    headCls: 'code',
    aside: 'levels joined by __',
    id: 'part_I__sec_1__point_1',
    parts: [
      { from: 0, to: 6, lines: [['akn_element: "part"', 'code'], ['I as printed in PART I, found by rules', 'note']] },
      { from: 8, to: 13, lines: [['akn_element: "section"', 'code'], ['prefix sec; 1 as printed, found by rules', 'note']] },
      { from: 15, to: 22, model: true, lines: [['Read from the filled text', ''], ['each “(1)” line becomes a point,', 'note'], ['not the Subsection level', 'note']] },
    ],
  },
]

const out = [modelMark(0, 7), text(14, 14, 'calls a model', 'note')]
let y = 40
for (const b of blocks) {
  out.push(text(0, y, b.head, b.headCls), text(W, y, b.aside, 'note', 'end'))
  const plate = y + 8
  out.push(rect(0, plate, W, 32, 's2 box-lo'))
  out.push(
    `<text x="${X0}" y="${plate + 20}" class="code" textLength="${(b.id.length * CH).toFixed(1)}" lengthAdjust="spacing">${b.id}</text>`,
  )
  // Rows run rightmost part first, so no leader crosses a label.
  let ry = plate + 32 + 28
  for (const p of [...b.parts].reverse()) {
    const x1 = X0 + p.from * CH
    const x2 = X0 + p.to * CH
    const mid = Math.round((x1 + x2) / 2)
    const by = plate + 36
    out.push(line([[x1 + 2, by - 3], [x1 + 2, by], [x2 - 2, by], [x2 - 2, by - 3]], 's2'))
    out.push(line([[mid, by], [mid, ry - 4], [mid + 8, ry - 4]], 's2'))
    let tx = mid + 12
    if (p.model) {
      out.push(modelMark(tx, ry - 8))
      tx += 12
    }
    p.lines.forEach(([s, cls], i) => out.push(text(tx, ry + i * 16, s, cls)))
    ry += p.lines.length * 16 + 16
  }
  y = ry + 12
}

svg(
  'v8-identifier-sources',
  W,
  y - 40,
  out,
  'Where each part of two identifiers comes from. Work URI /akn/xa/act/1992/7: /akn/xa/act/ is the fixed text of the config pattern frbr.uri_patterns.act, /akn/xa/act/{year}/{number}, the pattern of the default class act; 1992/7 fills its year and number slots, which a model read from the text. eId part_I__sec_1__point_1: part from the hierarchy level with akn_element part, I as printed; sec from akn_element section, 1 as printed, both found by rules; point_1 from the filled text, where each (1) line becomes a point, not the Subsection level.',
)
