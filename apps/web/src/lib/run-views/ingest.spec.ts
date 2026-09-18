import { describe, expect, it } from 'vitest';
import { deriveIngestView } from './ingest';

const t = ((key: string) => key) as never;

describe('deriveIngestView', () => {
  it('consumes the standalone stream through persisted completion', () => {
    const view = deriveIngestView([
      { type: 'data-run', id: 'run-running', data: { kind: 'ingest', status: 'running' } },
      { type: 'data-page', id: 'page-1', data: { page: 1, method: 'text', text_len: 240 } },
      { type: 'data-run-stage', id: 'stage-structure', data: { stage: 'structure', status: 'done' } },
      { type: 'data-result', id: 'result', data: { law_id: 'law-1', version_id: 'version-1' } },
      { type: 'data-run', id: 'run-succeeded', data: { kind: 'ingest', status: 'succeeded' } },
    ], t);
    expect(view.phase).toBe('done');
    expect(view.pages).toEqual([{ page: 1, method: 'text', text_len: 240 }]);
    expect(view.persisted).toEqual({ lawId: 'law-1', versionId: 'version-1' });
  });
});
