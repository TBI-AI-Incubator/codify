import { CountryFlag, StatusPill, cn } from '../../vendor/tbi-ui';
import type {
  LawGroup,
  LawSummary,
  SearchFacet,
  SearchResultItem,
  TableRowResult,
} from '@codify/core-ts/api';
import { useQuery } from '@tanstack/react-query';
import { ArrowRight, ChevronLeft, ChevronRight, FileText, Search, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useSearchParams } from 'react-router';

import { FacetPill } from '@/components/facets/facet-pill';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { track } from '@/lib/analytics';
import { listLawsCorpus, searchProvisions } from '@/lib/api-misc';
import { aknEidLabel } from '@/lib/citation';
import { countryName } from '@/lib/countries';
import { formatDoctype } from '@/lib/doctype';
import { numberFromWorkUri } from '@/lib/law-citation';
import { useJurisdiction } from '@/lib/jurisdiction-context';
import { useAllowedJurisdictions } from '@/lib/use-allowed-jurisdictions';

import { ProvisionPreview } from '@/components/law/provision-preview';

const TAB_IDS = ['laws', 'text', 'tables'] as const;
type TabId = (typeof TAB_IDS)[number];

const LAWS_PER_PAGE = 10;
const ROWS_PER_PAGE = 20;

const FOCUS_RING =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background';

