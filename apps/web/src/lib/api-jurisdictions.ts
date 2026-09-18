import { fetchJson } from '@/lib/api';
import type { components } from '@/lib/contract';

export type JurisdictionSummary = components['schemas']['JurisdictionSummary'];
export type JurisdictionConfig = components['schemas']['JurisdictionConfig'];

export function listJurisdictions(): Promise<JurisdictionSummary[]> {
  return fetchJson('/jurisdictions');
}

export function getJurisdiction(code: string): Promise<JurisdictionConfig> {
  return fetchJson(`/jurisdictions/${encodeURIComponent(code)}`);
}
