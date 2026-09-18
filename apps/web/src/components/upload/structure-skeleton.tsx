import type { TFunction } from 'i18next';
import { Check } from 'lucide-react';
import type * as React from 'react';
import { useTranslation } from 'react-i18next';

import { CircleFlag, cn } from '../../vendor/tbi-ui';

import { formatKindCount, type Stage } from '@/lib/run-views/ingest';
import { countryName } from '@/lib/countries';

interface StructureSkeletonProps {
  phase: Stage | 'idle' | 'done';
  title: string | null;
  year: number | null;
  number: string | null;
  jurisdictionCode: string;
  anchors?: { summary: Record<string, number> | null; total: number };
  fraction?: number;
  className?: string;
}

const PHASE_NARRATION: Record<Stage | 'idle' | 'done', string> = {
  idle: 'Waiting',
  acquire: 'Fetching the source',
  extract: 'Extracting pages',
  metadata: 'Reading metadata',
  structure: 'Recognising structure',
  parse: 'Parsing the document',
  enrich: 'Enriching cross-references',
  validate: 'Validating output',
  persist: 'Saving to the corpus',
  done: 'Complete',
};

export function StructureSkeleton({
  phase,
  title,
  year,
  number,
  jurisdictionCode,
  anchors,
  fraction = 0,
  className,
}: StructureSkeletonProps) {
  const { t } = useTranslation();
  const showChecks = phase === 'validate' || phase === 'persist' || phase === 'done';
  const showTitleCard = phase !== 'idle';
  const summary = anchors?.summary ?? null;
  const chapterN = summary ? (summary.chapter ?? summary.part ?? summary.title ?? 0) : 0;
  const unitN = summary ? (summary.article ?? summary.section ?? 0) : 0;
  const shownUnits = unitN > 0 ? Math.min(unitN, 16) : showTitleCard ? 6 : 0;
  const groups = buildGroups(chapterN, shownUnits);
  const settled = ['parse', 'enrich', 'validate', 'persist', 'done'].includes(phase);
  const filledThrough = settled ? shownUnits : Math.round(fraction * shownUnits);
  const countsLine = formatCounts(t, summary);

  return (
    <div className={cn('mx-auto max-w-[68ch] space-y-8 py-2', className)}>
      {/* Single live region for assistive tech. The skeleton bones below
          are decorative (aria-hidden); this is the source of truth for
          screen-reader users so they hear "Recognising structure" instead
          of "Chapter 1, Article 1, Chapter 2, Article 1, Chapter 3…". */}
      <span aria-live="polite" className="sr-only">
        {t(PHASE_NARRATION[phase])}
        {title ? `: ${title}` : ''}
      </span>

      {showTitleCard ? (
        <header className="flex items-start gap-4 border-b border-ink-20 pb-6">
          <CircleFlag
            countryCode={jurisdictionCode}
            height="32"
            width="32"
            title={countryName(jurisdictionCode)}
            className="mt-1 shrink-0 rounded-full ring-1 ring-ink-20"
          />
          <div className="min-w-0 flex-1 space-y-2">
            {title ? (
              <h2 className="font-legislative text-[1.375rem] font-medium leading-[1.3] tracking-[-0.005em] text-ink [overflow-wrap:anywhere]">
                {title}
              </h2>
            ) : (
              <div className="h-7 w-3/4 rounded shimmer-bone" aria-label={t('Title forming')} />
            )}
            <div className="flex flex-wrap items-center gap-2 micro-caps text-ink-60">
              {year ? <span className="tabular-nums">{year}</span> : null}
              {number ? (
                <span className="tabular-nums">{t('No. {{number}}', { number })}</span>
              ) : null}
              <span aria-hidden className="text-ink-40">
                ·
              </span>
              <span>{countryName(jurisdictionCode) ?? jurisdictionCode}</span>
            </div>
          </div>
        </header>
      ) : null}

      {countsLine ? <p className="micro-caps text-ink-60">{countsLine}</p> : null}

      <div aria-hidden="true" className="space-y-7">
        {groups.map((count, ci) => {
          const articleStart = groups.slice(0, ci).reduce((sum, c) => sum + c, 0);
          return (
            <ChapterBone
              key={ci}
              chapterNum={ci + 1}
              showChapter={chapterN > 0}
              articleStart={articleStart}
              articleCount={count}
              filledThrough={filledThrough}
              showCheck={showChecks}
            />
          );
        })}
      </div>
    </div>
  );
}