export default function SearchPage() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const q = params.get('q') ?? '';
  const jurisdiction = params.get('jurisdiction') ?? undefined;
  const language = params.get('language') ?? undefined;
  const doctype = params.get('doctype') ?? undefined;
  const aknType = params.get('akn') ?? undefined;
  const offset = Number(params.get('offset') ?? '0') || 0;
  const sort = params.get('sort') ?? undefined;
  const tabParam = params.get('tab');
  const pinnedTab = TAB_IDS.includes(tabParam as TabId) ? (tabParam as TabId) : null;

  const [preview, setPreview] = useState<SearchResultItem | null>(null);

  const rowsQuery = useQuery({
    queryKey: ['search-rows', q, jurisdiction, language, doctype, offset],
    queryFn: () =>
      searchProvisions({
        q,
        jurisdiction,
        language,
        doctype,
        includeTableRows: true,
        k: 1,
        offset,
        limit: ROWS_PER_PAGE,
      }),
    enabled: q.length > 0 && tabParam === 'tables',
  });
  const tableRows = rowsQuery.data?.table_rows ?? [];
  const rowsArm = rowsQuery.data ? (rowsQuery.data.table_rows_arm ?? null) : null;

  const { data, isFetching, error } = useQuery({
    queryKey: ['search', q, jurisdiction, language, doctype, aknType, offset, sort],
    queryFn: () =>
      searchProvisions({
        q,
        jurisdiction,
        language,
        doctype,
        aknType,
        group: 'law',
        offset,
        limit: LAWS_PER_PAGE,
        sort,
      }),
    enabled: q.length > 0 && (pinnedTab === null || pinnedTab === 'text'),
  });

  const lawsQuery = useQuery({
    queryKey: ['search-laws', q, jurisdiction, sort, offset],
    queryFn: () =>
      listLawsCorpus({
        q,
        jurisdictions: jurisdiction ? [jurisdiction] : undefined,
        sort,
        offset,
        limit: LAWS_PER_PAGE,
      }),
    enabled: q.length > 0,
  });

  const lawTotal = lawsQuery.data?.total ?? 0;
  const resolvedTab: TabId = pinnedTab
    ? pinnedTab
    : error
      ? 'text'
      : lawTotal > 0
        ? 'laws'
        : (data?.groups?.length ?? 0) > 0
          ? 'text'
          : 'laws';

  const arm = resolvedTab;
  let armFetching = isFetching;
  let armCount = data?.returned ?? 0;
  let armReady = data != null;
  let armFailed = error != null;
  let armUnreachable: boolean | null = data?.arms_fired
    ? !data.arms_fired.lexical && !data.arms_fired.vector
    : null;
  if (arm === 'tables') {
    armFetching = rowsQuery.isFetching;
    armCount = rowsQuery.data?.rows_returned ?? 0;
    armReady = rowsQuery.data != null;
    armFailed = rowsQuery.error != null;
    armUnreachable =
      rowsQuery.data?.table_rows_arm == null ? null : rowsQuery.data.table_rows_arm === false;
  } else if (arm === 'laws') {
    armFetching = lawsQuery.isFetching;
    armCount = lawsQuery.data?.total ?? 0;
    armReady = lawsQuery.data != null;
    armFailed = lawsQuery.error != null;
    armUnreachable = null;
  }

  const tabSettled =
    pinnedTab != null ||
    ((lawsQuery.data != null || lawsQuery.error != null) && (data != null || error != null));

  const reported = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!q || !tabSettled || armFetching || (!armReady && !armFailed)) return;
    const key = [q, jurisdiction, language, doctype, aknType, offset, arm].join('|');
    if (reported.current.has(key)) return;
    reported.current.add(key);
    track('search_performed', {
      query: q,
      query_length: q.length,
      arm,
      failed: armFailed,
      result_count: armFailed ? null : armCount,
      zero_results: armFailed ? null : armCount === 0,
      corpus_unreachable: armFailed ? null : armUnreachable,
      jurisdiction: jurisdiction ?? null,
      language: language ?? null,
      doctype: doctype ?? null,
      page: offset / (arm === 'tables' ? ROWS_PER_PAGE : LAWS_PER_PAGE),
    });
  }, [
    q,
    arm,
    armFetching,
    armReady,
    armFailed,
    tabSettled,
    armCount,
    armUnreachable,
    jurisdiction,
    language,
    doctype,
    aknType,
    offset,
  ]);

  const patch = (next: Record<string, string | undefined>) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) {
      if (v) p.set(k, v);
      else p.delete(k);
    }
    if (!('offset' in next)) p.delete('offset');
    setParams(p);
  };

  const { country } = useJurisdiction();
  const { items: allowedJur, isPending: jurPending } = useAllowedJurisdictions();
  const scopedOnce = useRef(false);
  useEffect(() => {
    if (scopedOnce.current || jurPending) return;
    scopedOnce.current = true;
    if (params.get('jurisdiction') || params.get('q') || !country) return;
    if (allowedJur.find((j) => j.code === country)?.has_ingested_data !== true) return;
    const p = new URLSearchParams(params);
    p.set('jurisdiction', country);
    setParams(p, { replace: true });
  }, [params, setParams, country, jurPending, allowedJur]);

  const scopedMiss =
    Boolean(jurisdiction) &&
    !isFetching &&
    data != null &&
    (data.returned ?? 0) === 0 &&
    q.length > 0;
  const fallback = useQuery({
    queryKey: ['search', q, undefined, language, doctype, aknType, 0],
    queryFn: () =>
      searchProvisions({
        q,
        language,
        doctype,
        aknType,
        group: 'law',
        offset: 0,
        limit: LAWS_PER_PAGE,
      }),
    enabled: scopedMiss,
  });

  const textPending = q.length > 0 && data == null && error == null;
  const groups = data?.groups ?? [];
  const returned = data?.returned ?? groups.length;
  const hasMore = data?.has_more ?? false;
  const arms = data?.arms_fired ?? null;
  const pageStart = offset + 1;
  const pageEnd = offset + returned;
  const showPager = offset > 0 || hasMore;
  const ceilingHit = returned === 0 && arms != null && !arms.lexical && !arms.vector;

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-6 py-10">
      <h1 className="sr-only">{t('Search the corpus')}</h1>
      <SearchBar
        defaultValue={q}
        onSubmit={(term) => patch({ q: term || undefined, offset: undefined })}
        onClear={() => patch({ q: undefined })}
      />

      {jurisdiction ? (
        <div className="flex items-center gap-2 text-sm text-ink-60">
          <span>{t('Searching within')}</span>
          <span className="inline-flex items-center gap-1.5 rounded-full border border-ink-20 bg-paper-sunken py-1 ps-2.5 pe-1 font-tbi-sans text-xs font-medium text-ink">
            <CountryFlag code={jurisdiction} size="xs" aria-hidden />
            {countryName(jurisdiction) ?? jurisdiction.toUpperCase()}
            <button
              type="button"
              aria-label={t('Search all jurisdictions')}
              onClick={() => patch({ jurisdiction: undefined })}
              className={cn(
                'grid size-6 place-items-center rounded-full text-ink-40 transition-colors hover:bg-ink-20 hover:text-ink',
                FOCUS_RING,
              )}
            >
              <X className="size-3.5" />
            </button>
          </span>
        </div>
      ) : null}

      {!q ? (
        <p className="py-16 text-center text-sm text-ink-60">
          {t(
            'Find a law by its name, or search what the law says. Try “companies” or “anti-corruption disclosure”.',
          )}
        </p>
      ) : (
        <Tabs
          value={resolvedTab}
          onValueChange={(value) => patch({ tab: String(value), offset: undefined })}
        >
          <TabsList variant="line" className="w-full justify-start gap-4 border-b border-ink-20">
            <TabsTrigger value="laws" className="flex-none">
              {t('Laws')}
              <span className="ms-1.5 tabular-nums text-ink-40">{lawTotal}</span>
            </TabsTrigger>
            {/* No count beside Text: the provision search returns a page, not a
                corpus total, and a page size sitting where a total sits reads as
                one. */}
            <TabsTrigger value="text" className="flex-none">
              {t('Text')}
            </TabsTrigger>
            {/* Same reason as Text carries no count: a page of rows is not a
                corpus total. `rows_has_more` says only whether another page
                exists, so the label stays bare. */}
            <TabsTrigger value="tables" className="flex-none">
              {t('Tables')}
            </TabsTrigger>
          </TabsList>

          <TabsContent
            value="laws"
            className="pt-5 duration-100 data-active:animate-in data-active:fade-in-0 motion-reduce:duration-0 motion-reduce:data-active:animate-none"
          >
            <LawsTab
              laws={lawsQuery.data?.items ?? []}
              total={lawTotal}
              isFetching={lawsQuery.isFetching}
              error={lawsQuery.error as Error | null}
              sort={sort}
              offset={offset}
              onSort={(next) => patch({ sort: next, offset: undefined })}
              onPage={(o) => patch({ offset: o ? String(o) : undefined })}
            />
          </TabsContent>

          <TabsContent
            value="text"
            className="space-y-6 pt-5 duration-100 data-active:animate-in data-active:fade-in-0 motion-reduce:duration-0 motion-reduce:data-active:animate-none"
          >
            <div className="flex items-center gap-2 text-sm text-ink-60">
              <span>{t('Show results in')}</span>
              <FacetPill selected={!language} onClick={() => patch({ language: undefined })}>
                {t('Original')}
              </FacetPill>
              <FacetPill selected={language === 'eng'} onClick={() => patch({ language: 'eng' })}>
                {t('English')}
              </FacetPill>
            </div>
            {/* No streamed quick answer here: a separate dependency chain, not core search. */}
            <div className="grid gap-8 md:grid-cols-[200px_1fr]">
              <FacetRail
                facets={data?.facets}
                jurisdiction={jurisdiction}
                doctype={doctype}
                aknType={aknType}
                onChange={patch}
              />

              <div className="min-w-0 space-y-4">
                <div className="flex items-baseline justify-between gap-3 text-sm text-ink-60">
                  <span aria-live="polite">
                    {isFetching
                      ? t('Searching…')
                      : returned === 0
                        ? t('No matching laws')
                        : offset === 0 && !hasMore
                          ? t('{{count}} laws', { count: returned })
                          : t('Laws {{start}}–{{end}}', { start: pageStart, end: pageEnd })}
                  </span>
                  <div className="flex items-center gap-3">
                    {showPager ? (
                      <span className="font-tbi-mono text-xs">
                        {pageStart}–{pageEnd}
                        {hasMore ? '+' : ''}
                      </span>
                    ) : null}
                    <SortSelect
                      sort={sort}
                      onSort={(next) => patch({ sort: next, offset: undefined })}
                    />
                  </div>
                </div>

                {error ? (
                  <p role="alert" className="text-sm text-destructive">
                    {(error as Error).message}
                  </p>
                ) : (isFetching || textPending) && groups.length === 0 ? (
                  <ResultsSkeleton />
                ) : ceilingHit ? (
                  <p className="py-12 text-center text-sm text-ink-60">
                    {t(
                      'No lexical or semantic match reached this query. A term in the corpus language, or an English phrasing of the concept, may surface it.',
                    )}
                  </p>
                ) : groups.length === 0 ? (
                  scopedMiss && (fallback.data?.returned ?? 0) > 0 ? (
                    <WidenScopeNudge
                      country={jurisdiction!}
                      onWiden={() => patch({ jurisdiction: undefined })}
                    />
                  ) : (
                    <EmptyState
                      hasFilters={Boolean(jurisdiction || doctype || aknType)}
                      onClearFilters={() =>
                        patch({ jurisdiction: undefined, doctype: undefined, akn: undefined })
                      }
                    />
                  )
                ) : (
                  <ul className="space-y-4">
                    {groups.map((g, rank) => (
                      <li key={g.law_id}>
                        <LawCard
                          group={g}
                          query={q}
                          onOpen={(provision) => {
                            track('search_result_opened', {
                              jurisdiction: jurisdiction ?? null,
                              rank: offset + rank,
                            });
                            setPreview(provision);
                          }}
                        />
                      </li>
                    ))}
                  </ul>
                )}

                {showPager ? (
                  <Pager
                    offset={offset}
                    hasMore={hasMore}
                    onPage={(o) => patch({ offset: o ? String(o) : undefined })}
                  />
                ) : null}
              </div>
            </div>
          </TabsContent>

          <TabsContent
            value="tables"
            className="space-y-4 pt-5 duration-100 data-active:animate-in data-active:fade-in-0 motion-reduce:duration-0 motion-reduce:data-active:animate-none"
          >
            <p className="text-sm text-ink-60" aria-live="polite">
              {rowsQuery.isFetching
                ? t('Searching…')
                : rowsArm === false || rowsQuery.error
                  ? // The alert below says what happened; claiming "no rows"
                    null
                  : tableRows.length === 0
                    ? t('No matching table rows')
                    : offset === 0 && !rowsQuery.data?.rows_has_more
                      ? t('{{count}} rows', { count: tableRows.length })
                      : t('Rows {{start}}–{{end}}', {
                          start: offset + 1,
                          end: offset + tableRows.length,
                        })}
            </p>

            {rowsQuery.error ? (
              <p role="alert" className="text-sm text-destructive">
                {(rowsQuery.error as Error).message}
              </p>
            ) : rowsArm === false ? (
              <p role="alert" className="text-sm text-destructive">
                {t('Table search is unavailable. The rows are stored; the index is not ready.')}
              </p>
            ) : null}

            <ul className="space-y-4">
              {tableRows.map((row) => (
                <li key={`${row.version_id}:${row.akn_eid}`}>
                  <TableRowCard row={row} />
                </li>
              ))}
            </ul>

            {offset > 0 || rowsQuery.data?.rows_has_more ? (
              <Pager
                offset={offset}
                pageSize={ROWS_PER_PAGE}
                hasMore={Boolean(rowsQuery.data?.rows_has_more)}
                onPage={(o) => patch({ offset: o ? String(o) : undefined })}
              />
            ) : null}
          </TabsContent>
        </Tabs>
      )}

      <ProvisionPreview provision={preview} onClose={() => setPreview(null)} />
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
      <Search
        aria-hidden
        className="pointer-events-none absolute start-4 top-1/2 size-5 -translate-y-1/2 text-ink-40"
      />
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
          className={cn(
            'absolute end-[5.5rem] top-1/2 grid size-7 -translate-y-1/2 place-items-center rounded-full text-ink-40 transition-colors hover:bg-paper-sunken hover:text-ink',
            FOCUS_RING,
          )}
        >
          <X className="size-4" />
        </button>
      ) : null}
      <button
        type="submit"
        className={cn(
          'absolute end-2 top-1/2 h-9 -translate-y-1/2 rounded-lg bg-emphasis-strong px-4 font-tbi-sans text-sm font-medium text-paper transition-colors hover:bg-emphasis',
          FOCUS_RING,
        )}
      >
        {t('Search')}
      </button>
    </form>
  );
}

