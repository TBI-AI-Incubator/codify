
import { ISO1_TO_ISO3 } from '@codify/core-ts';

import type { VersionSummary } from '@codify/core-ts/api';

const REGIONAL_FALLBACK: Record<string, string> = {
  jv: 'ind',
  su: 'ind',
  ban: 'ind',
  min: 'ind',
  ace: 'ind',
};

export function preferredExpressionLanguage(locale: string): string | null {
  const primary = locale.split('-')[0]?.toLowerCase() ?? '';
  return ISO1_TO_ISO3[primary] ?? REGIONAL_FALLBACK[primary] ?? null;
}

export function pickExpression(
  versions: readonly VersionSummary[],
  language: string,
): VersionSummary | undefined {
  return versions.find((v) => v.language === language);
}
