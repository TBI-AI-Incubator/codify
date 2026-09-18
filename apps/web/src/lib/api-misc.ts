import { fetchJson } from '@/lib/api';
import type { components } from '@/lib/contract';

export type LawPage = components['schemas']['LawPage'];
export type LawSummary = components['schemas']['LawSummary'];
export type SearchResult = components['schemas']['SearchResult'];
export type SearchMatch = components['schemas']['SearchMatch'];

export function listLaws(
  opts: { jurisdiction?: string; doctype?: string; year?: number; q?: string; limit?: number; offset?: number } = {},
): Promise<LawPage> {
  return fetchJson('/laws', { query: opts });
}

export function searchProvisions(opts: {
  q: string;
  jurisdiction: string;
  language?: string;
  k?: number;
}): Promise<SearchResult> {
  return fetchJson('/search', { query: opts });
}
