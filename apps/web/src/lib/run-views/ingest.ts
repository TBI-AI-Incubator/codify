
import type { TFunction } from 'i18next';

import type { RunStageData } from '@codify/core-ts/run_events';

import { capitalize } from '@codify/tbi-ui';

import { formatNumber } from '@/lib/format';
import { dataParts, latestPart, type RunParts } from '@/lib/run-parts';

export type Stage =
  'acquire' | 'extract' | 'metadata' | 'structure' | 'parse' | 'enrich' | 'validate' | 'persist';

export type StageStatus = 'idle' | 'active' | 'complete' | 'failed';

export interface StageInfo {
  stage: Stage;
  status: StageStatus;
  detail: string | null;
  startedAt: number | null;
  durationMs: number | null;
}

export interface PageInfo {
  page: number;
  method: 'text' | 'ocr';
  text_len: number;
  degraded?: boolean;
}

export interface IngestView {
  phase: Stage | 'idle' | 'done';
  stages: StageInfo[];
  pages: PageInfo[];
  extracted: { title: string | null; year: number | null; number: string | null };
  anchors: { summary: Record<string, number> | null; total: number };
  structureFraction: number;
  validationIssues: number;
  failure: { stage: string; error: string } | null;
  persisted: { versionId: string; lawId: string } | null;
}

const STAGE_ORDER: Stage[] = [
  'acquire',
  'extract',
  'metadata',
  'structure',
  'parse',
  'enrich',
  'validate',
  'persist',
];

const STATUS_MAP: Record<RunStageData['status'], StageStatus> = {
  queued: 'idle',
  active: 'active',
  done: 'complete',
  failed: 'failed',
};

export function deriveIngestView(parts: RunParts, t: TFunction): IngestView {
  const stages: Record<Stage, StageInfo> = Object.fromEntries(
    STAGE_ORDER.map((s) => [
      s,
      { stage: s, status: 'idle' as StageStatus, detail: null, startedAt: null, durationMs: null },
    ]),
  ) as Record<Stage, StageInfo>;

  let structureFraction = 0;
  let failure: IngestView['failure'] = null;

  for (const { data } of dataParts(parts, 'run-stage')) {
    const slot = (data.stage === 'directive' ? 'structure' : data.stage) as Stage;
    if (!STAGE_ORDER.includes(slot)) continue;
    const startedAt = data.started_at ? Date.parse(data.started_at) : NaN;
    const endedAt = data.ended_at ? Date.parse(data.ended_at) : NaN;
    stages[slot] = {
      stage: slot,
      status: STATUS_MAP[data.status] ?? 'idle',
      detail: data.label ?? null,
      startedAt: Number.isFinite(startedAt) ? startedAt : null,
      durationMs:
        Number.isFinite(startedAt) && Number.isFinite(endedAt) && endedAt >= startedAt
          ? endedAt - startedAt
          : null,
    };
    if (slot === 'structure') {
      structureFraction = data.status === 'done' ? 1 : (data.fraction ?? structureFraction);
    }
    if (data.status === 'failed' && !failure) {
      failure = { stage: data.stage, error: data.label ?? t('Stage failed') };
    }
  }

  const pages: PageInfo[] = dataParts(parts, 'page').map((p) => p.data);
  const metadata = latestPart(parts, 'doc-metadata');
  const title = metadata?.title ?? null;
  const year = metadata?.year ?? null;
  const number = metadata?.number ?? null;
  const anchorsPart = latestPart(parts, 'anchors');
  const anchorSummary = anchorsPart?.summary ?? null;
  const anchorTotal = anchorsPart?.total ?? 0;
  const validationIssues = dataParts(parts, 'validation-issue').length;
  const result = latestPart(parts, 'result');
  const persisted =
    result?.version_id && result?.law_id
      ? { versionId: result.version_id, lawId: result.law_id }
      : null;

  const dependency = latestPart(parts, 'dependency');
  if (!failure && dependency) {
    failure = { stage: dependency.service, error: dependency.reason };
    const active = STAGE_ORDER.find((s) => stages[s].status === 'active');
    if (active) stages[active].status = 'failed';
  }

  const totalChars = pages.reduce((acc, p) => acc + p.text_len, 0);
  if (pages.length > 0) {
    stages.extract.detail = t('{{count}} pages · {{chars}} chars', {
      count: pages.length,
      chars: formatChars(totalChars),
    });
  }
  if (title !== null) stages.metadata.detail = truncate(title, 40);
  else if (stages.metadata.status === 'complete') stages.metadata.detail = t('Title not detected');
  if (anchorTotal > 0 && anchorSummary) {
    stages.structure.detail = Object.entries(anchorSummary)
      .map(([kind, count]) => formatKindCount(t, kind, count))
      .join(', ');
  }
  if (stages.enrich.detail) stages.enrich.detail = capitalize(stages.enrich.detail);
  if (stages.validate.status !== 'idle') {
    stages.validate.detail =
      validationIssues === 0 ? t('No issues') : t('{{count}} issues', { count: validationIssues });
  }
  if (persisted !== null) {
    stages.persist.detail = t('Saved as {{id}}…', { id: persisted.versionId.slice(0, 8) });
  }

  const runStatus = latestPart(parts, 'run')?.status;
  let phase: IngestView['phase'] = 'idle';
  if (failure) {
    phase = STAGE_ORDER.find((s) => stages[s].status === 'failed') ?? 'extract';
  } else if (
    stages.persist.status === 'complete' ||
    (persisted !== null && runStatus === 'succeeded')
  ) {
    phase = 'done';
  } else {
    for (let i = STAGE_ORDER.length - 1; i >= 0; i--) {
      const s = STAGE_ORDER[i]!;
      if (stages[s].status !== 'idle') {
        phase = s;
        break;
      }
    }
  }

  return {
    phase,
    stages: STAGE_ORDER.map((s) => stages[s]),
    pages,
    extracted: { title, year, number },
    anchors: { summary: anchorSummary, total: anchorTotal },
    structureFraction,
    validationIssues,
    failure,
    persisted,
  };
}

function formatChars(n: number): string {
  if (n < 1000) return `${n}`;
  return `${(n / 1000).toFixed(1)}k`;
}

function truncate(s: string, max: number): string {
  return s.length <= max ? s : `${s.slice(0, max - 1)}…`;
}

const KIND_COUNT_KEY: Record<string, string> = {
  article: '{{n}} articles',
  chapter: '{{n}} chapters',
  paragraph: '{{n}} paragraphs',
  part: '{{n}} parts',
  point: '{{n}} points',
  section: '{{n}} sections',
  subparagraph: '{{n}} subparagraphs',
  title: '{{n}} titles',
};

export function formatKindCount(t: TFunction, kind: string, count: number): string {
  const key = KIND_COUNT_KEY[kind.toLowerCase()];
  if (key) return t(key, { n: formatNumber(count), count });
  const noun = kind.toLowerCase();
  return `${formatNumber(count)} ${count === 1 ? noun : `${noun}s`}`;
}
