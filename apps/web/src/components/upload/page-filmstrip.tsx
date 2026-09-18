import { AlertTriangle, ScanLine } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { cn } from '../../vendor/tbi-ui';

import type { PageInfo } from '@/lib/run-views/ingest';

interface PageFilmstripProps {
  pages: PageInfo[];
  totalPages?: number | null;
  className?: string;
}

const COMPACT_THRESHOLD = 30;
const CHIP_THRESHOLD = 100;

export function PageFilmstrip({ pages, totalPages, className }: PageFilmstripProps) {
  const { t } = useTranslation();
  const total = totalPages ?? pages.length;
  const totalChars = pages.reduce((acc, p) => acc + p.text_len, 0);

  if (total >= CHIP_THRESHOLD) {
    return (
      <div
        className={cn(
          'flex flex-col items-center justify-center gap-3 py-12 text-center',
          className,
        )}
        aria-live="polite"
      >
        <div className="text-[2.5rem] font-medium tabular-nums tracking-[-0.02em] text-ink">
          {pages.length}
          <span className="text-ink-40"> / {total}</span>
        </div>
        <p className="font-tbi-sans text-sm text-ink-60">
          {t('pages extracted')} ·{' '}
          <span className="tabular-nums text-ink-80">{(totalChars / 1000).toFixed(1)}k</span>{' '}
          {t('chars')}
        </p>
      </div>
    );
  }

  const isCompact = total >= COMPACT_THRESHOLD;
  const tileWidth = isCompact ? 'w-6' : 'w-16';
  const tileHeight = isCompact ? 'h-8' : 'h-22';

  const slots: { page: number; info: PageInfo | null }[] = [];
  if (totalPages != null) {
    for (let p = 1; p <= totalPages; p++) {
      slots.push({ page: p, info: pages.find((q) => q.page === p) ?? null });
    }
  } else {
    for (const info of pages) slots.push({ page: info.page, info });
    slots.push({ page: pages.length + 1, info: null });
  }

  return (
    <div
      className={cn('flex flex-wrap items-end gap-2 px-2 py-6', isCompact && 'gap-1.5', className)}
      role="status"
      aria-live="polite"
      aria-label={t('Extracted {{extracted}} of {{total}} pages', {
        extracted: pages.length,
        total: total ?? '?',
      })}
    >
      {slots.map(({ page, info }) => (
        <Tile
          key={page}
          page={page}
          info={info}
          compact={isCompact}
          widthClass={tileWidth}
          heightClass={tileHeight}
        />
      ))}
    </div>
  );
}

function Tile({
  page,
  info,
  compact,
  widthClass,
  heightClass,
}: {
  page: number;
  info: PageInfo | null;
  compact: boolean;
  widthClass: string;
  heightClass: string;
}) {
  const { t } = useTranslation();
  const filled = info !== null;
  const degraded = filled && info?.degraded === true;
  return (
    <div
      data-page={page}
      data-state={degraded ? 'degraded' : filled ? 'extracted' : 'pending'}
      aria-label={degraded ? t('Page {{page}} could not be read reliably', { page }) : undefined}
      className={cn(
        'relative flex flex-col items-center justify-end overflow-hidden rounded-sm border transition-all duration-200',
        widthClass,
        heightClass,
        degraded
          ? 'border-coral-strong/40 bg-coral-soft/50'
          : filled
            ? 'border-teal-strong/30 bg-teal-soft/50'
            : 'border-dashed border-ink-20 bg-paper-sunken',
      )}
    >
      {/* A degraded page flags itself; otherwise an OCR page shows the fallback
          glyph. Skipped in compact mode. */}
      {degraded && !compact ? (
        <AlertTriangle
          aria-label={t('Degraded read')}
          className="absolute end-1 top-1 size-3 text-coral-strong"
        />
      ) : filled && info?.method === 'ocr' && !compact ? (
        <ScanLine
          aria-label={t('OCR fallback')}
          className="absolute end-1 top-1 size-3 text-teal-strong/60"
        />
      ) : null}
      {compact ? (
        filled ? (
          <span
            className={cn(
              'absolute inset-0',
              degraded ? 'bg-coral-strong/40' : 'bg-teal-strong/40',
            )}
            aria-hidden
          />
        ) : null
      ) : (
        <>
          <span
            className={cn(
              'mt-1 font-tbi-mono text-[0.6875rem] tabular-nums',
              degraded ? 'text-coral-strong' : filled ? 'text-teal-strong' : 'text-ink-40',
            )}
          >
            {page}
          </span>
          {filled ? (
            <span className="mb-1 text-[0.625rem] tabular-nums text-ink-60">
              {info?.text_len ? `${(info.text_len / 1000).toFixed(1)}k` : '·'}
            </span>
          ) : null}
        </>
      )}
    </div>
  );
}
