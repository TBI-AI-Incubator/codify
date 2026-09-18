
export type { JurisdictionListItem, LawSummary } from '@codify/core-ts/api';

export interface CorpusJurisdiction {
  code: string;
  name: string;
  type: string | null;
  tier: number;
  effective_tier?: number | null;
  tradition: string[];
  languages: string[];
  documents_examined: number;
  unresolved_ambiguities: number;
  bluebell_tested: boolean;
  coordinates?: { lat: number; lng: number };
  continent?: string;
}

export interface CorpusBody {
  code: string;
  name: string;
  members: string[];
}

export interface CorpusSummary {
  total: number;
  total_documents_examined: number;
  jurisdictions: CorpusJurisdiction[];
  bodies: CorpusBody[];
}