function FacetRail({
  facets,
  jurisdiction,
  doctype,
  aknType,
  onChange,
}: {
  facets:
    | { jurisdiction?: SearchFacet[]; doctype?: SearchFacet[]; akn_type?: SearchFacet[] }
    | null
    | undefined;
  jurisdiction?: string;
  doctype?: string;
  aknType?: string;
  onChange: (next: Record<string, string | undefined>) => void;
}) {
  const { t } = useTranslation();
  if (!facets) return <div className="hidden md:block" />;

  const blocks: Array<{
    heading: string;
    key: string;
    facets: SearchFacet[] | undefined;
    selected?: string;
    flags?: boolean;
  }> = [
    {
      heading: t('Jurisdiction'),
      key: 'jurisdiction',
      facets: facets.jurisdiction,
      selected: jurisdiction,
      flags: true,
    },
    { heading: t('Type'), key: 'doctype', facets: facets.doctype, selected: doctype },
    { heading: t('Element'), key: 'akn', facets: facets.akn_type, selected: aknType },
  ];

  return (
    <aside className="space-y-5 md:sticky md:top-6 md:self-start">
      {blocks.map(({ heading, key, facets: fs, selected, flags }) =>
        (fs ?? []).length === 0 ? null : (
          <div key={key} className="space-y-2">
            <h2 className="font-tbi-sans text-xs font-semibold uppercase tracking-wide text-ink-60">
              {heading}
            </h2>
            <div className="flex flex-wrap gap-1.5">
              {(fs ?? []).map((f) => {
                const isSel = selected === f.value;
                return (
                  <FacetPill
                    key={f.value}
                    selected={isSel}
                    onClick={() => onChange({ [key]: isSel ? undefined : f.value })}
                  >
                    {flags ? <CountryFlag code={f.value} size="xs" aria-hidden /> : null}
                    <span>
                      {flags ? (countryName(f.value) ?? f.value.toUpperCase()) : titleCase(f.value)}
                    </span>
                    <span className={cn('tabular-nums', isSel ? 'text-paper' : 'text-ink-40')}>
                      {f.count}
                    </span>
                  </FacetPill>
                );
              })}
            </div>
          </div>
        ),
      )}
    </aside>
  );
}

