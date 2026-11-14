import type { APIRoute } from 'astro'

export const prerender = true

// A text monogram, not a brand mark.
export const GET: APIRoute = () =>
  new Response(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><style>text{fill:#1d1d1d;font:600 26px system-ui,-apple-system,'Segoe UI',sans-serif}@media (prefers-color-scheme:dark){text{fill:#f3f4ef}}</style><text x="16" y="25" text-anchor="middle">C</text></svg>`,
    { headers: { 'Content-Type': 'image/svg+xml' } },
  )
