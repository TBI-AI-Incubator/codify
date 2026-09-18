import { Check, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { cn } from '@codify/tbi-ui';

import type { Stage, StageInfo } from '@/lib/run-views/ingest';

const STAGE_LABELS: Record<Stage, string> = {
  acquire: 'Acquire',
  extract: 'Extract',
  metadata: 'Metadata',
  structure: 'Structure',
  parse: 'Parse',
  enrich: 'Enrich',
  validate: 'Validate',
  persist: 'Persist',
};

interface StageStepperProps {
  stages: StageInfo[];
  className?: string;
}

export function StageStepper({ stages, className }: StageStepperProps) {
  const { t } = useTranslation();
  return (
    <ol className={cn('flex flex-col gap-3', className)} aria-label={t('Ingestion stages')}>
      {stages.map((info) => (
        <Row key={info.stage} info={info} />
      ))}
    </ol>
  );
}

function Row({ info }: { info: StageInfo }) {
  const { t } = useTranslation();
  const label = t(STAGE_LABELS[info.stage]);
  return (
    <li
      data-stage={info.stage}
      data-status={info.status}
      aria-current={info.status === 'active' ? 'step' : undefined}
      className={cn(
        'relative flex items-start gap-3 rounded-sm px-2 py-1.5 transition-colors',
        info.status === 'active' && 'bg-emphasis-soft/40',
        info.status === 'failed' && 'bg-coral-soft/40',
      )}
    >
      {info.status === 'active' ? (
        <span
          aria-hidden
          className="step-underline absolute bottom-0 start-2 end-2 h-[2px] rounded-full bg-emphasis"
        />
      ) : null}
      <StatusDot status={info.status} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-3">
          <span
            className={cn(
              'text-[0.8125rem] font-medium leading-[1.4] tracking-[-0.005em]',
              info.status === 'idle' && 'text-ink-60',
              info.status === 'active' && 'text-ink',
              info.status === 'complete' && 'text-ink',
              info.status === 'failed' && 'text-coral-strong',
            )}
          >
            {label}
          </span>
          {info.status === 'complete' ? (
            <span aria-hidden className="shrink-0 micro-caps text-teal-strong">
              {t('✓ done')}
            </span>
          ) : null}
        </div>
        {info.detail ? (
          <p
            className={cn(
              'mt-0.5 truncate text-[0.75rem] leading-[1.35]',
              info.status === 'failed' ? 'text-coral-strong/80' : 'text-ink-60',
            )}
            title={info.detail}
          >
            {info.detail}
          </p>
        ) : null}
      </div>
    </li>
  );
}

function StatusDot({ status }: { status: StageInfo['status'] }) {
  if (status === 'complete') {
    return (
      <span
        aria-hidden
        className="mt-0.5 inline-flex size-4 shrink-0 items-center justify-center rounded-full bg-teal-soft text-teal-strong"
      >
        <Check className="size-2.5" strokeWidth={3} />
      </span>
    );
  }
  if (status === 'failed') {
    return (
      <span
        aria-hidden
        className="mt-0.5 inline-flex size-4 shrink-0 items-center justify-center rounded-full bg-coral-soft text-coral-strong"
      >
        <X className="size-2.5" strokeWidth={3} />
      </span>
    );
  }
  if (status === 'active') {
    return (
      <span aria-hidden className="mt-0.5 inline-flex size-4 shrink-0 items-center justify-center">
        <span className="size-2 rounded-full bg-emphasis" />
      </span>
    );
  }
  return (
    <span aria-hidden className="mt-0.5 inline-flex size-4 shrink-0 items-center justify-center">
      <span className="size-2 rounded-full border border-ink-40" />
    </span>
  );
}