function LawCard({
  group,
  query,
  onOpen,
}: {
  group: LawGroup;
  query: string;
  onOpen: (p: SearchResultItem) => void;
}) {
  const { t } = useTranslation();
  const hidden = group.match_count - group.provisions.length;
  const firstEid = group.provisions[0]?.akn_eid;
  const lawHref = `/jurisdictions/${group.jurisdiction_code}/laws/${group.law_id}${firstEid ? `#${firstEid}` : ''}`;
  const lawNumber = numberFromWorkUri(group.frbr_work_uri);
  return (
    <article className="overflow-hidden rounded-xl border border-ink-20 bg-paper">
      <header className="flex items-start gap-3 border-b border-ink-20 px-4 py-3">
        <CountryFlag
          code={group.jurisdiction_code}
          size="sm"
          className="mt-0.5"
          unknownLabel={(code) => t('Unrecognised country code: {{code}}', { code })}
        />
        <Link
          to={lawHref}
          className={cn(
            'min-w-0 flex-1 rounded-sm font-tbi-sans text-[0.95rem] font-semibold leading-snug text-ink hover:text-emphasis-strong',
            FOCUS_RING,
          )}
        >
          {group.law_short_title?.trim() || group.law_title}
        </Link>
        <StatusPill
          tone="emphasis"
          label={t(formatDoctype(group.doctype, group.jurisdiction_code))}
        />
        {lawNumber ? (
          <span className="whitespace-nowrap pt-0.5 font-tbi-mono text-xs text-ink-60">
            {lawNumber}
          </span>
        ) : null}
        <span className="whitespace-nowrap pt-0.5 font-tbi-mono text-xs text-ink-60">
          {t('{{count}} matches', { count: group.match_count })}
        </span>
      </header>
      <ul className="divide-y divide-ink-20/60">
        {group.provisions.map((p) => (
          <li key={p.provision_id}>
            <ProvisionRow provision={p} query={query} onOpen={onOpen} />
          </li>
        ))}
      </ul>
      {hidden > 0 ? (
        <Link
          to={lawHref}
          className={cn(
            'block border-t border-ink-20 px-4 py-2 text-center font-tbi-sans text-xs font-medium text-emphasis-strong hover:bg-paper-sunken',
            FOCUS_RING,
          )}
        >
          {t('{{count}} more matches in this law →', { count: hidden })}
        </Link>
      ) : null}
    </article>
  );
}

