import { CountryFlag, StatusPill } from '../../vendor/tbi-ui';
import { ChevronRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';

import { formatDoctype } from '@/lib/doctype';
import { lawDisplayName, lawSubtitle } from '@/lib/law-name';
import { cn } from '@/lib/utils';
import type { LawSummary } from '@/lib/types';

function isSyntheticTitle(law: LawSummary): boolean {
  const title = (law.title ?? '').trim();
  if (!title) return true;
  const doctype = law.doctype.trim();
  if (!doctype || !title.toLowerCase().startsWith(doctype.toLowerCase())) return false;
  const rest = title.slice(doctype.length);
  return !/[A-Za-z]{3,}/.test(rest);
}

export function LawRow({
  law,
  showFlag = false,
  selected,
  onSelect,
}: {
  law: LawSummary;
  showFlag?: boolean;
  selected?: boolean;
  onSelect?: (next: boolean) => void;
}) {
  const { t } = useTranslation();
  const meta = [law.year, law.number].filter(Boolean).join(' · ');
  const synthetic = isSyntheticTitle(law);
  return (
    <li className="flex items-center gap-3 border-b border-ink-20">
      {onSelect ? (
        <input
          type="checkbox"
          checked={Boolean(selected)}
          onChange={(e) => onSelect(e.target.checked)}
          aria-label={t('Select {{title}}', { title: law.title })}
          className="size-3.5 shrink-0 accent-emphasis-strong"
        />
      ) : null}
      <Link
        to={`/jurisdictions/${law.jurisdiction_code}/laws/${law.id}`}
        className={cn(
          'block min-w-0 flex-1 transition-colors hover:bg-paper-sunken',
          'focus-visible:bg-paper-sunken focus-visible:outline-none',
          'focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        )}
      >
        <div className="grid grid-cols-[minmax(0,1fr)_auto_16px] items-start gap-3 py-3">
          <div className="flex min-w-0 items-start gap-3">
            {showFlag ? <CountryFlag code={law.jurisdiction_code} size="sm" aria-hidden /> : null}
            <StatusPill
              tone="neutral"
              label={t(formatDoctype(law.doctype, law.jurisdiction_code))}
              className="shrink-0"
            />
            <div className="min-w-0 space-y-0.5">
              <div
                className={cn(
                  'truncate font-tbi-sans text-sm font-medium',
                  synthetic ? 'italic text-ink-60' : 'text-ink',
                )}
              >
                {synthetic
                  ? t('Untitled {{doctype}}', { doctype: law.doctype })
                  : lawDisplayName(law)}
              </div>
              {!synthetic && lawSubtitle(law) ? (
                <div className="truncate font-tbi-sans text-xs text-ink-60">{lawSubtitle(law)}</div>
              ) : null}
              {meta ? (
                <div className={cn('font-tbi-mono text-xs text-ink-60', synthetic && 'font-mono')}>
                  {synthetic && law.title ? law.title : meta}
                </div>
              ) : synthetic && law.title ? (
                <div className="font-tbi-mono text-xs text-ink-60">{law.title}</div>
              ) : null}
            </div>
          </div>
          <ChevronRight aria-hidden className="mt-1 size-4 shrink-0 text-ink-40 rtl:-scale-x-100" />
        </div>
      </Link>
    </li>
  );
}
