/* The parts the run views read, and the translation from the server's events to them. */

export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
export type StageStatus = 'queued' | 'active' | 'done' | 'failed';

export interface RunData { status: RunStatus; error?: string | null }
export interface RunStageData {
  stage: string;
  status: StageStatus;
  label?: string | null;
  fraction?: number | null;
  started_at?: string | null;
  ended_at?: string | null;
}
export interface PageData { page: number; method: 'text' | 'ocr'; text_len: number; degraded?: boolean }
export interface DocMetadataData { title?: string | null; year?: number | null; number?: string | null }
export interface AnchorsData { summary: Record<string, number>; total: number }
export interface ValidationIssueData { issue: Record<string, unknown> }
export interface ResultData { version_id?: string | null; law_id?: string | null }
export interface DependencyData { service: string; reason: string }

export interface RunPart { type: string; id?: string; data?: unknown }

const STAGE_OF_KIND: Record<string, string> = {
  page_extracted: 'extract',
  metadata_extracted: 'metadata',
  anchors_detected: 'structure',
  structure_progress: 'structure',
  structured: 'structure',
  parsed: 'parse',
  enriched: 'enrich',
  validation_issued: 'validate',
};

const stage = (name: string, status: StageStatus, extra: Partial<RunStageData> = {}): RunPart => ({
  type: 'data-run-stage',
  id: name,
  data: { stage: name, status, ...extra } satisfies RunStageData,
});

/* One server event becomes the parts the ingest view already understands. */
export function partsOf(kind: string, event: Record<string, unknown>): RunPart[] {
  const own = STAGE_OF_KIND[kind];
  const parts: RunPart[] = own ? [stage(own, 'active')] : [];
  switch (kind) {
    case 'page_extracted':
      return [...parts, { type: 'data-page', id: `page-${String(event.page)}`, data: event as unknown as PageData }];
    case 'metadata_extracted': {
      const m = (event.metadata ?? {}) as Record<string, unknown>;
      const year = Number.parseInt(String(m.year ?? ''), 10);
      return [
        stage('extract', 'done'),
        stage('metadata', 'done'),
        { type: 'data-doc-metadata', data: { title: (m.title as string) || null, year: Number.isFinite(year) ? year : null, number: (m.number as string) || null } },
      ];
    }
    case 'anchors_detected':
      return [...parts, { type: 'data-anchors', data: { summary: event.summary, total: event.total } as AnchorsData }];
    case 'structure_progress':
      return [stage('structure', 'active', { fraction: event.fraction as number, label: event.label as string })];
    case 'structured':
      return [stage('structure', 'done'), stage('parse', 'active')];
    case 'parsed':
      return [stage('parse', 'done'), stage('enrich', 'active')];
    case 'enriched':
      return [stage('enrich', 'active', { label: String(event.pass_name) })];
    case 'validation_issued':
      return [...parts, { type: 'data-validation-issue', id: crypto.randomUUID(), data: { issue: event.issue } as ValidationIssueData }];
    case 'stored':
      return [
        stage('persist', 'done'),
        { type: 'data-result', data: { version_id: event.version_id, law_id: event.law_id } as ResultData },
      ];
    case 'complete':
      return [stage('enrich', 'done'), stage('validate', 'done'), { type: 'data-run', data: { status: 'succeeded' } as RunData }];
    case 'failed':
      return [
        stage(String(event.stage), 'failed', { label: String(event.error) }),
        { type: 'data-run', data: { status: 'failed', error: String(event.error) } as RunData },
      ];
    default:
      return parts;
  }
}
