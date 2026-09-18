
import { useInfiniteQuery } from '@tanstack/react-query';
import { CountryFlag } from '@codify/tbi-ui';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams, useSearchParams } from 'react-router';

import { FacetBlock } from '@/components/facets/facet-block';
import { YearDecadesFacet } from '@/components/facets/year-decades';
import { LawRow } from '@/components/laws/law-row';
import { Skeleton } from '@/components/ui/skeleton';
import { listLawsCorpus } from '@/lib/api-misc';
import { countryName } from '@/lib/countries';
import { titleCase } from '@/lib/utils';
import type { LawSummary } from '@/lib/types';

const STATUS = [
  { value: 'enacted', label: 'Enacted' },
  { value: 'draft', label: 'Draft' },
] as const;

const PAGE_LIMIT = 25;

export default function LawsCorpusPage() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const { code } = useParams<{ code: string }>();
  const doctype = params.get('doctype') ?? undefined;
  const year = params.get('year') ? Number.parseInt(params.get('year')!, 10) : undefined;
  const status = (params.get('status') as 'draft' | 'enacted' | null) ?? undefined;
  const showAllLaws = params.get('view') === 'all';
  const groupBy = showAllLaws && params.get('group') === 'jurisdiction';

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
    queryKey: ['laws-corpus', showAllLaws ? null : code, doctype, year, status],
    queryFn: ({ pageParam }) =>
      listLawsCorpus({
        limit: PAGE_LIMIT,
        cursor: pageParam,
        jurisdictions: showAllLaws || !code ? undefined : [code],
        include_facets: true,
        ...(doctype ? { doctype } : {}),
        ...(status ? { status } : {}),
        ...(year ? { year } : {}),
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });

  const items = useMemo(() => query.data?.pages.flatMap((page) => page.items) ?? [], [query.data]);
  const visible = items;

  const grouped = useMemo(() => {
    if (!groupBy) return null;
    const out = new Map<string, LawSummary[]>();
    for (const l of visible) {
      const list = out.get(l.jurisdiction_code) ?? [];
      list.push(l);
      out.set(l.jurisdiction_code, list);
    }
    return [...out.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [visible, groupBy]);

  const doctypeFacets = query.data?.pages[0]?.facets?.doctype ?? [];
  const yearFacets = query.data?.pages[0]?.facets?.year ?? [];

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-6 py-10">
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
              onChange={() =>
                patch({
                  view: showAllLaws ? undefined : 'all',
                  group: showAllLaws ? undefined : 'jurisdiction',
                  jurisdictions: undefined,
                })
              }
              className="peer sr-only"
            />
            <span
              aria-hidden="true"
              className="relative h-5 w-9 rounded-full bg-ink-20 transition-colors peer-checked:bg-emphasis-strong after:absolute after:start-0.5 after:top-0.5 after:h-4 after:w-4 after:rounded-full after:bg-paper after:transition-transform peer-checked:after:translate-x-4"
            />
          </label>
          {/* The checkbox is the control; its visual track is intentionally decorative. */}
          <span className="sr-only" aria-live="polite">
            {showAllLaws ? t('Showing all laws') : t('Showing this jurisdiction only')}
          </span>
        </div>
        {items.length > 0 ? (
          <p className="text-sm text-ink-60">
            {t('{{count}} laws loaded', { count: items.length })}
          </p>
        ) : null}
      </header>

      <div className="grid gap-8 md:grid-cols-[minmax(0,1fr)_16rem]">
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
          ) : visible.length === 0 ? (
            <div className="border-t border-ink-20 py-6">
              <p role="status" aria-live="polite" className="text-sm text-ink-60">
                {t('No laws match the current filter.')}
              </p>
              {doctype || year || status ? (
                <button
                  type="button"
                  onClick={() => {
                    setParams(
                      (prev) => {
                        const p = new URLSearchParams(prev);
                        p.delete('doctype');
                        p.delete('year');
                        p.delete('status');
                        p.delete('has_findings');
                        return p;
                      },
                      { replace: true },
                    );
                  }}
                  className="mt-2 text-xs font-medium text-emphasis-strong hover:text-ink"
                >
                  {t('Clear filters')}
                </button>
              ) : null}
            </div>
          ) : grouped ? (
            <div className="space-y-6">
              {grouped.map(([code, laws]) => (
                <section key={code} className="space-y-2">
                  <h2 className="flex items-center gap-2 font-tbi-sans text-xs font-semibold uppercase tracking-wide text-ink-60">
                    <CountryFlag code={code} size="sm" aria-hidden />
                    <span>{countryName(code) ?? code.toUpperCase()}</span>
                    <span className="text-ink-40">·</span>
                    <span className="tabular-nums text-ink-40">
                      {t('{{count}} on this page', { count: laws.length })}
                    </span>
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
              {visible.map((law) => (
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

        <aside className="space-y-5 md:sticky md:top-6 md:self-start">
          <FacetBlock
            heading={t('Type')}
            options={doctypeFacets.map((f) => ({
              value: f.value,
              label: titleCase(f.value),
              count: f.count,
            }))}
            selected={doctype}
            onChange={(v) => patch({ doctype: v })}
          />
          <FacetBlock
            heading={t('Status')}
            options={STATUS.map((s) => ({ value: s.value, label: t(s.label) }))}
            selected={status}
            onChange={(v) => patch({ status: v })}
          />
          <YearDecadesFacet
            heading={t('Year')}
            years={yearFacets}
            selected={year}
            onChange={(v) => patch({ year: v !== undefined ? String(v) : undefined })}
          />
        </aside>
      </div>
    </div>
  );
}
