// V15, a run's life in the HTTP server. Limits from codify/serve/runs.py (RunTable) and uploads from codify/serve/app.py.
// Writes v15-run-lifecycle.svg (wide) and v15-run-lifecycle.narrow.svg.
import { line, rect, svg, text } from './kit.mjs'

const box = (x, y, w, h, label, notes, cls = 's box', labelCls = '') => [
  rect(x, y, w, h, cls),
  text(x + 12, y + 17, label, labelCls),
  ...notes.map((n, i) => (Array.isArray(n) ? text(x + 12, y + 32 + i * 16, ...n) : text(x + 12, y + 32 + i * 16, n, 'note'))),
]
const queued = ['up to 100 waiting', 'the next gets 429']
const finished = ['succeeded, failed, cancelled', 'the last 200 stay readable']
const uploads = [['codify-uploads-…', 'code']]
const header = 'server process · lost on restart'

function wide() {
  const W = 624
  const o = []
  o.push(...box(12, 0, 160, 40, 'Upload', [['POST /runs/ingest', 'code']]))
  o.push(rect(0, 64, W, 180, 's2 dash'))
  o.push(text(W - 12, 84, header, 'micro', 'end'))
  o.push(...box(12, 116, 160, 56, 'Queued', queued))
  o.push(...box(188, 116, 160, 56, 'Running', ['2 at once']))
  o.push(...box(364, 116, 244, 56, 'Finished', finished))
  o.push(...box(364, 188, 244, 40, 'Uploaded files', uploads, 's2 box-lo'))
  o.push(line([[40, 40], [40, 114]], 's', true))
  o.push(line([[172, 144], [186, 144]], 's', true))
  o.push(line([[348, 144], [362, 144]], 's', true))
  // Retry: a new queued run with the same file.
  o.push(line([[486, 116], [486, 104], [92, 104], [92, 114]], 's2', 'arrow2'))
  o.push(text(200, 96, 'retry: a new run', 'note'))
  // The run writes its law before it finishes.
  o.push(line([[268, 172], [268, 266]], 's', true))
  o.push(text(276, 200, 'stored', 'code'), text(276, 216, 'writes the law', 'note'))
  o.push(...box(188, 268, 160, 40, 'PostgreSQL', ['kept across restarts'], 'em box', 'em'))
  return { o, W, H: 309 }
}

function narrow() {
  const W = 358
  const o = []
  o.push(...box(36, 0, 200, 40, 'Upload', [['POST /runs/ingest', 'code']]))
  o.push(rect(0, 64, W, 292, 's2 dash'))
  o.push(text(W - 12, 84, header, 'micro', 'end'))
  o.push(...box(36, 100, 200, 56, 'Queued', queued))
  o.push(...box(36, 172, 200, 40, 'Running', ['2 at once']))
  o.push(...box(36, 228, 200, 56, 'Finished', finished))
  o.push(...box(36, 300, 200, 40, 'Uploaded files', uploads, 's2 box-lo'))
  o.push(line([[60, 40], [60, 98]], 's', true))
  o.push(line([[136, 156], [136, 170]], 's', true))
  o.push(line([[136, 212], [136, 226]], 's', true))
  o.push(line([[236, 256], [252, 256], [252, 128], [238, 128]], 's2', 'arrow2'))
  o.push(text(260, 188, 'retry:', 'note'), text(260, 204, 'a new run', 'note'))
  o.push(line([[36, 192], [20, 192], [20, 400], [34, 400]], 's', true))
  o.push(text(28, 372, 'stored', 'code'), text(84, 372, 'writes the law', 'note'))
  o.push(...box(36, 380, 200, 40, 'PostgreSQL', ['kept across restarts'], 'em box', 'em'))
  return { o, W, H: 421 }
}

const title =
  'A run in codify serve: an upload to POST /runs/ingest is queued (up to 100; the next gets 429), runs two at once, and finishes as succeeded, failed or cancelled, with the last 200 kept; retry makes a new run. Runs and uploaded files live only in the server process and are lost on restart; the law a run stored stays in PostgreSQL.'

for (const [name, f] of [['v15-run-lifecycle', wide], ['v15-run-lifecycle.narrow', narrow]]) {
  const { o, W, H } = f()
  svg(name, W, H, o, title)
}
