# Third-party notices

Codify source is licensed under Apache 2.0. Dependencies, bundled schemas and
other third-party materials keep their own licences and notices.

The two tables below are generated from the lockfiles: `uv.lock` for the Python
package and `apps/web/pnpm-lock.yaml` for the web app. Versions are the locked
ones. The licence column repeats what each distribution declares in its own
metadata. It is an inventory, not legal clearance, and not an inventory of any
hosted product. An install that resolves its own versions, such as
`pip install codify-core`, can differ from the lock. Review the distributions
you redistribute and the licence files inside them.

A jurisdiction configuration or this inventory grants no right to redistribute
source documents. Synthetic fixture material is identified in its files;
acquired documents require their own rights assessment.

`data/jurisdictions/eu/` quotes short passages of EU legislation (enacting
formulae and an excerpt of Regulation (EU) 2016/679) from EUR-Lex. Source:
© European Union, https://eur-lex.europa.eu, reused under Commission Decision
2011/833/EU on the reuse of Commission documents. Only the texts published in
the Official Journal of the European Union are authentic.

## Licence texts in `licenses/`

The texts are kept as supporting notices for the components below.

| File | Kept for |
| --- | --- |
| `Apache-2.0.txt` | Dependencies that declare Apache 2.0 |
| `LGPL-3.0.txt`, `GPL-3.0.txt` | Cobalt, Bluebell and PyMeeus, which declare LGPL 3 terms, and psycopg and psycopg-binary, which declare LGPL 3 only. LGPL 3 is written as a set of permissions added to GPL 3, so both texts are kept |
| `LGPL-2.1.txt`, `GPL-2.0.txt` | Native libraries inside binary wheels. The licence files in the lxml, NumPy, Pillow and Shapely wheels name components under LGPL 2.1 or GPL 2 terms, for example GEOS in Shapely and iconv in lxml. Which libraries a wheel bundles depends on the platform |
| `MPL-2.0.txt` | certifi |
| `PostgreSQL.txt` | libpq, which the psycopg-binary wheel bundles |
| `pg_textsearch.txt` | pg_textsearch, which the development Postgres image builds from pinned source. The image keeps that project's LICENSE and NOTICE |
| `Unicode-CLDR.txt` | Country and subdivision names in `codify/countries.json`, taken from Unicode CLDR 49.0.0, Copyright © 2001–2026 Unicode, Inc., under the Unicode License v3 |

The base image and operating-system packages of the development Postgres image
carry further notices that are not listed here.

## Python package (`uv.lock`)

The closure of the package's dependencies and of its `migrations`, `serve` and
`mcp` extras, on every platform the lock resolves. The `dev` group is left out.
Licence metadata is read from the distributions installed from the lock on
macOS arm64. Three locked packages install only on other platforms
(`httpx2-jsfetch`, `pywin32`, `tzdata`); their entries repeat the package
index's metadata for the locked version.

