const ICONS = {
  Available: ['var(--sl-color-green)', '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>'],
  'Library only': ['var(--sl-color-blue)', '<path d="m16 18 6-6-6-6"/><path d="m8 6-6 6 6 6"/>'],
  'Not in the core': ['var(--sl-color-gray-3)', '<circle cx="12" cy="12" r="10"/><path d="M8 12h8"/>'],
}

/** A bold status label with its icon in front; the words stay, so the icon is never the only signal. */
export function statusHtml(label) {
  const hit = Object.hasOwn(ICONS, label) ? ICONS[label] : null
  if (!hit) return null
  const [colour, paths] = hit
  // Inline, not flex: the label must share the surrounding text's baseline.
  return `<strong style="white-space:nowrap"><svg aria-hidden="true" width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="${colour}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:inline;vertical-align:-0.15em;margin-inline-end:0.3em">${paths}</svg>${label}</strong>`
}

/** Markdown plugin: `**Available**`, `**Library only**` and `**Not in the core**` gain their icon on every page. */
export const statusIcons = () => () => ({
  name: 'status-icons',
  strong(node, ctx) {
    const only = node.children.length === 1 ? node.children[0] : null
    const html = only?.type === 'text' ? statusHtml(only.value) : null
    if (html) ctx.replaceNode(node, { type: 'html', value: html })
  },
})
