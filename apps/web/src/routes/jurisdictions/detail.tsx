import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router';

import { getJurisdiction } from '@/lib/api-jurisdictions';
import { listLaws } from '@/lib/api-misc';

export default function JurisdictionDetailRoute() {
  const { code = '' } = useParams<{ code: string }>();

  const config = useQuery({
    queryKey: ['jurisdiction', code],
    queryFn: () => getJurisdiction(code),
    enabled: !!code,
  });
  const laws = useQuery({
    queryKey: ['jurisdiction-laws', code],
    queryFn: () => listLaws({ jurisdiction: code, limit: 50 }),
    enabled: !!code,
  });

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="mb-1 text-xl font-semibold tracking-tight text-ink">
        {config.data?.name ?? code.toUpperCase()}
      </h1>
      <p className="mb-6 text-sm text-ink-60">
        {config.data ? `${config.data.type} · ${config.data.languages.join(', ')}` : ''}
      </p>

      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-60">Laws</h2>
      {laws.isLoading ? (
        <p className="text-sm text-ink-70">Loading…</p>
      ) : laws.error ? (
        <p role="alert" className="text-sm text-destructive">
          {laws.error.message}
        </p>
      ) : (
        <ul className="divide-y divide-ink-20">
          {(laws.data?.items ?? []).map((law) => (
            <li key={law.id} className="py-3">
              <Link to={`/laws/${law.id}`} className="text-sm font-medium text-ink hover:underline">
                {law.title}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