| Distribution | Version | Declared licence metadata |
| --- | --- | --- |
| alembic | 1.20.1 | MIT |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| asyncpg | 0.32.0 | Apache-2.0 |
| attrs | 26.1.0 | MIT |
| backoff | 2.2.1 | MIT |
| beautifulsoup4 | 4.15.0 | MIT License |
| bluebell-akn | 5.0.0 | LGPLv3+ |
| certifi | 2026.10.23 | MPL-2.0 |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.1 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| cobalt | 9.0.1 | LGPLv3+ |
| convertdate | 2.5.1 | MIT |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| distro | 1.9.0 | Apache License, Version 2.0 |
| fastapi | 0.146.0 | MIT |
| genai-prices | 0.1.9 | MIT |
| google-auth | 2.58.0 | Apache 2.0 |
| google-genai | 2.31.0 | Apache-2.0 |
| googleapis-common-protos | 1.75.3 | Apache-2.0 |
| greenlet | 3.5.5 | MIT AND PSF-2.0 |
| griffelib | 2.3.0 | ISC |
| h11 | 0.16.0 | MIT |
| hijridate | 2.6.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpcore2 | 2.12.0 | BSD-3-Clause |
| httpx | 0.28.1 | BSD-3-Clause |
| httpx2 | 2.12.0 | BSD-3-Clause |
| httpx2-jsfetch | 1.0 | BSD-3-Clause |
| idna | 3.19 | BSD-3-Clause |
| iso8601 | 2.1.0 | MIT |
| jiter | 0.16.0 | MIT |
| jsonschema | 4.26.0 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |
| langfuse | 4.18.0 | MIT |
| logfire-api | 5.0.0 | MIT |
| lxml | 6.1.3 | BSD-3-Clause |
| Mako | 1.4.2 | MIT |
| MarkupSafe | 3.0.3 | BSD-3-Clause |
| mcp | 2.3.0 | MIT |
| mcp-types | 2.3.0 | MIT |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| openai | 3.35.1 | Apache-2.0 |
| opentelemetry-api | 1.45.1 | Apache-2.0 |
| opentelemetry-exporter-http-transport | 0.66b1 | Apache-2.0 |
| opentelemetry-exporter-otlp-common | 0.66b1 | Apache-2.0 |
| opentelemetry-exporter-otlp-proto-common | 1.45.1 | Apache-2.0 |
| opentelemetry-exporter-otlp-proto-http | 1.45.1 | Apache-2.0 |
| opentelemetry-proto | 1.45.1 | Apache-2.0 |
| opentelemetry-sdk | 1.45.1 | Apache-2.0 |
| opentelemetry-semantic-conventions | 0.66b1 | Apache-2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pdf2image | 1.17.0 | MIT |
| pgvector | 0.5.1 | MIT |
| pillow | 13.0.0 | MIT-CMU |
| protobuf | 7.36.1 | 3-Clause BSD License |
| psycopg | 3.3.5 | LGPL-3.0-only |
| psycopg-binary | 3.3.5 | LGPL-3.0-only |
| pyasn1 | 0.6.5 | BSD-2-Clause |
| pyasn1_modules | 0.4.2 | BSD |
| pycparser | 3.0 | BSD-3-Clause |
| pydantic | 2.14.1 | MIT |
| pydantic-ai-slim | 2.53.0 | MIT |
| pydantic-graph | 2.53.0 | MIT |
| pydantic-settings | 2.16.0 | MIT |
| pydantic_core | 2.50.1 | MIT |
| PyJWT | 2.15.0 | MIT |
| PyMeeus | 0.5.12 | LGPLv3 |
| pypdf | 6.21.4 | BSD-3-Clause |
| PyStemmer | 3.1.0 | MIT, BSD |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| pywin32 | 312 | PSF |
| PyYAML | 6.0.3 | MIT |
| referencing | 0.37.0 | MIT |
| regex | 2026.9.29 | Apache-2.0 AND CNRI-Python |
| requests | 2.34.2 | Apache-2.0 |
| rpds-py | 2026.6.3 | MIT |
| shapely | 2.2.0 | BSD-3-Clause |
| sniffio | 1.3.1 | MIT OR Apache-2.0 |
| soupsieve | 2.9.2 | MIT |
| SQLAlchemy | 2.0.52 | MIT |
| sqlmodel | 0.0.48 | MIT |
| sse-starlette | 3.4.11 | BSD-3-Clause |
| starlette | 1.6.0 | BSD-3-Clause |
| structlog | 26.1.0 | MIT OR Apache-2.0 |
| tenacity | 9.1.4 | Apache 2.0 |
| tiktoken | 0.14.0 | MIT License |
| truststore | 0.10.4 | MIT |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| tzdata | 2026.3 | Apache-2.0 |
| urllib3 | 2.8.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| websockets | 16.1.1 | BSD-3-Clause |
| wrapt | 2.4.0 | BSD-2-Clause |

## Web app (`apps/web/pnpm-lock.yaml`)

Production dependencies, as `pnpm licenses list --prod` reports them for an
install of the lockfile. The Geist and Noto fonts come from the `@fontsource`
packages under the SIL Open Font Licence 1.1. The flag images under
`apps/web/public/flags` are from the Circle Flags project under MIT; their
notice is `apps/web/public/flags/NOTICE`.

