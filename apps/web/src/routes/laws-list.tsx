import { useInfiniteQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams, useSearchParams } from 'react-router';

import { CountryFlag } from '../vendor/tbi-ui';

import { LawRow } from '@/components/laws/law-row';
import { Skeleton } from '@/components/ui/skeleton';
import { listLaws } from '@/lib/api-misc';
import { countryName } from '@/lib/countries';
import type { LawSummary } from '@/lib/types';

const PAGE_LIMIT = 25;

/* Offset-paged over the server's list; no facets, the server computes none. */
export default function LawsCorpusPage() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const { code } = useParams<{ code: string }>();
  const doctype = params.get('doctype') ?? undefined;
  const year = params.get('year') ? Number.parseInt(params.get('year')!, 10) : undefined;
  const q = params.get('q') ?? undefined;
  const showAllLaws = params.get('view') === 'all';

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

  const query = useInfiniteQuery({
    queryKey: ['laws', showAllLaws ? null : code, doctype, year, q],
    queryFn: ({ pageParam }) =>
      listLaws({
        limit: PAGE_LIMIT,
        offset: pageParam,
        jurisdiction: showAllLaws || !code ? undefined : code,
        doctype,
        year,
        q,
      }),
    initialPageParam: 0,
    getNextPageParam: (lastPage) =>
      lastPage.items.length === PAGE_LIMIT ? lastPage.offset + PAGE_LIMIT : undefined,
  });

  const items = useMemo(() => query.data?.pages.flatMap((page) => page.items) ?? [], [query.data]);

  const grouped = useMemo(() => {
    if (!showAllLaws) return null;
    const out = new Map<string, LawSummary[]>();
    for (const l of items) {
      const list = out.get(l.jurisdiction_code) ?? [];
      list.push(l);
      out.set(l.jurisdiction_code, list);
    }
    return [...out.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [items, showAllLaws]);

  return (
    <div className="mx-auto max-w-4xl space-y-6 px-6 py-10">
      <header className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div className="space-y-1">
            <p className="micro-caps text-ink-60">{t('Corpus')}</p>
            <h1 className="font-tbi-sans text-2xl font-semibold tracking-[-0.01em] text-ink">
              {t('Laws')}
            </h1>
          </div>
          <label className="inline-flex cursor-pointer items-center gap-2 text-sm font-medium text-ink">
            <span>{t('Show all laws')}</span>
            <input
              type="checkbox"
              checked={showAllLaws}
              onChange={() => patch({ view: showAllLaws ? undefined : 'all' })}
              className="peer sr-only"
            />
            <span
              aria-hidden="true"
              className="relative h-5 w-9 rounded-full bg-ink-20 transition-colors peer-checked:bg-emphasis-strong after:absolute after:start-0.5 after:top-0.5 after:h-4 after:w-4 after:rounded-full after:bg-paper after:transition-transform peer-checked:after:translate-x-4"
            />
          </label>
        </div>
        <input
          type="search"
          defaultValue={q ?? ''}
          placeholder={t('Filter by title')}
          onChange={(e) => patch({ q: e.target.value || undefined })}
          className="h-9 w-full rounded-lg border border-ink-20 bg-paper px-3 text-sm text-ink placeholder:text-ink-40"
        />
        {items.length > 0 ? (
          <p className="text-sm text-ink-60">{t('{{count}} laws loaded', { count: items.length })}</p>
        ) : null}
      </header>

      <div className="min-w-0 space-y-3">
        {query.isError ? (
          <p role="alert" className="border-t border-ink-20 py-6 text-sm text-destructive">
            {query.error instanceof Error ? query.error.message : t('Could not load laws.')}
          </p>
        ) : query.isLoading ? (
          <ul role="list" className="border-t border-ink-20">
            {Array.from({ length: 5 }).map((_, i) => (
              <li key={i} className="border-b border-ink-20 py-3">
                <Skeleton className="h-4 w-2/3" />
                <Skeleton className="mt-1.5 h-3 w-1/3" />
              </li>
            ))}
          </ul>
        ) : items.length === 0 ? (
          <p role="status" className="border-t border-ink-20 py-6 text-sm text-ink-60">
            {t('No laws match the current filter.')}
          </p>
        ) : grouped ? (
          <div className="space-y-6">
            {grouped.map(([jurisdiction, laws]) => (
              <section key={jurisdiction} className="space-y-2">
                <h2 className="flex items-center gap-2 font-tbi-sans text-xs font-semibold uppercase tracking-wide text-ink-60">
                  <CountryFlag code={jurisdiction} size="sm" aria-hidden />
                  <span>{countryName(jurisdiction) ?? jurisdiction.toUpperCase()}</span>
                </h2>
                <ul role="list" className="border-t border-ink-20">
                  {laws.map((law) => (
                    <LawRow key={law.id} law={law} />
                  ))}
                </ul>
              </section>
            ))}
          </div>
        ) : (
          <ul role="list" className="border-t border-ink-20">
            {items.map((law) => (
              <LawRow key={law.id} law={law} showFlag />
            ))}
          </ul>
        )}
        {!query.isError && !query.isLoading && query.hasNextPage ? (
          <div className="flex justify-center py-4">
            <button
              type="button"
              onClick={() => void query.fetchNextPage()}
              disabled={query.isFetchingNextPage}
              className="rounded-lg border border-ink-20 px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:bg-paper-sunken disabled:opacity-50"
            >
              {query.isFetchingNextPage ? t('Loading…') : t('Load more')}
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
