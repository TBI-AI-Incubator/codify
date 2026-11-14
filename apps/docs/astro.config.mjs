import { defineConfig } from 'astro/config'
import starlight from '@astrojs/starlight'
import starlightLinksValidator from 'starlight-links-validator'
import starlightDotMd from 'starlight-dot-md'
import starlightLlmsTxt from 'starlight-llms-txt'
import codifyDocsTheme from '@codify/docs-theme'
import { figures } from '@codify/docs-theme/figures'
import { journey } from './src/journey.mjs'
import { docsLinks } from './src/links.mjs'
import { statusIcons } from './src/status.mjs'
import { sidebar } from './src/sidebar.mjs'
import { repoUrl } from './src/site.mjs'

const base = process.env.DOCS_BASE ?? '/'

export default defineConfig({
  site: process.env.DOCS_SITE ?? 'http://localhost:4330',
  base,
  server: { port: 4330 },
  devToolbar: { enabled: false },
  // Pages live outside this folder, so their imports are resolved from here.
  vite: { resolve: { dedupe: ['astro', '@astrojs/starlight', '@codify/docs-theme'] } },
  integrations: [
    {
      name: 'docs-links',
      hooks: {
        'astro:config:setup': ({ config }) => {
          config.markdown.processor.options.mdastPlugins.push(docsLinks(base))
          config.markdown.processor.options.mdastPlugins.push(statusIcons())
          config.markdown.processor.options.mdastPlugins.push(figures())
        },
      },
    },
    starlight({
      title: 'Codify',
      description: 'Documentation for Codify, the open-source engine that turns legislation into Akoma Ntoso XML.',
      locales: { root: { label: 'English', lang: 'en-GB' } },
      social: [{ icon: 'github', label: 'GitHub', href: repoUrl }],
      // File paths start `../../docs/`, so the URL resolves to the repository's docs folder.
      editLink: { baseUrl: `${repoUrl}/edit/main/apps/docs/` },
      lastUpdated: true,
      markdown: { processedDirs: ['../../docs'] },
      sidebar,
      plugins: [
        codifyDocsTheme({ journey }),
        starlightDotMd(),
        starlightLlmsTxt({
          details: 'Every page is also served as Markdown: replace the final `/` of its address with `.md`.',
          // A figure keeps its caption and text description; the drawing itself is dropped.
          customSelectors: { all: ['.cd-figure__art'] },
        }),
        starlightLinksValidator(),
      ],
    }),
  ],
})
