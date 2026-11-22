import assert from 'node:assert/strict'
import { test } from 'node:test'
import { dropStyles } from '@codify/docs-theme/figures'
import { esc } from '../scripts/figures/kit.mjs'

test('every style element is dropped from an inlined figure', () => {
  assert.equal(dropStyles('<svg><style>a{}</style>\n<style>b{}</style><rect/></svg>'), '<svg><rect/></svg>')
  assert.equal(dropStyles('<svg><sty<style></style>le>a{}</style></svg>'), '<svg></svg>')
})

test('a figure carrying a script, a leftover style or an event handler is rejected', () => {
  for (const svg of ['<svg><script>x()</script></svg>', '<svg><style>a{}</svg>','<svg><rect onclick="x()"/></svg>']) {
    assert.throws(() => dropStyles(svg))
  }
  assert.equal(dropStyles('<svg><text>one = two</text></svg>'), '<svg><text>one = two</text></svg>')
})

test('figure text is escaped for both element content and attributes', () => {
  assert.equal(esc(`a & <b> "c" 'd'`), 'a &amp; &lt;b&gt; &quot;c&quot; &#39;d&#39;')
})

test('a pathological figure is checked in linear time', () => {
  const t = performance.now()
  assert.equal(dropStyles('<'.repeat(200_000) + ' x'), '<'.repeat(200_000) + ' x')
  assert.throws(() => dropStyles('<'.repeat(200_000) + ' onx=1'))
  dropStyles('<svg>' + '<style></style>'.repeat(50_000) + '</svg>')
  assert.ok(performance.now() - t < 1000)
})
