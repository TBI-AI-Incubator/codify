# Codify documentation site

An [Astro Starlight](https://starlight.astro.build) site built from the Markdown in the repository's [`docs/`](../../docs) folder. Pages are written for GitHub first: links between them are relative `.md` paths, and the build turns them into site routes.

```bash
pnpm install
pnpm dev
pnpm build
```

`pnpm dev` serves on port 4330. `pnpm build` writes a static site to `dist/` for any host and fails on a broken internal link. Set `DOCS_SITE` to the public origin and `DOCS_BASE` to a sub-path when the site is not served from `/`.

## What is published

The sections `start`, `guide`, `concepts`, `reference`, `operate`, `open-core` and `project`, the decision records in `docs/decisions`, and the landing page `docs/index.mdx`. Other folders under `docs/` stay on GitHub only. Files whose names start with `_` are skipped.

Six reference pages (`cli`, `http-api`, `mcp-tools`, `python-api`, `jurisdiction-config`, `environment`) are generated from the code; regenerate them from the repository root with `uv run --extra mcp --extra serve python apps/docs/scripts/generate_reference.py`, adding `--core <path>` to read another checkout. CI fails when they drift.

To add a page, write `docs/<section>/<slug>.md` with a `title`, `description` and `sidebar.order`. Its place in the sidebar is set in [`src/sidebar.mjs`](src/sidebar.mjs); a page not listed there goes to the end of its section.

Each page is also served as Markdown at its address with `.md` in place of the final `/`, and `llms.txt` lists the site for software agents. Search is Pagefind, built with the site.

## Look and feel

The look lives in [`../docs-theme`](../docs-theme), the package `@codify/docs-theme`: tokens, fonts and component overrides as a Starlight plugin. Another repository can use it as a git subdirectory dependency:

```bash
pnpm add "github:TBI-AI-Incubator/codify#main&path:/apps/docs-theme"
```

```js
import codifyDocsTheme from '@codify/docs-theme'

starlight({ plugins: [codifyDocsTheme()] })
```

Pass `journey`, a list of `{ label, slug, pages }`, to show the pipeline progress rail on every page a stage lists.
