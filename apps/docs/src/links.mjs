import { existsSync, statSync } from 'node:fs'
import { dirname, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { docsDir, repoRoot, repoUrl, sections } from './site.mjs'

const posix = (path) => path.split(sep).join('/')
const isPage = (rel) => /\.mdx?$/.test(rel) && (/^index\.mdx?$/.test(rel) || sections.includes(rel.split('/')[0]))

/** The site route of a file in docs/: `guide/ocr.md` gives `guide/ocr`, `index.mdx` gives ``. */
export const routeOf = (rel) => rel.replace(/\.mdx?$/, '').replace(/(^|\/)index$/, '')

/**
 * Turns a link written for GitHub into one that works on the site: a published page
 * becomes its route, any other file in the repository becomes its GitHub URL.
 */
export function rewriteLink(url, file, base = '/') {
  if (/^([a-z][a-z0-9+.-]*:|\/\/|#|\?)/i.test(url)) return url
  const [, path, tail] = /^([^?#]*)(.*)$/.exec(url)
  const target = path.startsWith('/') ? resolve(repoRoot, `.${decodeURI(path)}`) : resolve(dirname(file), decodeURI(path))
  const inRepo = posix(relative(repoRoot, target))
  if (inRepo.startsWith('..')) return url
  const rel = posix(relative(docsDir, target))
  if (isPage(rel)) {
    const route = routeOf(rel)
    return `${base.replace(/\/$/, '')}/${route}${route ? '/' : ''}${tail}`
  }
  if (!existsSync(target)) return url
  return `${repoUrl}/${statSync(target).isDirectory() ? 'tree' : 'blob'}/main/${inRepo}${tail}`
}

/** Markdown plugin for pages under docs/. */
export const docsLinks = (base) => ({ fileURL }) => {
  if (!fileURL) return
  const file = fileURLToPath(fileURL)
  const rel = posix(relative(docsDir, file))
  if (rel.startsWith('..')) return
  let untitled = false
  const visit = (node, ctx) => {
    const url = rewriteLink(node.url, file, base)
    if (url !== node.url) ctx.setProperty(node, 'url', url)
  }
  return {
    name: 'docs-links',
    before(_root, ctx) {
      const frontmatter = ((ctx.data.astro ??= {}).frontmatter ??= {})
      untitled = !frontmatter.title
      // The links validator keys a page by its frontmatter slug; `//` is its spelling of the home page.
      frontmatter.slug ??= routeOf(rel) || '//'
    },
    heading(node, ctx) {
      // A page with no frontmatter takes its title from its first heading, so drop the repeat.
      if (untitled && node.depth === 1) ctx.removeNode(node)
      untitled = false
    },
    link: visit,
    definition: visit,
  }
}
