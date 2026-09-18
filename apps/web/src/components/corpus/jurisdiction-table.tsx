import { ArrowDown, ArrowUp, Check, X } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { TRADITION_LABELS } from '@/lib/constants';
import type { CorpusJurisdiction } from '@/lib/types';
import { cn } from '@/lib/utils';

import { TierBadge } from './tier-badge';
import { CircleFlag } from '@codify/tbi-ui';

type SortKey = 'tier' | 'name' | 'documents_examined' | 'unresolved_ambiguities';
type SortDir = 'asc' | 'desc';

function SortHeader({
  k,
  label,
  className,
  align = 'left',
  sortKey,
  sortDir,
  onToggle,
}: {
  k: SortKey;
  label: string;
  className?: string;
  align?: 'left' | 'right';
  sortKey: SortKey;
  sortDir: SortDir;
  onToggle: (key: SortKey) => void;
}) {
  const active = sortKey === k;
  const ariaSort = active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none';
  return (
    <th
      className={cn(
        'px-3 py-2.5 font-medium',
        align === 'right' ? 'text-end' : 'text-start',
        className,
      )}
      aria-sort={ariaSort}
      scope="col"
    >
      <button
        type="button"
        onClick={() => onToggle(k)}
        className={cn(
          'inline-flex items-center gap-1 text-muted-foreground transition-colors hover:text-foreground focus:outline-none focus-visible:rounded focus-visible:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-card',
          active && 'text-foreground',
        )}
        aria-label={`Sort by ${label}${active ? `, currently ${ariaSort}` : ''}`}
      >
        {label}
        {active &&
          (sortDir === 'asc' ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
      </button>
    </th>
  );
}

interface JurisdictionTableProps {
  jurisdictions: CorpusJurisdiction[];
  onSelect: (code: string) => void;
  highlightSet?: Set<string>;
  tradFilter: string;
  onTradFilterChange: (value: string) => void;
}

export function JurisdictionTable({
  jurisdictions,
  onSelect,
  highlightSet,
  tradFilter,
  onTradFilterChange,
}: JurisdictionTableProps) {
  const { t } = useTranslation();
  const [filter, setFilter] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('tier');
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  const availableTraditions = useMemo(() => {
    const present = new Set<string>();
    for (const j of jurisdictions) for (const t of j.tradition) present.add(t);
    return Object.entries(TRADITION_LABELS)
      .filter(([k]) => present.has(k))
      .sort((a, b) => a[1].localeCompare(b[1]));
  }, [jurisdictions]);

  const sorted = useMemo(() => {
    const f = filter.trim().toLowerCase();
    let rows = jurisdictions.filter((j) => {
      if (highlightSet && !highlightSet.has(j.code)) return false;
      if (f && !j.name.toLowerCase().includes(f) && !j.code.toLowerCase().includes(f)) return false;
      return true;
    });

    rows = [...rows].sort((a, b) => {
      let cmp = 0;
      switch (sortKey) {
        case 'tier': {
          const aTier = a.effective_tier ?? a.tier;
          const bTier = b.effective_tier ?? b.tier;
          cmp = aTier - bTier || a.name.localeCompare(b.name);
          break;
        }
        case 'name':
          cmp = a.name.localeCompare(b.name);
          break;
        case 'documents_examined':
          cmp = a.documents_examined - b.documents_examined;
          break;
        case 'unresolved_ambiguities':
          cmp = a.unresolved_ambiguities - b.unresolved_ambiguities;
          break;
      }
      return sortDir === 'asc' ? cmp : -cmp;
    });

    return rows;
  }, [jurisdictions, filter, sortKey, sortDir, highlightSet]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDir(key === 'name' ? 'asc' : 'desc');
    }
  }

  function clearFilters() {
    setFilter('');
    onTradFilterChange('');
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="corpus-filter" className="sr-only">
          {t('Filter jurisdictions by name or code')}
        </label>
        <Input
          id="corpus-filter"
          placeholder={t('Filter by name or code…')}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="h-9 max-w-xs text-sm"
        />
        <label htmlFor="corpus-tradition-filter" className="sr-only">
          {t('Filter by legal tradition')}
        </label>
        <Select
          value={tradFilter || 'all'}
          onValueChange={(v) => onTradFilterChange(v === 'all' ? '' : (v as string))}
        >
          <SelectTrigger id="corpus-tradition-filter" className="h-9 w-auto text-sm">
            <SelectValue placeholder={t('All traditions')} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t('All traditions')}</SelectItem>
            {availableTraditions.map(([k, v]) => (
              <SelectItem key={k} value={k}>
                {v}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {(filter || tradFilter) && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={clearFilters}
            className="h-9 text-sm"
          >
            <X className="me-1 h-3.5 w-3.5" />
            {t('Clear')}
          </Button>
        )}
        <span
          className="ms-auto text-xs text-muted-foreground tabular-nums"
          aria-live="polite"
          aria-atomic="true"
        >
          {sorted.length} / {jurisdictions.length}
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] text-sm">
          <caption className="sr-only">
            {t(
              'Codify corpus jurisdictions: sortable by tier, name, document count, or open issues. Select a row to open its profile.',
            )}
          </caption>
          <thead className="sticky top-0 z-10 border-b bg-background">
            <tr>
              <th className="w-8 px-3 py-2.5" scope="col">
                <span className="sr-only">{t('Flag')}</span>
              </th>
              <SortHeader
                k="name"
                label={t('Jurisdiction')}
                sortKey={sortKey}
                sortDir={sortDir}
                onToggle={toggleSort}
              />
              <SortHeader
                k="tier"
                label={t('Tier')}
                sortKey={sortKey}
                sortDir={sortDir}
                onToggle={toggleSort}
              />
              <th className="px-3 py-2.5 text-start font-medium text-muted-foreground" scope="col">
                {t('Tradition')}
              </th>
              <SortHeader
                k="documents_examined"
                label={t('Docs')}
                align="right"
                sortKey={sortKey}
                sortDir={sortDir}
                onToggle={toggleSort}
              />
              <SortHeader
                k="unresolved_ambiguities"
                label={t('Open issues')}
                align="right"
                sortKey={sortKey}
                sortDir={sortDir}
                onToggle={toggleSort}
              />
              <th className="px-3 py-2.5 text-center font-medium text-muted-foreground" scope="col">
                {t('Verified')}
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 && (
              <tr>
                <td colSpan={7} className="px-6 py-10 text-center text-muted-foreground">
                  <p className="mb-3 text-sm">{t('No jurisdictions match the current filters.')}</p>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={clearFilters}
                    className="h-9 text-sm"
                  >
                    <X className="me-1 h-3.5 w-3.5" />
                    {t('Clear filters')}
                  </Button>
                </td>
              </tr>
            )}
            {sorted.map((j) => (
              <tr
                key={j.code}
                tabIndex={0}
                aria-label={`${j.name}, Tier ${j.tier}${j.documents_examined ? `, ${j.documents_examined} documents profiled` : ''}. Open profile.`}
                className="cursor-pointer border-b border-border/50 last:border-0 transition-colors hover:bg-accent focus:outline-none focus-visible:bg-accent focus-visible:shadow-[inset_0_0_0_2px_var(--ring)]"
                onClick={() => onSelect(j.code)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onSelect(j.code);
                  }
                }}
              >
                <td className="px-3 py-2 pointer-coarse:py-3">
                  <CircleFlag
                    countryCode={j.code.split('-')[0] ?? j.code}
                    height="18"
                    width="18"
                    title={j.name}
                  />
                </td>
                <td className="px-3 py-2 pointer-coarse:py-3 font-medium [overflow-wrap:anywhere]">
                  {j.name}
                  <span className="ms-2 text-ink-40">{j.code}</span>
                </td>
                <td className="px-3 py-2 pointer-coarse:py-3">
                  <TierBadge tier={j.tier} effectiveTier={j.effective_tier ?? null} />
                </td>
                <td className="px-3 py-2 pointer-coarse:py-3 text-muted-foreground">
                  {j.tradition
                    .slice(0, 2)
                    .map((t) => TRADITION_LABELS[t] ?? t)
                    .join(', ')}
                  {j.tradition.length > 2 && ` +${j.tradition.length - 2}`}
                </td>
                <td className="px-3 py-2 pointer-coarse:py-3 text-end tabular-nums">
                  {j.documents_examined || <span className="text-ink-40">–</span>}
                </td>
                <td className="px-3 py-2 pointer-coarse:py-3 text-end tabular-nums">
                  {j.unresolved_ambiguities > 0 ? (
                    <span className="text-coral-strong">{j.unresolved_ambiguities}</span>
                  ) : (
                    <span className="text-ink-40">–</span>
                  )}
                </td>
                <td className="px-3 py-2 pointer-coarse:py-3 text-center">
                  {j.bluebell_tested ? (
                    <Check
                      className="mx-auto h-3.5 w-3.5 text-teal-strong"
                      aria-label={t('Bluebell verified')}
                    />
                  ) : (
                    <span className="text-ink-40">–</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