const TICK_STEP_MS = 60;
function tickDelay(rowIndex: number): React.CSSProperties {
  return { animationDelay: `${rowIndex * TICK_STEP_MS}ms` };
}

function buildGroups(chapters: number, units: number): number[] {
  if (units <= 0) return [];
  const g = Math.min(Math.max(chapters, 0), 6);
  if (g <= 1) return [units];
  const base = Math.floor(units / g);
  const rem = units % g;
  return Array.from({ length: g }, (_, i) => base + (i < rem ? 1 : 0)).filter((c) => c > 0);
}

function formatCounts(t: TFunction, summary: Record<string, number> | null): string | null {
  if (!summary) return null;
  const parts = Object.entries(summary)
    .filter(([, c]) => c > 0)
    .map(([k, c]) => formatKindCount(t, k, c));
  return parts.length > 0 ? parts.join(' · ') : null;
}

function ChapterBone({
  chapterNum,
  showChapter,
  articleStart,
  articleCount,
  filledThrough,
  showCheck,
}: {
  chapterNum: number;
  showChapter: boolean;
  articleStart: number;
  articleCount: number;
  filledThrough: number;
  showCheck: boolean;
}) {
  const { t } = useTranslation();
  const articles = Array.from({ length: articleCount }, (_, i) => articleStart + i);
  return (
    <div className="space-y-3">
      {showChapter ? (
        <div className="flex items-center gap-3">
          <span className="micro-caps-sm text-ink-60">
            {t('Chapter {{number}}', { number: chapterNum })}
          </span>
          <span className="h-2 flex-1 rounded shimmer-bone" />
          {showCheck ? (
            <Check
              aria-hidden
              className="tick-in size-3.5 shrink-0 text-teal-strong"
              strokeWidth={3}
              style={tickDelay(articleStart)}
            />
          ) : null}
        </div>
      ) : null}
      <div className={cn('space-y-2', showChapter && 'ps-4')}>
        {articles.map((idx) => (
          <ArticleBone
            key={idx}
            articleNum={idx + 1}
            filled={idx < filledThrough}
            showCheck={showCheck}
            rowIndex={idx}
          />
        ))}
      </div>
    </div>
  );
}

function ArticleBone({
  articleNum,
  filled,
  showCheck,
  rowIndex,
}: {
  articleNum: number;
  filled: boolean;
  showCheck: boolean;
  rowIndex: number;
}) {
  const { t } = useTranslation();
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3">
        <span className="micro-caps-sm text-emphasis-strong/80">
          {t('Article {{number}}', { number: articleNum })}
        </span>
        <span
          className={cn(
            'h-1.5 flex-1 max-w-40 rounded transition-colors duration-500',
            filled ? 'bg-emphasis-strong/25' : 'shimmer-bone',
          )}
        />
        {showCheck ? (
          <Check
            aria-hidden
            className="tick-in size-3 shrink-0 text-teal-strong/70"
            strokeWidth={3}
            style={tickDelay(rowIndex)}
          />
        ) : null}
      </div>
      {filled ? (
        <div className="space-y-1.5 ps-4">
          {[82, 96, 64].map((widthPct, i) => (
            <span
              key={i}
              aria-hidden
              className="block h-2 rounded bg-ink-20"
              style={{ width: `${widthPct}%` }}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}
