import { existsSync, readdirSync } from 'node:fs'
import { docsDir } from './site.mjs'

// Reading order. A page that is not on disk is skipped; one that is not listed goes last.
const order = {
  start: ['overview', 'quickstart', 'install', 'first-ingest'],
  guide: [
    'model-your-legal-system',
    'write-a-jurisdiction-config',
    'acquire-sources',
    'ocr',
    'structure',
    'validate-and-repair',
    'amendments-and-versions',
    'translate',
    'search',
    'serve-and-publish',
    'interoperate',
  ],
  concepts: ['architecture', 'akoma-ntoso', 'frbr', 'bluebell', 'translation', 'akn4eu-divergences'],
  reference: ['cli', 'http-api', 'mcp-tools', 'python-api', 'jurisdiction-config', 'environment', 'bundle', 'validation-checks'],
  operate: ['deployment', 'database', 'models-and-costs'],
  'open-core': ['what-is-open', 'platform'],
  project: ['contributing', 'testing', 'versioning', 'security', 'decisions'],
}

const labels = {
  start: 'Start',
  guide: 'Guide',
  concepts: 'Concepts',
  reference: 'Reference',
  operate: 'Operate',
  'open-core': 'Open core',
  project: 'Project',
}

function pages(dir, first = []) {
  if (!existsSync(docsDir + dir)) return []
  const found = readdirSync(docsDir + dir)
    .filter((name) => /^[^_].*\.mdx?$/.test(name))
    .map((name) => name.replace(/\.mdx?$/, ''))
  const rest = found.filter((page) => !first.includes(page)).sort()
  return [...first.filter((page) => found.includes(page)), ...rest].map((page) => (page === 'index' ? dir : `${dir}/${page}`))
}

// The decision records are reached from their index page, project/decisions.
export const sidebar = Object.entries(order)
  .map(([dir, first]) => ({ label: labels[dir], items: pages(dir, first) }))
  .filter((group) => group.items.length)
