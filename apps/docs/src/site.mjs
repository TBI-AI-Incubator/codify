import { fileURLToPath } from 'node:url'

export const repoRoot = fileURLToPath(new URL('../../../', import.meta.url))
export const docsDir = `${repoRoot}docs/`
export const repoUrl = 'https://github.com/TBI-AI-Incubator/codify'

/** Folders of docs/ that the site publishes. Everything else stays GitHub-only. */
export const sections = ['start', 'guide', 'concepts', 'reference', 'operate', 'open-core', 'project', 'decisions']
