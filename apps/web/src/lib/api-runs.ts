export interface RunCreated {
  run_id: string;
  duplicate?: unknown;
}

export function normaliseIngestUrl(sourceUrl: string): string {
  return sourceUrl;
}

export function startIngestRun(
  file: File,
  fields: {
    jurisdiction_code: string;
    law_title?: string;
    doctype?: string;
    year?: number;
    number?: string;
  },
): Promise<RunCreated> {
  const form = new FormData();
  form.set('file', file);
  for (const [key, value] of Object.entries(fields)) {
    if (value !== undefined && value !== null) form.set(key, String(value));
  }
  return fetch('/api/proxy/runs/ingest', { method: 'POST', body: form }).then(async (response) => {
    if (!response.ok) throw new Error(await response.text());
    return response.json() as Promise<RunCreated>;
  });
}

export function startIngestUrlRun(body: {
  source_url: string;
  jurisdiction_code: string;
  law_title?: string;
  doctype?: string;
}): Promise<RunCreated> {
  const sourceUrl = normaliseIngestUrl(body.source_url);
  return fetch('/api/proxy/runs/ingest-url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...body, source_url: sourceUrl }),
  }).then(async (response) => {
    if (!response.ok) throw new Error(await response.text());
    return response.json() as Promise<RunCreated>;
  });
}

export function retryRun(id: string): Promise<RunCreated> {
  return fetch(`/api/proxy/runs/${encodeURIComponent(id)}/retry`, {
    method: 'POST',
  }).then(async (response) => {
    if (!response.ok) throw new Error(await response.text());
    return response.json() as Promise<RunCreated>;
  });
}
