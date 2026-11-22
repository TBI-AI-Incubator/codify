import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { dirname } from 'node:path'
import { defineCollection } from 'astro:content'
import { glob } from 'astro/loaders'
import { docsSchema } from '@astrojs/starlight/schema'
import { sections } from './site.mjs'

const files = glob({
  base: '../../docs',
  pattern: ['index.mdx', `{${sections.join(',')}}/**/[^_]*.{md,mdx}`],
})

function lastCommit(file: string) {
  try {
    const date = execFileSync('git', ['log', '-1', '--format=%cI', '--', file], { cwd: dirname(file), encoding: 'utf8' })
    return date.trim() ? new Date(date.trim()) : undefined
  } catch {
    return undefined
  }
}

export const collections = {
  docs: defineCollection({
    loader: {
      name: 'repository-docs',
      load: (context) =>
        files.load({
          ...context,
          parseData: (props) => {
            const data: Record<string, unknown> = { ...props.data }
            if (props.filePath) {
              // Starlight only reads commit dates for its own content folder.
              data.lastUpdated ??= lastCommit(props.filePath)
              // Pages written for GitHub alone have no frontmatter: the first heading is the title.
              data.title ??= /^#\s+(.+)$/m.exec(readFileSync(props.filePath, 'utf8'))?.[1]
            }
            return context.parseData({ ...props, data })
          },
        }),
    },
    schema: docsSchema(),
  }),
}
