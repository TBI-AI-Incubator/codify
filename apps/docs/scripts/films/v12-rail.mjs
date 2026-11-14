// V12, the landing rail's films: each of stages 2 to 5 reads one file of the run and writes its own.
// Every word and image is from the worked example's run (apps/docs/src/assets/journey); the timing is schematic.
// Each film opens and closes on the stage's still, so it hands over to the HTML still without a jump.
// Usage: node apps/docs/scripts/films/v12-rail.mjs [stage ...]; needs playwright-core (PLAYWRIGHT_FROM), Chrome, ffmpeg.
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'

const assets = fileURLToPath(new URL('../../src/assets/journey/', import.meta.url))
const fonts = fileURLToPath(new URL('../../../docs-theme/node_modules/@fontsource/', import.meta.url))
const tokens = fileURLToPath(new URL('../../../docs-theme/styles/tokens.css', import.meta.url))
const out = `${assets}films/`
const { chromium } = createRequire(process.env.PLAYWRIGHT_FROM ?? import.meta.url)('playwright-core')

// The film box is 113x142 CSS px in the wide rail and 142x178 in the narrow one; each size gets its own film,
// with the still's padding, so its 11px text wraps where the still's does.
const sizes = {
  wide: { W: 113, H: 142, pad: 14, scale: 4 },
  narrow: { W: 142, H: 178, pad: 14, scale: 3 },
}
const FPS = 30

const read = (name) => readFileSync(`${assets}${name}`, 'utf8')
const source = read('source.txt').split('\n')
const scaffold = read('scaffold.bluebell')
const akn = read('final.akn.xml')
const validator = read('validator.json')

const dedent = (lines) => {
  const indent = Math.min(...lines.filter((l) => l.trim()).map((l) => l.search(/\S/)))
  return lines.map((l) => l.slice(indent))
}
const at = (lines, prefix) => {
  const i = lines.findIndex((l) => l.trim().startsWith(prefix))
  if (i < 0) throw new Error(`no line starting ${prefix}`)
  return i
}

// The same text the rail's stills show (apps/docs/src/components/JourneyRail.astro).
const ocrLines = source.slice(0, 14)
const scaffoldLines = dedent(scaffold.slice(scaffold.indexOf('BODY')).split('\n'))
const section = /<section eId="part_I__sec_1">[\s\S]*?<\/section>/.exec(akn)[0]
const sectionLines = dedent(`        ${section}`.split('\n'))
const law = {
  title: /<FRBRalias value="([^"]*)"/.exec(akn)[1],
  heading: /<heading>(.*?)<\/heading>/.exec(section)[1],
  subsections: [...section.matchAll(/<num>(.*?)<\/num>\s*<content>\s*<p[^>]*>([\s\S]*?)<\/p>/g)].map(([, num, text]) => [num, text.replace(/<[^>]+>/g, '')]),
}
const part = at(source, 'PART I ')
const sourceFromPart = source.slice(part, part + 12)
// The validator's one finding, and the element it names: part II, which has a number and no heading.
// Opens at its eid, so the value stays whole in the wide rail's crop.
const findingLines = dedent(validator.slice(validator.lastIndexOf('\n', validator.indexOf('"eid"'))).split('\n').filter((l) => l.trim().startsWith('"')))
const aknLines = akn.split('\n')
const partII = at(aknLines, '<part eId="part_II">')
const partLines = dedent(aknLines.slice(partII, partII + 10))

/*
 * A stage is two sheets: `from` (the previous stage's file, in ink-3) and `to` (this stage's still).
 * `light` lines of `from` turn to ink at weight 600, then `moves` carry them onto their `to` line.
 * Line kinds: code (mono, wraps like the still), cut (mono, one line, trimmed with an ellipsis), raw HTML.
 */
