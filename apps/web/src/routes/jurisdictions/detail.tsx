import { useQuery } from '@tanstack/react-query';
import ReactMarkdown from 'react-markdown';
import { Link, useParams } from 'react-router';
import remarkGfm from 'remark-gfm';

import { getJurisdictionProfile } from '@/lib/api-jurisdictions';
import { listLawsCorpus } from '@/lib/api-misc';

export default function JurisdictionDetailRoute() {
  const { code = '' } = useParams<{ code: string }>();

  const profile = useQuery({
    queryKey: ['jurisdiction-profile', code],
    queryFn: () => getJurisdictionProfile(code),
    enabled: !!code,
  });
  const laws = useQuery({
    queryKey: ['jurisdiction-laws', code],
    queryFn: () => listLawsCorpus({ jurisdictions: [code], limit: 50 }),
    enabled: !!code,
  });

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="mb-6 text-xl font-semibold uppercase tracking-tight text-ink">{code}</h1>

      {profile.data?.profile_md ? (
        <article className="prose prose-sm mb-10 max-w-none text-ink">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{profile.data.profile_md}</ReactMarkdown>
        </article>
      ) : null}

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
