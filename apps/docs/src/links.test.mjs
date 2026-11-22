import assert from 'node:assert/strict'
import { test } from 'node:test'
import { rewriteLink } from './links.mjs'
import { docsDir, repoUrl } from './site.mjs'

const page = `${docsDir}guide/ocr.md`

test('a link to a published page becomes its route', () => {
  assert.equal(rewriteLink('../concepts/frbr.md#work', page), '/concepts/frbr/#work')
  assert.equal(rewriteLink('structure.md', page, '/docs/'), '/docs/guide/structure/')
  assert.equal(rewriteLink('../index.mdx', page), '/')
})

test('a link to another repository file becomes its GitHub address', () => {
  assert.equal(rewriteLink('../../pyproject.toml', page), `${repoUrl}/blob/main/pyproject.toml`)
  assert.equal(rewriteLink('/codify', page), `${repoUrl}/tree/main/codify`)
})

test('external links, anchors and missing files are left alone', () => {
  for (const url of ['https://example.org/a.md', '#steps', 'mailto:a@example.org', '../../no-such-file.txt']) {
    assert.equal(rewriteLink(url, page), url)
  }
})