const stages = {
  ocr: {
    from: [{ html: `<img src="${pathToFileURL(`${assets}page-001.webp`)}" alt="">`, cls: 'page' }],
    to: ocrLines.map((text) => ({ text, cls: 'code' })),
  },
  structure: {
    from: sourceFromPart.map((text) => ({ text, cls: 'cut' })),
    to: scaffoldLines.map((text) => ({ text, cls: 'code' })),
    light: [at(sourceFromPart, 'PART I '), at(sourceFromPart, 'Short title')],
    moves: {
      [at(sourceFromPart, 'PART I ')]: at(scaffoldLines, 'PART I '),
      [at(sourceFromPart, 'Short title')]: at(scaffoldLines, 'SECTION 1 '),
    },
  },
  validate: {
    // The part line wraps rather than trims, so its eId stays readable.
    from: partLines.map((text, i) => ({ text, cls: i ? 'cut' : 'code' })),
    to: findingLines.map((text) => ({ text, cls: 'code' })),
    light: [0],
    moves: { 0: at(findingLines, '"eid"') },
  },
  publish: {
    from: sectionLines.map((text) => ({ text, cls: 'cut' })),
    to: [
      { html: '', cls: 'law-paper' },
      { text: law.title, cls: 'law-title' },
      { text: law.heading, num: '1', cls: 'law-heading' },
      ...law.subsections.map(([num, text]) => ({ text, num, cls: 'law-sub' })),
    ],
    light: [at(sectionLines, '<heading>')],
    moves: { [at(sectionLines, '<heading>')]: 2 },
  },
}

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
// A trimmed line keeps its nesting as a narrow indent, so the words that name it stay in frame.
const line = ({ text, html, num, cls }, sheet, i) =>
  `<div class="ln ${cls}" data-sheet="${sheet}" data-i="${i}"${
    cls === 'cut' ? ` style="padding-inline-start:${text.search(/\S|$/) * 2}px"` : ''
  }>${
    html ?? (num ? `<span class="n">${esc(num)}</span><span>${esc(text)}</span>` : esc(cls === 'cut' ? text.trim() : text) || '&#8203;')
  }</div>`

const page = (stage, theme, { W, H, pad }) => `<!doctype html>
<html lang="en-GB" data-theme="${theme}"><head><meta charset="utf-8">
<link rel="stylesheet" href="${pathToFileURL(tokens)}">
${['geist-sans/latin-400', 'geist-sans/latin-600', 'geist-mono/latin-400', 'geist-mono/latin-600', 'instrument-serif/latin-400']
  .map((f) => `<link rel="stylesheet" href="${pathToFileURL(`${fonts}${f.split('/')[0]}/${f.split('/')[1]}.css`)}">`)
  .join('\n')}
<style>
html,body{margin:0;background:var(--cd-paper-lo)}
#c{position:relative;width:${W}px;height:${H}px;overflow:hidden;background:var(--cd-paper-lo)}
.sheet{position:absolute;inset:0;padding:${pad}px;font:400 11px/1.6 var(--cd-font-mono);color:var(--cd-ink-2)}
.ln{will-change:transform,opacity}
.code{white-space:pre-wrap;overflow-wrap:anywhere}
.cut{white-space:pre;overflow:hidden;text-overflow:ellipsis;color:var(--cd-ink-3)}
.lit{position:absolute;inset-inline:${pad}px;color:var(--cd-ink);font-weight:600;white-space:pre;overflow:hidden;text-overflow:ellipsis}
.lit.code{white-space:pre-wrap;overflow-wrap:anywhere}
.page{position:absolute;inset:0}
.page img{display:block;width:100%;height:100%;object-fit:cover;object-position:top}
.sheet.law{padding:16px 14px;font:400 12px/1.5 var(--cd-font-text);color:var(--cd-ink)}
.law-paper{position:absolute;inset:0;background:var(--cd-paper)}
.law-title{position:relative;border-bottom:1px solid var(--cd-rule);padding-bottom:12px;font:400 22px/1.1 var(--cd-font-display);letter-spacing:-.02em}
.law-heading,.law-sub{position:relative;display:flex;gap:8px;hyphens:auto}
.law-heading{margin:12px 0 6px;font-weight:600}
.law-sub{margin-top:6px;color:var(--cd-ink-2)}
.n{flex:none;font-variant-numeric:tabular-nums;color:var(--cd-ink-3)}
</style></head><body><div id="c">
<div class="sheet">${stage.from.map((l, i) => line(l, 'from', i)).join('')}</div>
<div class="sheet${stage.to[0].cls === 'law-paper' ? ' law' : ''}">${stage.to.map((l, i) => line(l, 'to', i)).join('')}</div>
</div></body></html>`

// The timeline, in seconds. State at time t is a pure function of t.
const T = {
  outEnd: [0.3, 0.8], // the still floats out
  inFrom: [0.6, 1.1], // the previous stage's file floats in
  light: 1.5, // first lit line; one every 0.35s
}

