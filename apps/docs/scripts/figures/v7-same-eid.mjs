// V7, one eId across two dates (v7-amend) and two languages (v7-translate), each with a narrow cut.
// Text from bundle final.akn.xml and the guides' consolidated-2012.akn.xml and translated-fra.akn.xml.
import { line, rect, svg, text } from './kit.mjs'

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

const instances = [
  {
    id: 'v7-amend',
    heading: 'Over time: an amendment',
    eid: 'part_I__sec_5__point_1',
    cards: [
      {
        label: 'As enacted',
        uri: '/akn/xa/act/1992/7/eng@1992-05-02',
        body: '(1) The Publishing Authority shall maintain the official text of every law in a state that is current to within thirty days of the coming into force of any amending instrument affecting the law.',
      },
      {
        label: 'As amended from 1 January 2012',
        uri: '/akn/xa/act/1992/7/eng@2012-01-01',
        body: '(1) The Publishing Authority shall maintain the official text of every law in a state that is current to within *seven days* of the coming into force of any amending instrument affecting the law.',
      },
    ],
    recordedIn: 'Recorded in the 2012 text',
    code: {
      wide: [
        '<textualMod eId="pmod_1" type="substitution">',
        '  <source href="/akn/xa/act/2011/5"/>',
        '  <destination href="/akn/xa/act/1992/7/!main#part_I__sec_5__point_1"/>',
        '  <force period="#period_2012"/>',
        '</textualMod>',
      ],
      narrow: [
        '<textualMod eId="pmod_1" type="substitution">',
        '  <source href="/akn/xa/act/2011/5"/>',
        '  <destination href="…#part_I__sec_5__point_1"/>',
        '  <force period="#period_2012"/>',
        '</textualMod>',
      ],
    },
  },
  {
    id: 'v7-translate',
    heading: 'Across languages: a translation',
    eid: 'part_I__sec_1',
    cards: [
      {
        label: 'English source',
        uri: '/akn/xa/act/1992/7/eng@2012-01-01',
        title: '1 Short title and commencement',
        body: '(1) This Act may be cited as the Legislation Act, 1992.',
      },
      {
        label: 'French, not authoritative',
        uri: '/akn/xa/act/1992/7/fra@2012-01-01',
        title: '1 Titre abrégé et entrée en vigueur',
        body: '(1) La présente loi peut être citée sous le titre : Legislation Act, 1992.',
      },
    ],
    recordedIn: 'Recorded in the French text',
    code: {
      wide: [
        '<FRBRtranslation fromLanguage="eng"',
        '  href="/akn/xa/act/1992/7/eng@2012-01-01"',
        '  by="#codify" authoritative="false"/>',
      ],
    },
  },
]

// Greedy wrap on an estimated advance; *words* stay together and are set at 600.
function wrap(s, max) {
  // A French colon never starts a line.
  const tokens = s.match(/\*[^*]+\*|\S+ :|\S+/g)
  const lines = [[]]
  let len = 0
  for (const t of tokens) {
    const n = t.replace(/\*/g, '').length
    if (len && len + 1 + n > max) lines.push([]), (len = 0)
    lines.at(-1).push(t)
    len += (len ? 1 : 0) + n
  }
  return lines.map((ws) => ws.join(' '))
}
const richLine = (x, y, s) =>
  `<text x="${x}" y="${y}" class="note">${s
    .split(/(\*[^*]+\*)/)
    .map((p) => (p.startsWith('*') ? `<tspan font-weight="600">${esc(p.slice(1, -1))}</tspan>` : esc(p)))
    .join('')}</text>`

const cardHeight = (c, max) => 56 + (c.title ? 20 : 0) + wrap(c.body, max).length * 16

function card(c, x, y, w, h, max) {
  const out = [rect(x, y, w, h)]
  out.push(text(x + 12, y + 20, c.label))
  out.push(text(x + 12, y + 36, c.uri, 'code'))
  let ty = y + 60
  if (c.title) out.push(text(x + 12, ty, c.title)), (ty += 20)
  wrap(c.body, max).forEach((l, i) => out.push(richLine(x + 12, ty + i * 16, l)))
  return out
}

function draw(inst, width) {
  const wide = width >= 600
  const cw = wide ? 296 : width
  const max = wide ? 42 : 46
  const out = []
  let y = 0
  {
    out.push(text(0, y + 11, inst.heading, 'micro'))
    // The eId, with a bracket down into both texts it names.
    const xs = wide ? [cw / 2, width - cw / 2] : [width / 2]
    const mid = width / 2
    out.push(wide ? text(mid, y + 40, inst.eid, 'code', 'middle') : text(0, y + 40, inst.eid, 'code'))
    const h = Math.max(...inst.cards.map((c) => cardHeight(c, max)))
    const top = y + 68
    if (wide) {
      out.push(line([[mid, y + 46], [mid, y + 52]], 's2'))
      out.push(line([[xs[0], y + 52], [xs[1], y + 52]], 's2'))
      for (const x of xs) out.push(line([[x, y + 52], [x, top - 2]], 's2', 'arrow2'))
      out.push(...card(inst.cards[0], 0, top, cw, h, max), ...card(inst.cards[1], width - cw, top, cw, h, max))
      out.push(line([[cw, top + h / 2], [width - cw - 2, top + h / 2]], 's', true))
      y = top + h
    } else {
      // Stacked: the eId's rail runs down the inline-start edge, with a tick into each text.
      const cx = 20
      const h1 = cardHeight(inst.cards[0], max)
      const h2 = cardHeight(inst.cards[1], max)
      const top2 = top + h1 + 32
      out.push(line([[8, y + 46], [8, top2 + 20]], 's2'))
      for (const t of [top, top2]) out.push(line([[8, t + 20], [cx - 2, t + 20]], 's2', 'arrow2'))
      out.push(...card(inst.cards[0], cx, top, cw - cx, h1, max))
      out.push(line([[cx + (cw - cx) / 2, top + h1], [cx + (cw - cx) / 2, top2 - 2]], 's', true))
      out.push(...card(inst.cards[1], cx, top2, cw - cx, h2, max))
      y = top2 + h2
    }
    out.push(text(0, y + 28, inst.recordedIn, 'note'))
    const code = (wide ? inst.code.wide : inst.code.narrow ?? inst.code.wide)
    // Indent by position, not spaces: Geist Mono advances 0.6em (7.2px at 12px).
    // One <text> so right to left flips the block as a whole and keeps its indentation.
    out.push(
      `<text class="code">${code
        .map((l, i) => `<tspan x="${l.match(/^ */)[0].length * 7.2}" y="${y + 48 + i * 16}">${esc(l.trimStart())}</tspan>`)
        .join('')}</text>`,
    )
    y += 48 + (code.length - 1) * 16 + 40
  }
  return { out, height: y - 40 + 4 }
}

const titles = {
  'v7-amend':
    'One eId, part_I__sec_5__point_1, names subsection 5(1) of the Legislation Act, 1992 as enacted (thirty days) and as amended from 1 January 2012 (seven days); the 2012 text records the change in a textualMod from /akn/xa/act/2011/5.',
  'v7-translate':
    'One eId, part_I__sec_1, names section 1 of the Legislation Act, 1992 in English and in a French text marked as not authoritative; the French was written by hand for the example.',
}

for (const inst of instances)
  for (const [suffix, width] of [
    ['', 624],
    ['.narrow', 358],
  ]) {
    const { out, height } = draw(inst, width)
    svg(inst.id + suffix, width, height, out, titles[inst.id])
  }