function TableRowCard({ row }: { row: TableRowResult }) {
  const { t } = useTranslation();
  const href = `/jurisdictions/${row.jurisdiction_code}/laws/${row.law_id}?v=${row.version_id}#${row.akn_eid}`;
  const cells = row.cells ?? [];
  const lineage = row.lineage ?? [];
  const labels = cells.map((c) => c.label).filter(Boolean);

  return (
    <article className="overflow-hidden rounded-xl border border-ink-20 bg-paper">
      <header className="flex items-start gap-3 border-b border-ink-20 px-4 py-3">
        <CountryFlag
          code={row.jurisdiction_code}
          size="sm"
          className="mt-0.5"
          unknownLabel={(code) => t('Unrecognised country code: {{code}}', { code })}
        />
        <Link
          to={href}
          className={cn(
            'min-w-0 flex-1 rounded-sm font-tbi-sans text-[0.95rem] font-semibold leading-snug text-ink hover:text-emphasis-strong',
            FOCUS_RING,
          )}
        >
          {row.law_title ?? row.frbr_work_uri}
        </Link>
      </header>

      {lineage.length > 0 ? (
        <p className="border-b border-ink-20/60 px-4 py-2 font-tbi-sans text-xs text-ink-60">
          {lineage.join(' › ')}
        </p>
      ) : null}

      {/* The row as its own two-row table. A cell reading "Article 12" says
          nothing until it sits under the column naming where it came from. */}
      <div className="overflow-x-auto px-4 py-3">
        {cells.length === 0 ? (
          <p className="text-sm text-ink">{row.text}</p>
        ) : (
          <table className="w-full border-collapse text-sm">
            <caption className="sr-only">
              {t('Row {{index}} of a table in {{law}}', {
                index: row.row_index,
                law: row.law_title ?? row.frbr_work_uri,
              })}
            </caption>
            {labels.length > 0 ? (
              <thead>
                <tr>
                  {cells.map((c, i) => (
                    <th
                      key={i}
                      scope="col"
                      className="border-b border-ink-20 pb-1 pe-4 text-start font-tbi-sans text-xs font-medium text-ink-60"
                    >
                      {c.label || t('Unnamed column')}
                    </th>
                  ))}
                </tr>
              </thead>
            ) : null}
            <tbody>
              <tr>
                {cells.map((c, i) => (
                  <td key={i} className="pe-4 pt-1 align-top text-ink">
                    {c.text}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        )}
        {labels.length === 0 && cells.length > 0 ? (
          <p className="pt-2 font-tbi-sans text-xs text-ink-60">
            {t('This table names no columns.')}
          </p>
        ) : null}
      </div>

      <Link
        to={href}
        className={cn(
          'block border-t border-ink-20 px-4 py-2 text-center font-tbi-sans text-xs font-medium text-emphasis-strong hover:bg-paper-sunken',
          FOCUS_RING,
        )}
      >
        {t('Open the table →')}
      </Link>
    </article>
  );
}

function ProvisionRow({
  provision,
  query,
  onOpen,
}: {
  provision: SearchResultItem;
  query: string;
  onOpen: (p: SearchResultItem) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen(provision)}
      className={cn(
        'flex w-full items-start gap-3 px-4 py-3 text-start transition-colors hover:bg-paper-sunken',
        FOCUS_RING,
      )}
    >
      <FileText aria-hidden className="mt-0.5 size-4 shrink-0 text-ink-40" />
      <div className="min-w-0 flex-1">
        <span className="font-tbi-sans text-sm font-medium text-ink">
          {aknEidLabel(provision.akn_eid)}
        </span>
        <p className="mt-0.5 line-clamp-2 font-legislative text-sm leading-relaxed text-ink-80">
          {highlight(provision.snippet, query)}
        </p>
      </div>
    </button>
  );
}

function Pager({
  offset,
  hasMore,
  onPage,
  pageSize = LAWS_PER_PAGE,
}: {
  offset: number;
  hasMore: boolean;
  onPage: (offset: number) => void;
  pageSize?: number;
}) {
  const { t } = useTranslation();
  const hasPrev = offset > 0;
  const hasNext = hasMore;
  const btn = cn(
    'inline-flex h-8 items-center gap-1 rounded-lg border border-ink-20 px-3 font-tbi-sans text-xs font-medium text-ink-80 transition-colors enabled:hover:bg-paper-sunken disabled:opacity-40',
    FOCUS_RING,
  );
  return (
    <div className="flex items-center justify-center gap-3 pt-2">
      <button
        type="button"
        className={btn}
        disabled={!hasPrev}
        onClick={() => onPage(Math.max(0, offset - pageSize))}
      >
        <ChevronLeft className="size-4 rtl:-scale-x-100" /> {t('Previous')}
      </button>
      <button
        type="button"
        className={btn}
        disabled={!hasNext}
        onClick={() => onPage(offset + pageSize)}
      >
        {t('Next')} <ChevronRight className="size-4 rtl:-scale-x-100" />
      </button>
    </div>
  );
}

function ResultsSkeleton() {
  return (
    <ul className="space-y-4">
      {[0, 1, 2].map((i) => (
        <li key={i} className="space-y-2 rounded-xl border border-ink-20 bg-paper p-4">
          <Skeleton className="h-5 w-2/3" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-5/6" />
        </li>
      ))}
    </ul>
  );
}

function EmptyState({
  hasFilters,
  onClearFilters,
}: {
  hasFilters: boolean;
  onClearFilters: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      role="status"
      aria-live="polite"
      className="rounded-xl border border-dashed border-ink-20 px-4 py-12 text-center"
    >
      <p className="font-tbi-sans text-sm text-ink-80">{t('No matching laws.')}</p>
      <p className="mt-1 text-xs text-ink-60">
        {hasFilters
          ? t('Try clearing a filter or broadening your query.')
          : t('Try different wording; search matches indexed legal terms.')}
      </p>
      {hasFilters ? (
        <button
          type="button"
          onClick={onClearFilters}
          className="mt-3 text-xs font-medium text-emphasis-strong hover:text-ink"
        >
          {t('Clear filters')}
        </button>
      ) : null}
    </div>
  );
}

function WidenScopeNudge({ country, onWiden }: { country: string; onWiden: () => void }) {
  const { t } = useTranslation();
  return (
    <div
      role="status"
      aria-live="polite"
      className="rounded-xl border border-dashed border-ink-20 px-4 py-12 text-center motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-100"
    >
      <p className="font-tbi-sans text-sm text-ink-80">
        {t('No matches in {{country}}.', {
          country: countryName(country) ?? country.toUpperCase(),
        })}
      </p>
      <button
        type="button"
        onClick={onWiden}
        className={cn(
          'mt-2 inline-flex items-center gap-1.5 text-sm font-medium text-emphasis-strong hover:text-ink',
          FOCUS_RING,
        )}
      >
        {t('Search all jurisdictions')}
        <ArrowRight className="size-4 rtl:-scale-x-100" aria-hidden />
      </button>
    </div>
  );
}

function highlight(text: string, query: string) {
  const rawTokens = query
    .toLowerCase()
    .split(/\s+/)
    .filter((t) => t.length > 1);
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

function titleCase(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

const SORTS = [
  { value: 'relevance', label: 'Relevance' },
  { value: 'title', label: 'Title A–Z' },
  { value: 'year_desc', label: 'Year, newest' },
  { value: 'year_asc', label: 'Year, oldest' },
  { value: 'ingested', label: 'Recently added' },
] as const;

function SortSelect({
  sort,
  onSort,
}: {
  sort?: string;
  onSort: (next: string | undefined) => void;
}) {
  const { t } = useTranslation();
  return (
    <label className="flex items-center gap-2">
      <span className="text-xs uppercase tracking-wide">{t('Sort')}</span>
      <select
        value={sort ?? 'relevance'}
        onChange={(e) => onSort(e.target.value === 'relevance' ? undefined : e.target.value)}
        className={cn(
          'h-8 rounded-md border border-ink-20 bg-paper px-2 font-tbi-sans text-sm text-ink',
          FOCUS_RING,
        )}
      >
        {SORTS.map((s) => (
          <option key={s.value} value={s.value}>
            {t(s.label)}
          </option>
        ))}
      </select>
    </label>
  );
}

function LawsTab({
  laws,
  total,
  isFetching,
  error,
  sort,
  offset,
  onSort,
  onPage,
}: {
  laws: LawSummary[];
  total: number;
  isFetching: boolean;
  error: Error | null;
  sort?: string;
  offset: number;
  onSort: (next: string | undefined) => void;
  onPage: (offset: number) => void;
}) {
  const { t } = useTranslation();
  const hasMore = offset + laws.length < total;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-3 text-sm text-ink-60">
        <span aria-live="polite">
          {isFetching
            ? t('Searching…')
            : total === 0
              ? t('No law of that name')
              : t('{{count}} laws', { count: total })}
        </span>
        <SortSelect sort={sort} onSort={onSort} />
      </div>

      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error.message}
        </p>
      ) : isFetching && laws.length === 0 ? (
        <ResultsSkeleton />
      ) : laws.length === 0 ? (
        <p className="py-12 text-center text-sm text-ink-60">
          {t('Nothing here answers to that name. The Text tab searches what laws say.')}
        </p>
      ) : (
        <ul className="divide-y divide-ink-20 border-y border-ink-20">
          {laws.map((law) => (
            <li key={law.id}>
              <Link
                to={`/jurisdictions/${law.jurisdiction_code}/laws/${law.id}`}
                className={cn(
                  'flex items-baseline gap-3 py-3 transition-colors hover:bg-paper-sunken',
                  FOCUS_RING,
                )}
              >
                <CountryFlag code={law.jurisdiction_code} size="xs" aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="block font-tbi-sans text-sm font-medium text-ink">
                    {law.short_title || law.title}
                  </span>
                  {/* The ingest date is the point: it is the answer to "is this
                      in the corpus", which people were re-uploading to find.
                      The number earns its place too: 130 Philippine laws share a
                      title with another, and RA 3951 and RA 3952 are the same
                      year, so without it two rows read as identical. */}
                  <span className="block text-xs text-ink-60">
                    {[
                      formatDoctype(law.doctype, law.jurisdiction_code),
                      law.number ? t('No. {{number}}', { number: law.number }) : null,
                      law.year ? String(law.year) : null,
                      law.latest_ingested_at
                        ? t('added {{date}}', {
                            date: new Date(law.latest_ingested_at).toLocaleDateString(),
                          })
                        : null,
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </span>
                </span>
                <ArrowRight className="size-4 shrink-0 text-ink-40" aria-hidden />
              </Link>
            </li>
          ))}
        </ul>
      )}

      {offset > 0 || hasMore ? <Pager offset={offset} hasMore={hasMore} onPage={onPage} /> : null}
    </div>
  );
}
