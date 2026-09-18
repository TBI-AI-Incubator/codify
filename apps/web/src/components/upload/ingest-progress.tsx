import { LawReader, type VersionDocument as SteleVersionDocument } from '../../vendor/stele';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, RotateCcw } from 'lucide-react';
import { useEffect, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';

import { cn } from '../../vendor/tbi-ui';

import type { RunStreamStatus } from '@/hooks/use-run';
import { getVersionDocument } from '@/lib/api-laws';
import { formatNumber } from '@/lib/format';
import type { IngestView } from '@/lib/run-views/ingest';

import { PageFilmstrip } from './page-filmstrip';
import { StageStepper } from './stage-stepper';
import { StructureSkeleton } from './structure-skeleton';

const MIN_REVEAL_MS = 1500;
const MAX_HOLD_MS = 4500;

interface IngestProgressProps {
  view: IngestView;
  status: RunStreamStatus;
  errorText: string | null;
  isReconnecting?: boolean;
  jurisdictionCode: string;
  sourceLabel: string;
  onComplete?: (lawId: string) => void;
  onRetry?: () => void;
  className?: string;
}

export function IngestProgress({
  view,
  status,
  errorText,
  isReconnecting,
  jurisdictionCode,
  sourceLabel,
  onComplete,
  onRetry,
  className,
}: IngestProgressProps) {
  const { t } = useTranslation();
  const progress = useMemo(
    () => ({
      ...view,
      failure:
        view.failure ??
        (status === 'error' && errorText ? { stage: 'run', error: errorText } : null),
    }),
    [view, status, errorText],
  );
  const versionId = progress.persisted?.versionId ?? null;

  const docQuery = useQuery({
    queryKey: ['version-document', versionId],
    queryFn: () => getVersionDocument(versionId as string),
    enabled: !!versionId,
  });

  const firedRef = useRef(false);
  useEffect(() => {
    if (firedRef.current) return;
    if (!progress.persisted) return;
    const lawId = progress.persisted.lawId;
    const fire = () => {
      if (firedRef.current) return;
      firedRef.current = true;
      onComplete?.(lawId);
    };
    const minTimer = docQuery.data ? window.setTimeout(fire, MIN_REVEAL_MS) : null;
    const maxTimer = window.setTimeout(fire, MAX_HOLD_MS);
    return () => {
      if (minTimer !== null) window.clearTimeout(minTimer);
      window.clearTimeout(maxTimer);
    };
  }, [docQuery.data, progress.persisted, onComplete]);

  const showRetry = !!progress.failure && onRetry;

  return (
    <section
      aria-label={t('Ingestion progress')}
      className={cn(
        'grid gap-6 rounded-md border border-ink-20 bg-paper p-6',
        'lg:grid-cols-[minmax(0,1fr)_minmax(0,3fr)]',
        className,
      )}
    >
      <aside className="flex flex-col justify-between gap-6">
        <StageStepper stages={progress.stages} />
        <div className="space-y-2 text-[0.75rem] text-ink-60">
          <p className="truncate font-tbi-mono" title={sourceLabel}>
            {sourceLabel}
          </p>
          {isReconnecting ? <p role="status">{t('Reconnecting to stream…')}</p> : null}
          {showRetry ? (
            <button
              type="button"
              onClick={onRetry}
              className="inline-flex min-h-9 items-center gap-1.5 rounded-sm border border-ink-20 bg-paper px-3 py-1.5 font-tbi-sans text-[0.8125rem] text-ink-80 transition-colors hover:bg-paper-sunken focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emphasis focus-visible:ring-offset-1"
            >
              <RotateCcw className="size-3.5" aria-hidden />
              {t('Retry')}
            </button>
          ) : null}
        </div>
      </aside>

      <div className="min-w-0">
        <RightPane
          progress={progress}
          jurisdictionCode={jurisdictionCode}
          docData={docQuery.data ?? null}
        />
      </div>
    </section>
  );
}

function RightPane({
  progress,
  jurisdictionCode,
  docData,
}: {
  progress: IngestView;
  jurisdictionCode: string;
  docData: { sections?: unknown[]; provisions?: unknown[] } | null;
}) {
  const { t } = useTranslation();
  if (progress.failure) {
    return (
      <div
        role="alert"
        aria-live="assertive"
        className="flex h-full min-h-[40vh] flex-col items-center justify-center gap-3 rounded-md border border-coral-soft bg-coral-soft/20 p-8 text-center"
      >
        <AlertTriangle className="size-8 text-coral-strong" aria-hidden />
        <p className="font-tbi-sans text-sm font-medium text-coral-strong">
          {t('Stage failed: {{stage}}', { stage: progress.failure.stage })}
        </p>
        <p className="max-w-md font-tbi-mono text-[0.75rem] text-coral-strong/80 [overflow-wrap:anywhere]">
          {progress.failure.error}
        </p>
      </div>
    );
  }

  if (progress.phase === 'idle') {
    return (
      <div className="flex h-full min-h-[40vh] items-center justify-center text-sm text-ink-60">
        {t('Waiting for the first frame…')}
      </div>
    );
  }

  if (progress.phase === 'extract') {
    const lastPage = progress.pages.at(-1);
    const headline =
      progress.pages.length === 0
        ? t('Reading the document')
        : lastPage?.method === 'ocr'
          ? t('Page {{page}}, OCR fallback', { page: lastPage.page })
          : t('Page {{page}}, {{chars}} chars', {
              page: lastPage?.page ?? progress.pages.length,
              chars: formatNumber(lastPage?.text_len ?? 0),
            });
    return (
      <div className="space-y-4">
        <p className="font-tbi-sans text-[0.875rem] text-ink-80 tabular-nums" aria-live="polite">
          {headline}
        </p>
        <PageFilmstrip pages={progress.pages} />
      </div>
    );
  }

  if (progress.phase === 'persist' && docData) {
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-2 micro-caps text-teal-strong">
          <span aria-hidden className="size-1.5 rounded-full bg-teal-strong" />
          {t('Persisted ✓ · Opening law…')}
        </div>
        <div className="max-h-[60vh] overflow-y-auto pe-2">
          <LawReader
            document={docData as unknown as SteleVersionDocument}
            chapterLabel={(num) => t('Chapter {{number}}', { number: num })}
            enactingFormulaLabel={t('Enacting formula')}
            copyCitationLabel={(citation) => t('Copy citation {{citation}}', { citation })}
            copyLabel={(citation) => t('Copy {{citation}}', { citation })}
            copiedLabel={t('Copied')}
          />
        </div>
      </div>
    );
  }

  return (
    <StructureSkeleton
      phase={progress.phase}
      title={progress.extracted.title}
      year={progress.extracted.year}
      number={progress.extracted.number}
      jurisdictionCode={jurisdictionCode}
      anchors={progress.anchors}
      fraction={progress.structureFraction}
    />
  );
}
