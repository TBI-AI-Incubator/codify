import type { LawSummary } from './types';

export function coverageCount(
  coverage: LawSummary['lens_coverage'] | undefined,
  lens: string,
): number {
  const entry = (coverage ?? {})[lens];
  if (!entry || typeof entry !== 'object') return 0;
  const value = (entry as Record<string, unknown>).finding_count;
  return typeof value === 'number' ? value : 0;
}
