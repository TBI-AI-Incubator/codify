import type { components } from '@/lib/contract';

export type RunSnapshot = components['schemas']['RunSnapshot'];

async function created(response: Response): Promise<RunSnapshot> {
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<RunSnapshot>;
}

export function startIngestRun(file: File, fields: { jurisdiction: string; title?: string }): Promise<RunSnapshot> {
  const form = new FormData();
  form.set('file', file);
  form.set('jurisdiction', fields.jurisdiction);
  if (fields.title) form.set('title', fields.title);
  return fetch('/api/proxy/runs/ingest', { method: 'POST', body: form }).then(created);
}

export function startIngestUrlRun(body: { url: string; jurisdiction: string; title?: string }): Promise<RunSnapshot> {
  return fetch('/api/proxy/runs/ingest-url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(created);
}

export function retryRun(id: string): Promise<RunSnapshot> {
  return fetch(`/api/proxy/runs/${encodeURIComponent(id)}/retry`, { method: 'POST' }).then(created);
}
