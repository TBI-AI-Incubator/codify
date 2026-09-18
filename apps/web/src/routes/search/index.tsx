import { useQuery } from '@tanstack/react-query';
import { FileText, Search, X } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useSearchParams } from 'react-router';

import { cn } from '../../vendor/tbi-ui';

import { ProvisionPreview, type PreviewableProvision } from '@/components/law/provision-preview';
import { Skeleton } from '@/components/ui/skeleton';
import { searchProvisions, type SearchMatch } from '@/lib/api-misc';
import { aknEidLabel } from '@/lib/citation';
import { useJurisdiction } from '@/lib/jurisdiction-context';
import { useAllowedJurisdictions } from '@/lib/use-allowed-jurisdictions';

const FOCUS_RING =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background';

/* Hybrid search over one jurisdiction's provisions, best first. */
export default function SearchPage() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const { country } = useJurisdiction();
  const { items: jurisdictions } = useAllowedJurisdictions();
  const q = params.get('q') ?? '';
  const jurisdiction = params.get('jurisdiction') ?? country;
  const [preview, setPreview] = useState<PreviewableProvision | null>(null);

  const results = useQuery({
    queryKey: ['search', q, jurisdiction],
    queryFn: () => searchProvisions({ q, jurisdiction, k: 25 }),
    enabled: q.length > 0,
  });

  const patch = (next: Record<string, string | undefined>) => {
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        for (const [k, v] of Object.entries(next)) {
          if (v) p.set(k, v);
          else p.delete(k);
        }
        return p;
      },
      { replace: true },
    );
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-6 py-10">
      <header className="space-y-3">
        <h1 className="font-tbi-sans text-2xl font-semibold tracking-[-0.01em] text-ink">
          {t('Search')}
        </h1>
        <SearchBar defaultValue={q} onSubmit={(term) => patch({ q: term })} onClear={() => patch({ q: undefined })} />
        <label className="flex items-center gap-2 text-sm text-ink-60">
          {t('Jurisdiction')}
          <select
            value={jurisdiction}
            onChange={(e) => patch({ jurisdiction: e.target.value })}
            className="h-8 rounded-lg border border-ink-20 bg-paper px-2 text-sm text-ink"
          >
            {jurisdictions.map((j) => (
              <option key={j.code} value={j.code}>
                {j.name}
              </option>
            ))}
          </select>
        </label>
      </header>

      {!q ? (
        <p className="text-sm text-ink-60">{t('Search legislation by meaning or wording…')}</p>
      ) : results.isLoading ? (
        <ul className="divide-y divide-ink-20">
          {Array.from({ length: 5 }).map((_, i) => (
            <li key={i} className="py-3">
              <Skeleton className="h-4 w-1/3" />
              <Skeleton className="mt-1.5 h-3 w-full" />
            </li>
          ))}
        </ul>
      ) : results.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {results.error.message}
        </p>
      ) : results.data?.matches.length === 0 ? (
        <p role="status" className="text-sm text-ink-60">
          {t('No provisions match.')}
        </p>
      ) : (
        <ul className="divide-y divide-ink-20 rounded-xl border border-ink-20">
          {results.data?.matches.map((m) => (
            <li key={m.provision_id}>
              <MatchRow match={m} query={q} onOpen={() => setPreview(toPreviewable(m))} />
            </li>
          ))}
        </ul>
      )}

      <ProvisionPreview provision={preview} onClose={() => setPreview(null)} />
    </div>
  );
}

function toPreviewable(m: SearchMatch): PreviewableProvision {
  return {
    version_id: m.version_id,
    law_id: m.law_id,
    akn_eid: m.eid,
    law_title: m.law_title,
    frbr_work_uri: m.work_uri,
    jurisdiction_code: m.jurisdiction,
  };
}

function MatchRow({ match, query, onOpen }: { match: SearchMatch; query: string; onOpen: () => void }) {
  return (
    <div className="flex items-start gap-3 px-4 py-3">
      <FileText aria-hidden className="mt-0.5 size-4 shrink-0 text-ink-40" />
      <div className="min-w-0 flex-1">
        <button type="button" onClick={onOpen} className={cn('text-start', FOCUS_RING)}>
          <span className="font-tbi-sans text-sm font-medium text-ink">{aknEidLabel(match.eid)}</span>
          <span className="text-ink-40"> · </span>
          <span className="text-sm text-ink-70">{match.law_title}</span>
        </button>
        <p className="mt-0.5 line-clamp-3 font-legislative text-sm leading-relaxed text-ink-80">
          {highlight(match.text, query)}
        </p>
        <Link
          to={`/laws/${match.law_id}?v=${match.version_id}#${encodeURIComponent(match.eid)}`}
          className="mt-1 inline-block text-xs text-ink-60 hover:underline"
        >
          Open in reader
        </Link>
      </div>
    </div>
  );
}

function SearchBar({
  defaultValue,
  onSubmit,
  onClear,
}: {
  defaultValue: string;
  onSubmit: (term: string) => void;
  onClear: () => void;
}) {
  const { t } = useTranslation();
  const [value, setValue] = useState(defaultValue);
  return (
    <form
      role="search"
      className="relative"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit(value.trim());
      }}
    >
      <Search aria-hidden className="pointer-events-none absolute start-4 top-1/2 size-5 -translate-y-1/2 text-ink-40" />
      <input
        type="search"
        aria-label={t('Search the corpus')}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={t('Search legislation by meaning or wording…')}
        className="h-12 w-full rounded-xl border border-ink-20 bg-paper ps-12 pe-24 font-tbi-sans text-base text-ink shadow-sm outline-none transition-colors placeholder:text-ink-40 hover:border-ink-40 focus-visible:border-emphasis focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background [&::-webkit-search-cancel-button]:hidden"
      />
      {value ? (
        <button
          type="button"
          aria-label={t('Clear search')}
          onClick={() => {
            setValue('');
            onClear();
          }}
          className={cn('absolute end-[5.5rem] top-1/2 grid size-7 -translate-y-1/2 place-items-center rounded-full text-ink-40 transition-colors hover:bg-paper-sunken hover:text-ink', FOCUS_RING)}
        >
          <X className="size-4" />
        </button>
      ) : null}
      <button
        type="submit"
        className={cn('absolute end-2 top-1/2 h-9 -translate-y-1/2 rounded-lg bg-emphasis-strong px-4 font-tbi-sans text-sm font-medium text-paper transition-colors hover:bg-emphasis', FOCUS_RING)}
      >
        {t('Search')}
      </button>
    </form>
  );
}

function highlight(text: string, query: string) {
  const rawTokens = query.toLowerCase().split(/\s+/).filter((t) => t.length > 1);
  if (rawTokens.length === 0) return text;
  const tokenSet = new Set(rawTokens);
  const escaped = rawTokens.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const re = new RegExp(`(${escaped.join('|')})`, 'gi');
  return text.split(re).map((part, i) =>
    tokenSet.has(part.toLowerCase()) ? (
      <mark key={i} className="rounded-sm bg-transparent font-semibold text-emphasis-strong">
        {part}
      </mark>
    ) : (
      part
    ),
  );
}
