import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router';

import { JurisdictionTable } from '@/components/corpus/jurisdiction-table';
import { WorldMap } from '@/components/corpus/world-map';
import { getCorpusSummary } from '@/lib/api-misc';
import { useState } from 'react';

export default function JurisdictionsRoute() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['corpus-summary'],
    queryFn: getCorpusSummary,
    staleTime: 5 * 60 * 1000,
  });
  const navigate = useNavigate();
  const [tradFilter, setTradFilter] = useState('');

  if (isLoading) return <p className="p-6 text-sm text-ink-70">Loading…</p>;
  if (error) {
    return (
      <p role="alert" className="p-6 text-sm text-destructive">
        {error.message}
      </p>
    );
  }

  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      <h1 className="mb-1 text-xl font-semibold tracking-tight text-ink">Jurisdictions</h1>
      <p className="mb-6 text-sm text-ink-60">
        {data?.total ?? 0} jurisdictions · {data?.total_documents_examined ?? 0} documents
      </p>
      <section className="mb-12">
        <WorldMap
          jurisdictions={data?.jurisdictions ?? []}
          bodies={data?.bodies ?? []}
          selectedBody=""
          onSelect={(code) => navigate(`/jurisdictions/${code}`)}
        />
      </section>
      <JurisdictionTable
        jurisdictions={data?.jurisdictions ?? []}
        onSelect={(code) => navigate(`/jurisdictions/${code}`)}
        tradFilter={tradFilter}
        onTradFilterChange={setTradFilter}
      />
    </div>
  );
}
