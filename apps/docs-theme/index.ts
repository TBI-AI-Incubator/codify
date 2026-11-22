import type { StarlightPlugin } from '@astrojs/starlight/types'

export interface Stage {
  /** Name shown on the rail. */
  label: string
  /** Slug of the page that opens the stage, for example `guide/ocr`. */
  slug: string
  /** Every page in the stage; each shows the progress rail. */
  pages: string[]
}

export interface ThemeOptions {
  /** Pipeline stages, in order. */
  journey?: Stage[]
}

const optionsId = 'virtual:codify-docs-theme/options'

export default function codifyDocsTheme(options: ThemeOptions = {}): StarlightPlugin {
  return {
    name: '@codify/docs-theme',
    hooks: {
      'config:setup'({ config, updateConfig, addIntegration }) {
        updateConfig({
          customCss: ['@codify/docs-theme/styles.css', ...(config.customCss ?? [])],
          components: {
            PageTitle: '@codify/docs-theme/components/PageTitle.astro',
            SiteTitle: '@codify/docs-theme/components/SiteTitle.astro',
            Footer: '@codify/docs-theme/components/Footer.astro',
            ...config.components,
          },
          expressiveCode:
            config.expressiveCode === false
              ? false
              : {
                  themes: ['min-dark', 'min-light'],
                  useStarlightUiThemeColors: true,
                  styleOverrides: {
                    borderRadius: '3px',
                    codeFontFamily: 'var(--cd-font-mono)',
                    codeFontSize: '0.8125rem',
                    codeLineHeight: '1.7',
                    frames: { frameBoxShadowCssValue: 'none' },
                  },
                  ...(typeof config.expressiveCode === 'object' ? config.expressiveCode : {}),
                },
        })
        addIntegration({
          name: '@codify/docs-theme',
          hooks: {
            'astro:config:setup'({ updateConfig, injectRoute }) {
              // Starlight links /favicon.svg by default; this serves a text monogram.
              injectRoute({ pattern: '/favicon.svg', entrypoint: '@codify/docs-theme/favicon.svg.ts', prerender: true })
              updateConfig({
                vite: {
                  plugins: [
                    {
                      name: '@codify/docs-theme/options',
                      resolveId: (id) => (id === optionsId ? `\0${optionsId}` : undefined),
                      load: (id) => (id === `\0${optionsId}` ? `export default ${JSON.stringify(options)}` : undefined),
                    },
                  ],
                },
              })
            },
          },
        })
      },
    },
  }
}
