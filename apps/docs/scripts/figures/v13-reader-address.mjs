// V13, a search match's reader address in three parts, above the reader at that provision.
// The captures are apps/web at 856px and 390px wide, opened at that address against a database holding the worked example,
// with the section mark hidden so the flash bar does not half-cover it.
// Writes v13-reader-address.svg (wide) and v13-reader-address.narrow.svg; the captures are embedded, since inlined SVG cannot resolve a relative image.
import { readFileSync } from 'node:fs'
import { line, rect, svg, text } from './kit.mjs'

const parts = [
  { code: '/laws/21278932-9b16-4320-b50a-c832f3c61806', note: 'The law, by its id in this database' },
  { code: '?v=20cb9d70-142f-47fc-993b-98d884a79ad8', note: 'The version; left out, the newest' },
  { code: '#part_IV__sec_12', note: 'The provision: scrolls to it, marks it briefly', em: true },
]

const png = (f) => `data:image/png;base64,${readFileSync(new URL(f, import.meta.url)).toString('base64')}`
const marker = (cx, cy, n) => [`<circle cx="${cx}" cy="${cy}" r="8" class="marker"/>`, text(cx, cy, n, 'marker-n')]

function draw({ width, narrow }) {
  const out = [text(0, 12, 'Address of a search match', 'micro')]
  let y = 40
  parts.forEach((p, i) => {
    out.push(...marker(8, y - 4, String(i + 1)))
    out.push(text(24, y, p.code, p.em ? 'code em' : 'code'))
    if (narrow) {
      out.push(text(24, y + 18, p.note, 'note'))
      y += 44
    } else {
      out.push(text(356, y, p.note, 'note'))
      y += 28
    }
  })
  y += 8
  out.push(text(0, y, narrow ? 'The reader, 390px wide' : 'The reader', 'micro'))
  y += 12
  // Capture sizes in CSS pixels (the files are at 2x).
  const [w, h, f] = narrow ? [320, 248, 'v13-reader-sec12.narrow.png'] : [576, 183, 'v13-reader-sec12.png']
  const x = width - w - 1
  out.push(`<image href="${png(f)}" x="${x}" y="${y}" width="${w}" height="${h}" class="no-mirror" preserveAspectRatio="xMinYMin meet"/>`)
  out.push(rect(x - 0.5, y - 0.5, w + 1, h + 1, 's2'))
  // Marker 3 beside the bar the reader draws on the target.
  out.push(...marker(8, y + 22, '3'))
  out.push(line([[17, y + 22], [x - 4, y + 22]], 's2 dash'))
  return { body: out, height: y + h + 4 }
}

const label =
  'A search match opens the reader at /laws/ plus the law id, ?v= plus the version id, and # plus the eId part_IV__sec_12; the reader shows section 12, Right of access, marked.'
for (const [name, opts] of [
  ['v13-reader-address', { width: 624, narrow: false }],
  ['v13-reader-address.narrow', { width: 358, narrow: true }],
]) {
  const { body, height } = draw(opts)
  svg(name, opts.width, height, body, label)
}
