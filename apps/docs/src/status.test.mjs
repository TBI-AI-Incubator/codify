import assert from 'node:assert/strict'
import { test } from 'node:test'
import { statusHtml } from './status.mjs'

test('each status label keeps its words and gains a hidden icon', () => {
  for (const label of ['Available', 'Library only', 'Not in the core']) {
    const html = statusHtml(label)
    assert.match(html, new RegExp(`aria-hidden="true".*</svg>${label}</strong>$`))
  }
})

test('other bold text is left alone', () => {
  assert.equal(statusHtml('Available soon'), null)
  assert.equal(statusHtml('available'), null)
})

test('a label naming an inherited property is left alone', () => {
  for (const label of ['constructor', '__proto__', 'toString']) assert.equal(statusHtml(label), null)
})
