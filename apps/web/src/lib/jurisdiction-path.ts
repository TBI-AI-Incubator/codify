
export const FALLBACK_COUNTRY = 'gb';

export function resolveCountry(pin: string | null, jurisdictions: string[]): string {
  const unrestricted = jurisdictions.includes('*');
  if (pin && (unrestricted || jurisdictions.includes(pin))) return pin;
  return jurisdictions.find((c) => c !== '*') ?? FALLBACK_COUNTRY;
}

export function parseJurisdictionPath(pathname: string): { code: string; rest: string } | null {
  const m = pathname.match(/^\/jurisdictions\/([^/]+)(?:\/(.*))?$/);
  if (!m) return null;
  return { code: m[1]!, rest: m[2] ?? '' };
}

const RESCOPEABLE = new Set([
  '',
  'laws',
  'assessment',
  'lenses/eu-acquis',
  'lenses/anticorruption',
  'lenses/anticorruption/examinations',
]);

export function rescopePath(
  location: { pathname: string; search: string },
  newCode: string,
): string | null {
  const parsed = parseJurisdictionPath(location.pathname);
  if (parsed) {
    if (RESCOPEABLE.has(parsed.rest))
      return `/jurisdictions/${newCode}/${parsed.rest}`.replace(/\/$/, '') + location.search;
    if (parsed.rest.startsWith('laws/')) return `/jurisdictions/${newCode}/laws`;
    if (parsed.rest.startsWith('chapters/')) return `/jurisdictions/${newCode}/lenses/eu-acquis`;
    return `/jurisdictions/${newCode}`;
  }
  if (location.pathname === '/laws' || location.pathname === '/recent') {
    const params = new URLSearchParams(location.search);
    params.delete('jurisdictions');
    return `${location.pathname}?${params.toString()}`;
  }
  if (location.pathname === '/search') {
    const params = new URLSearchParams(location.search);
    params.set('jurisdiction', newCode);
    return `${location.pathname}?${params.toString()}`;
  }
  if (location.pathname === '/ingest') {
    const params = new URLSearchParams(location.search);
    params.set('jurisdiction', newCode);
    return `${location.pathname}?${params.toString()}`;
  }
  return null;
}
