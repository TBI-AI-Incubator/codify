import type { LawListResponse, SearchResponse } from '@codify/core-ts/api';

import { fetchJson } from '@/lib/api';
import { listJurisdictions } from '@/lib/api-jurisdictions';
import type { CorpusJurisdiction, CorpusSummary } from '@/lib/types';

export function listLawsCorpus(
  opts: {
    jurisdictions?: readonly string[];
    doctype?: string;
    status?: 'draft' | 'enacted';
    year?: number;
    cursor?: string;
    limit?: number;
    q?: string;
    sort?: string;
    offset?: number;
    include_facets?: boolean;
  } = {},
): Promise<LawListResponse> {
  return fetchJson('/laws', {
    query: {
      ...opts,
      jurisdictions:
        opts.jurisdictions && opts.jurisdictions.length > 0
          ? opts.jurisdictions.join(',')
          : undefined,
      include_facets: opts.include_facets ? 'true' : undefined,
    },
  });
}

export function searchProvisions(opts: {
  q: string;
  jurisdiction?: string;
  language?: string;
  lawId?: string;
  versionId?: string;
  k?: number;
  group?: 'law';
  offset?: number;
  limit?: number;
  doctype?: string;
  aknType?: string;
  sort?: string;
  includeTableRows?: boolean;
}): Promise<SearchResponse> {
  return fetchJson<SearchResponse>('/search', {
    query: {
      q: opts.q,
      jurisdiction: opts.jurisdiction,
      language: opts.language,
      law_id: opts.lawId,
      version_id: opts.versionId,
      k: opts.k,
      group: opts.group,
      offset: opts.offset,
      limit: opts.limit,
      doctype: opts.doctype,
      akn_type: opts.aknType,
      sort: opts.sort,
      include_table_rows: opts.includeTableRows ? 'true' : undefined,
    },
  });
}

export async function getCorpusSummary(): Promise<CorpusSummary> {
  const list = await listJurisdictions();
  const jurisdictions: CorpusJurisdiction[] = list.items.map((j) => ({
    code: j.code,
    name: j.name,
    type: j.type ?? null,
    tier: j.tier ?? 0,
    effective_tier: j.effective_tier ?? null,
    tradition: j.tradition ?? [],
    languages: j.languages ?? [],
    documents_examined: j.laws_count ?? 0,
    unresolved_ambiguities: 0,
    bluebell_tested: j.has_ingested_data ?? false,
  }));
  const total_documents_examined = jurisdictions.reduce((sum, j) => sum + j.documents_examined, 0);
  return {
    total: jurisdictions.length,
    total_documents_examined,
    jurisdictions,
    bodies: [],
  };
}