function setup(stage, T) {
  const ease = (x) => 1 - (1 - Math.min(1, Math.max(0, x))) ** 3
  const ramp = (t, a, b) => ease((t - a) / (b - a))
  const c = document.getElementById('c')
  const from = [...c.querySelectorAll('[data-sheet=from]')]
  const to = [...c.querySelectorAll('[data-sheet=to]')]
  const light = stage.light ?? []
  const moves = stage.moves ?? {}
  // Lit copies sit exactly over their line.
  const lit = Object.fromEntries(
    light.map((i) => {
      const el = from[i]
      const copy = document.createElement('div')
      copy.className = el.classList.contains('code') ? 'lit code' : 'lit'
      copy.textContent = el.textContent
      copy.style.top = `${el.offsetTop}px`
      copy.style.paddingInlineStart = el.style.paddingInlineStart
      el.parentElement.append(copy)
      return [i, copy]
    }),
  )
  const delta = Object.fromEntries(Object.entries(moves).map(([i, j]) => [i, to[j].getBoundingClientRect().top - from[i].getBoundingClientRect().top]))
  const go = T.light + light.length * 0.35 + 0.4 // the transformation starts
  // Lines below the frame never show, so the stagger stops at the last line in view.
  const shown = to.filter((el) => el.getBoundingClientRect().top < c.clientHeight).length
  const toStart = (j) => go + 0.3 + Math.min(j, shown - 1) * 0.07
  const targets = new Set(Object.values(moves))
  const end = Math.max(go + 1.1, toStart(to.length - 1) + 0.5) + 1.2
  const set = (el, opacity, y) => {
    el.style.opacity = opacity
    el.style.transform = `translateY(${y}px)`
  }
  window.render = (t) => {
    from.forEach((el, i) => {
      const inn = ramp(t, ...T.inFrom)
      const moving = i in moves
      const away = moving ? ramp(t, go + 0.55, go + 0.75) : ramp(t, go, go + 0.4)
      const dy = moving ? ramp(t, go, go + 0.8) * delta[i] : -4 * away
      set(el, inn * (1 - away), (1 - inn) * 6 + dy)
      if (lit[i]) {
        const on = ramp(t, T.light + light.indexOf(i) * 0.35, T.light + light.indexOf(i) * 0.35 + 0.3)
        set(lit[i], inn * on * (1 - away), dy)
        el.style.opacity = inn * (1 - on) * (1 - away)
      }
    })
    to.forEach((el, j) => {
      const out = ramp(t, ...T.outEnd)
      const back = targets.has(j) ? ramp(t, go + 0.75, go + 1) : ramp(t, toStart(j), toStart(j) + 0.5)
      const y = targets.has(j) ? 0 : (1 - back) * 6
      set(el, t < T.outEnd[1] ? 1 - out : back, t < T.outEnd[1] ? -4 * out : y)
    })
  }
  return end
}

const browser = await chromium.launch({ channel: 'chrome' })
mkdirSync(out, { recursive: true })
const wanted = process.argv.slice(2).length ? process.argv.slice(2) : Object.keys(stages)
for (const name of wanted) {
  for (const [size, dims] of Object.entries(sizes)) {
    for (const theme of ['light', 'dark']) {
      const id = `v12-${name}-${theme}-${size}`
      const html = `${tmpdir()}/${id}.html`
      writeFileSync(html, page(stages[name], theme, dims))
      const context = await browser.newContext({ viewport: { width: dims.W, height: dims.H }, deviceScaleFactor: dims.scale })
      const tab = await context.newPage()
      await tab.goto(pathToFileURL(html).href, { waitUntil: 'load' })
      await tab.evaluate(() => document.fonts.ready)
      const seconds = await tab.evaluate(`(${setup})(${JSON.stringify({ light: stages[name].light, moves: stages[name].moves })}, ${JSON.stringify(T)})`)
      const file = `${out}${id}.mp4`
      const ff = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-i', '-', '-c:v', 'libx264', '-preset', 'veryslow', '-crf', '26', '-tune', 'animation', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-an', file], { stdio: ['pipe', 'inherit', 'inherit'] })
      for (let f = 0; f <= Math.round(seconds * FPS); f++) {
        await tab.evaluate((t) => window.render(t), f / FPS)
        if (!ff.stdin.write(await tab.locator('#c').screenshot({ type: 'png' }))) await once(ff.stdin, 'drain')
      }
      ff.stdin.end()
      await once(ff, 'close')
      console.log(`${id}.mp4 ${seconds.toFixed(1)}s ${(statSync(file).size / 1024).toFixed(0)} KB`)
      await context.close()
    }
  }
}
await browser.close()
