import type { JurisdictionListResponse, JurisdictionProfile } from '@codify/core-ts/api';

import { fetchJson } from '@/lib/api';

export function listJurisdictions(): Promise<JurisdictionListResponse> {
  return fetchJson('/jurisdictions');
}

export function getJurisdictionProfile(code: string): Promise<JurisdictionProfile> {
  return fetchJson(`/jurisdictions/${encodeURIComponent(code)}/profile`);
}
