// Helpers for figure generators. Output goes to docs/figures/; the site restyles it from the theme's figures.css.
import { writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

export const figuresDir = fileURLToPath(new URL('../../../../docs/figures/', import.meta.url))

export const esc = (s) =>
  String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;')

// Used only where the SVG is opened on its own (GitHub, a file viewer); the site strips it.
export const standaloneStyle = `<style>
svg{color:#1d1d1d;font-family:'Geist Sans',ui-sans-serif,system-ui,sans-serif;fill:none;stroke:none}
.s{stroke:currentColor;stroke-width:1.5}.s2{stroke:#6b6b6b;stroke-width:1}.dash{stroke-dasharray:3 3}:not(text).em{stroke:currentColor;stroke-width:2.5}
.box{fill:#fff}.box-lo{fill:#f5f5f5}.ink{fill:currentColor}
text{fill:currentColor;font-size:13px;font-weight:500;letter-spacing:-0.01em}
text.note{fill:#3e3e3e;font-size:12px;font-weight:400;letter-spacing:0}
text.code{fill:#3e3e3e;font-family:'Geist Mono',ui-monospace,monospace;font-size:12px;font-weight:400;letter-spacing:0}
text.micro{fill:#3e3e3e;font-family:'Geist Mono',ui-monospace,monospace;font-size:11px;font-weight:400;letter-spacing:.06em;text-transform:uppercase}
text.em{font-weight:600}
@media (prefers-color-scheme:dark){svg{color:#f3f4ef}.s2{stroke:#a5afa8}.box{fill:#131615}.box-lo{fill:#1c211f}text.note,text.code,text.micro{fill:#c7cdc9}}
</style>`

/** Open arrowhead, about 6px, drawn in the stroke colour. */
export const arrowDefs = `<defs><marker id="arrow" viewBox="0 0 9 9" refX="8" refY="4.5" markerWidth="6" markerHeight="6" orient="auto-start-reverse" markerUnits="userSpaceOnUse"><path d="M1 1 8 4.5 1 8" class="s" stroke-linejoin="round" stroke-linecap="round"/></marker><marker id="arrow2" viewBox="0 0 9 9" refX="8" refY="4.5" markerWidth="6" markerHeight="6" orient="auto-start-reverse" markerUnits="userSpaceOnUse"><path d="M1 1 8 4.5 1 8" class="s2" stroke-linejoin="round" stroke-linecap="round"/></marker></defs>`

export const text = (x, y, s, cls = '', anchor = 'start') =>
  `<text x="${x}" y="${y}"${cls ? ` class="${cls}"` : ''}${anchor !== 'start' ? ` text-anchor="${anchor}"` : ''}>${esc(s)}</text>`

export const rect = (x, y, w, h, cls = 's box') => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="3" class="${cls}"/>`

/** A polyline; `arrow` is true for an ink arrowhead or 'arrow2' for a secondary one. */
export const line = (points, cls = 's', arrow = false) =>
  `<path d="M${points.map((p) => p.join(' ')).join(' L')}" class="${cls}"${arrow ? ` marker-end="url(#${arrow === true ? 'arrow' : arrow})"` : ''}/>`

/** The model-call mark: a filled 7px square. */
export const modelMark = (x, y) => `<rect x="${x}" y="${y}" width="7" height="7" class="ink"/>`

export function svg(name, width, height, body, title) {
  const out = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(title)}">
${standaloneStyle}
${arrowDefs}
${body.join('\n')}
</svg>
`
  writeFileSync(`${figuresDir}${name}.svg`, out)
  return out
}
