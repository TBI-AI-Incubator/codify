import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';

import { CountryFlag } from '../../vendor/tbi-ui';

import { listJurisdictions } from '@/lib/api-jurisdictions';

/* The jurisdictions the server ships, as a list; the map and tiers are the platform's. */
export default function JurisdictionsRoute() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['jurisdictions'],
    queryFn: listJurisdictions,
    staleTime: 5 * 60 * 1000,
  });

  if (isLoading) return <p className="p-6 text-sm text-ink-70">Loading…</p>;
  if (error) {
    return (
      <p role="alert" className="p-6 text-sm text-destructive">
        {error.message}
      </p>
    );
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="mb-1 text-xl font-semibold tracking-tight text-ink">Jurisdictions</h1>
      <p className="mb-6 text-sm text-ink-60">{data?.length ?? 0} configured</p>
      <ul className="divide-y divide-ink-20">
        {(data ?? []).map((j) => (
          <li key={j.code} className="flex items-center gap-3 py-3">
            <CountryFlag code={j.code} size="sm" />
            <Link to={`/jurisdictions/${j.code}/laws`} className="text-sm font-medium text-ink hover:underline">
              {j.name}
            </Link>
            <span className="ml-auto font-mono text-xs uppercase text-ink-60">{j.languages.join(' · ')}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
