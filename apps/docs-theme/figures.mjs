import { existsSync, readFileSync } from 'node:fs'
import { basename, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const escape = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

/** A figure SVG ready to inline: no XML prolog or standalone style, ids prefixed so two copies never clash. */
export function inlineSvg(path, prefix) {
  return readFileSync(path, 'utf8')
    .replace(/<\?xml[^>]*>\s*/, '')
    .replace(/<style[\s\S]*?<\/style>\s*/g, '')
    .replace(/\bid="([^"]+)"/g, `id="${prefix}-$1"`)
    .replace(/url\(#([^)]+)\)/g, `url(#${prefix}-$1)`)
    .replace(/href="#([^"]+)"/g, `href="#${prefix}-$1"`)
    .replace(/<svg\b([^>]*?)\s(?:role|aria-label)="[^"]*"/g, '<svg$1')
    .replace(/<svg\b([^>]*?)\s(?:role|aria-label)="[^"]*"/g, '<svg$1')
    .replace(/<svg\b/, '<svg aria-hidden="true" focusable="false"')
}

/** The site markup for `![description](figures/<id>.svg "caption")`; `<id>.narrow.svg`, when present, replaces it in a column narrower than 624px. */
export function figureHtml(svgPath, description, caption) {
  const id = basename(svgPath, '.svg')
  const narrowPath = svgPath.replace(/\.svg$/, '.narrow.svg')
  const narrow = existsSync(narrowPath)
  const art = [
    `<div class="cd-figure__art${narrow ? ' cd-figure__art--wide' : ''}">${inlineSvg(svgPath, `${id}-w`)}</div>`,
    narrow ? `<div class="cd-figure__art cd-figure__art--narrow" data-pagefind-ignore>${inlineSvg(narrowPath, `${id}-n`)}</div>` : '',
  ].join('')
  return [
    `<figure class="cd-figure" id="figure-${id}" data-figure="${id}" aria-labelledby="figure-${id}-caption">`,
    art,
    `<p class="cd-figure__alt">${escape(description)}</p>`,
    `<figcaption id="figure-${id}-caption">${escape(caption)}</figcaption>`,
    '</figure>',
  ].join('')
}

const isFigure = (url) => /(^|\/)figures\/[^/]+\.svg$/.test(url)

/** Markdown plugin: a paragraph holding only a figure image becomes an inline, themed figure with its caption. */
export const figures = () => ({ fileURL }) => {
  if (!fileURL) return
  const file = fileURLToPath(fileURL)
  return {
    name: 'figures',
    paragraph(node, ctx) {
      const kids = node.children.filter((c) => !(c.type === 'text' && !c.value.trim()))
      const img = kids.length === 1 && kids[0].type === 'image' ? kids[0] : null
      if (!img || !isFigure(img.url)) return
      if (!img.alt || !img.title) throw new Error(`${file}: figure ${img.url} needs a description (alt) and a caption (title)`)
      const svgPath = resolve(dirname(file), decodeURI(img.url))
      if (!existsSync(svgPath)) throw new Error(`${file}: figure ${img.url} not found`)
      ctx.replaceNode(node, { type: 'html', value: figureHtml(svgPath, img.alt, img.title) })
    },
  }
}