| Package | Version | Declared licence metadata |
| --- | --- | --- |
| @ai-sdk/gateway | 4.0.127 | Apache-2.0 |
| @ai-sdk/provider | 4.0.34 | Apache-2.0 |
| @ai-sdk/provider-utils | 5.0.69 | Apache-2.0 |
| @babel/runtime | 7.29.10 | MIT |
| @babel/runtime | 8.0.8 | MIT |
| @base-ui/react | 1.9.0 | MIT |
| @base-ui/utils | 0.5.0 | MIT |
| @codemirror/language | 6.13.1 | MIT |
| @codemirror/state | 6.7.6 | MIT |
| @codemirror/streamparser | 6.0.0 | MIT |
| @codemirror/view | 6.43.15 | MIT |
| @floating-ui/core | 1.8.0 | MIT |
| @floating-ui/dom | 1.8.0 | MIT |
| @floating-ui/react-dom | 2.1.9 | MIT |
| @floating-ui/utils | 0.2.12 | MIT |
| @fontsource/geist-mono | 5.3.0 | OFL-1.1 |
| @fontsource/geist-sans | 5.3.0 | OFL-1.1 |
| @fontsource/noto-sans | 5.3.0 | OFL-1.1 |
| @fontsource/noto-sans-arabic | 5.3.0 | OFL-1.1 |
| @fontsource/noto-sans-hebrew | 5.3.0 | OFL-1.1 |
| @lezer/common | 1.5.3 | MIT |
| @lezer/highlight | 1.2.5 | MIT |
| @lezer/lr | 1.4.11 | MIT |
| @mapbox/jsonlint-lines-primitives | 2.0.3 | MIT |
| @mapbox/point-geometry | 1.1.0 | ISC |
| @mapbox/tiny-sdf | 2.3.0 | BSD-2-Clause |
| @mapbox/unitbezier | 1.0.0 | BSD-2-Clause |
| @mapbox/vector-tile | 3.0.0 | BSD-3-Clause |
| @maplibre/geojson-vt | 6.3.0 | ISC |
| @maplibre/maplibre-gl-style-spec | 26.4.4 | ISC |
| @maplibre/mlt | 1.3.0 | (MIT OR Apache-2.0) |
| @maplibre/vt-pbf | 4.3.2 | MIT |
| @marijn/find-cluster-break | 1.0.4 | MIT |
| @radix-ui/primitive | 1.1.7 | MIT |
| @radix-ui/react-compose-refs | 1.1.5 | MIT |
| @radix-ui/react-context | 1.2.2 | MIT |
| @radix-ui/react-dialog | 1.1.23 | MIT |
| @radix-ui/react-dismissable-layer | 1.1.19 | MIT |
| @radix-ui/react-focus-guards | 1.1.6 | MIT |
| @radix-ui/react-focus-scope | 1.1.16 | MIT |
| @radix-ui/react-id | 1.1.4 | MIT |
| @radix-ui/react-portal | 1.1.17 | MIT |
| @radix-ui/react-presence | 1.1.10 | MIT |
| @radix-ui/react-primitive | 2.1.10 | MIT |
| @radix-ui/react-slot | 1.3.3 | MIT |
| @radix-ui/react-use-callback-ref | 1.1.4 | MIT |
| @radix-ui/react-use-controllable-state | 1.2.6 | MIT |
| @radix-ui/react-use-effect-event | 0.0.5 | MIT |
| @radix-ui/react-use-layout-effect | 1.1.4 | MIT |
| @remix-run/route-pattern | 0.22.1 | MIT |
| @standard-schema/spec | 1.1.0 | MIT |
| @tanstack/query-core | 5.104.5 | MIT |
| @tanstack/react-query | 5.104.5 | MIT |
| @types/debug | 4.1.13 | MIT |
| @types/estree | 1.0.9 | MIT |
| @types/estree-jsx | 1.0.5 | MIT |
| @types/geojson | 7946.0.16 | MIT |
| @types/hast | 3.0.5 | MIT |
| @types/mdast | 4.0.4 | MIT |
| @types/ms | 2.1.0 | MIT |
| @types/react | 19.3.0 | MIT |
| @types/react-dom | 19.3.0 | MIT |
| @types/unist | 2.0.11 | MIT |
| @types/unist | 3.0.3 | MIT |
| @ungap/structured-clone | 1.4.0 | ISC |
| @vercel/oidc | 3.2.0 | Apache-2.0 |
| @workflow/serde | 4.1.0 | Apache-2.0 |
| ai | 7.0.154 | Apache-2.0 |
| aria-hidden | 1.2.6 | MIT |
| attr-accept | 4.0.1 | MIT |
| bail | 2.0.2 | MIT |
| bidi-js | 1.1.0 | MIT |
| ccount | 2.0.1 | MIT |
| character-entities | 2.0.2 | MIT |
| character-entities-html4 | 2.1.0 | MIT |
| character-entities-legacy | 3.0.0 | MIT |
| character-reference-invalid | 2.0.1 | MIT |
| class-variance-authority | 0.7.1 | Apache-2.0 |
| clsx | 2.1.1 | MIT |
| cmdk | 1.1.1 | MIT |
| comma-separated-tokens | 2.0.3 | MIT |
| cookie-es | 3.1.1 | MIT |
| crelt | 1.0.7 | MIT |
| csstype | 3.2.3 | MIT |
| debug | 4.4.3 | MIT |
| decode-named-character-reference | 1.3.0 | MIT |
| dequal | 2.0.3 | MIT |
| detect-node-es | 1.1.0 | MIT |
| devlop | 1.1.0 | MIT |
| diacritics | 1.3.0 | MIT |
| earcut | 3.2.4 | ISC |
| escape-string-regexp | 5.0.0 | MIT |
| estree-util-is-identifier-name | 3.0.0 | MIT |
| eventsource-parser | 3.1.1 | MIT |
| extend | 3.0.2 | MIT |
| file-selector | 5.0.1 | MIT |
| get-nonce | 1.0.1 | MIT |
| gl-matrix | 3.4.4 | MIT |
| hast-util-to-jsx-runtime | 2.3.6 | MIT |
| hast-util-whitespace | 3.0.0 | MIT |
| html-parse-stringify | 4.0.1 | MIT |
| html-url-attributes | 3.0.1 | MIT |
| i18n-iso-countries | 7.14.0 | MIT |
| i18next | 26.5.3 | MIT |
| inline-style-parser | 0.2.7 | MIT |
| is-alphabetical | 2.0.1 | MIT |
| is-alphanumerical | 2.0.1 | MIT |
| is-decimal | 2.0.1 | MIT |
| is-hexadecimal | 2.0.1 | MIT |
| is-plain-obj | 4.1.0 | MIT |
| json-schema | 0.4.0 | (AFL-2.1 OR BSD-3-Clause) |
| json-stringify-pretty-compact | 4.0.0 | MIT |
| kdbush | 4.1.0 | ISC |
| longest-streak | 3.1.0 | MIT |
| lucide-react | 1.60.0 | ISC |
| maplibre-gl | 6.15.0 | BSD-3-Clause |
| markdown-table | 3.0.4 | MIT |
| mdast-util-find-and-replace | 3.0.2 | MIT |
| mdast-util-from-markdown | 2.0.3 | MIT |
| mdast-util-gfm | 3.1.0 | MIT |
| mdast-util-gfm-autolink-literal | 2.0.1 | MIT |
| mdast-util-gfm-footnote | 2.1.0 | MIT |
| mdast-util-gfm-strikethrough | 2.0.0 | MIT |
| mdast-util-gfm-table | 2.0.0 | MIT |
| mdast-util-gfm-task-list-item | 2.0.0 | MIT |
| mdast-util-mdx-expression | 2.0.1 | MIT |
| mdast-util-mdx-jsx | 3.2.0 | MIT |
| mdast-util-mdxjs-esm | 2.0.1 | MIT |
| mdast-util-phrasing | 4.1.0 | MIT |
| mdast-util-to-hast | 13.2.1 | MIT |
| mdast-util-to-markdown | 2.1.2 | MIT |
| mdast-util-to-string | 4.0.0 | MIT |
| micromark | 4.0.2 | MIT |
| micromark-core-commonmark | 2.0.3 | MIT |
| micromark-extension-gfm | 3.0.0 | MIT |
| micromark-extension-gfm-autolink-literal | 2.1.0 | MIT |
| micromark-extension-gfm-footnote | 2.1.0 | MIT |
| micromark-extension-gfm-strikethrough | 2.1.0 | MIT |
| micromark-extension-gfm-table | 2.1.2 | MIT |
| micromark-extension-gfm-tagfilter | 2.0.0 | MIT |
| micromark-extension-gfm-task-list-item | 2.1.0 | MIT |
| micromark-factory-destination | 2.0.1 | MIT |
| micromark-factory-label | 2.0.1 | MIT |
| micromark-factory-space | 2.0.1 | MIT |
| micromark-factory-title | 2.0.1 | MIT |
| micromark-factory-whitespace | 2.0.1 | MIT |
| micromark-util-character | 2.1.1 | MIT |
| micromark-util-chunked | 2.0.1 | MIT |
| micromark-util-classify-character | 2.0.1 | MIT |
| micromark-util-combine-extensions | 2.0.1 | MIT |
| micromark-util-decode-numeric-character-reference | 2.0.2 | MIT |
| micromark-util-decode-string | 2.0.1 | MIT |
| micromark-util-encode | 2.0.1 | MIT |
| micromark-util-html-tag-name | 2.0.1 | MIT |
| micromark-util-normalize-identifier | 2.0.1 | MIT |
| micromark-util-resolve-all | 2.0.1 | MIT |
| micromark-util-sanitize-uri | 2.0.1 | MIT |
| micromark-util-subtokenize | 2.1.0 | MIT |
| micromark-util-symbol | 2.0.1 | MIT |
| micromark-util-types | 2.0.2 | MIT |
| minimist | 1.2.8 | MIT |
| ms | 2.1.3 | MIT |
| murmurhash-js | 1.0.0 | MIT |
| next-themes | 0.4.6 | MIT |
| parse-entities | 4.0.2 | MIT |
| pbf | 5.1.2 | BSD-3-Clause |
| potpack | 2.1.0 | ISC |
| property-information | 7.2.0 | MIT |
| protocol-buffers-schema | 3.6.1 | MIT |
| quickselect | 3.0.0 | ISC |
| react | 19.3.1 | MIT |
| react-circle-flags | 0.0.30 | MIT |
| react-dom | 19.3.1 | MIT |
| react-dropzone | 20.1.4 | MIT |
| react-i18next | 17.1.0 | MIT |
| react-markdown | 10.1.0 | MIT |
| react-remove-scroll | 2.7.2 | MIT |
| react-remove-scroll-bar | 2.3.8 | MIT |
| react-router | 8.5.0 | MIT |
| react-style-singleton | 2.2.3 | MIT |
| remark-gfm | 4.0.1 | MIT |
| remark-parse | 11.0.0 | MIT |
| remark-rehype | 11.1.2 | MIT |
| remark-stringify | 11.0.0 | MIT |
| require-from-string | 2.0.2 | MIT |
| reselect | 5.3.0 | MIT |
| resolve-protobuf-schema | 2.1.0 | MIT |
| scheduler | 0.28.0 | MIT |
| sonner | 2.0.8 | MIT |
| space-separated-tokens | 2.0.2 | MIT |
| stringify-entities | 4.0.4 | MIT |
| style-mod | 4.1.4 | MIT |
| style-to-js | 1.1.21 | MIT |
| style-to-object | 1.0.14 | MIT |
| supports-color | 10.2.2 | MIT |
| tailwind-merge | 3.7.0 | MIT |
| tinyqueue | 3.0.0 | ISC |
| trim-lines | 3.0.1 | MIT |
| trough | 2.2.0 | MIT |
| tslib | 2.8.1 | 0BSD |
| tw-animate-css | 1.4.0 | MIT |
| typescript | 6.0.3 | Apache-2.0 |
| undici | 7.30.1 | MIT |
| unified | 11.0.5 | MIT |
| unist-util-is | 6.0.1 | MIT |
| unist-util-position | 5.0.0 | MIT |
| unist-util-stringify-position | 4.0.0 | MIT |
| unist-util-visit | 5.1.0 | MIT |
| unist-util-visit-parents | 6.0.2 | MIT |
| use-callback-ref | 1.3.3 | MIT |
| use-sidecar | 1.1.3 | MIT |
| use-sync-external-store | 1.7.0 | MIT |
| vfile | 6.0.3 | MIT |
| vfile-message | 4.0.3 | MIT |
| w3c-keyname | 2.2.8 | MIT |
| zod | 4.6.7 | MIT |
| zwitch | 2.0.4 | MIT |
