import { Link, useLocation, useNavigate, useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { ArrowUpRight } from 'lucide-react';

import { IngestProgress } from '@/components/upload/ingest-progress';
import { useRun } from '@/hooks/use-run';
import { useJurisdiction } from '@/lib/jurisdiction-context';
import { deriveIngestView } from '@/lib/run-views/ingest';
import { retryRun } from '@/lib/api-runs';
import { latestPart } from '@/lib/run-parts';

export default function RunRoute() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const { id = '' } = useParams<{ id: string }>();
  const run = useRun(id);
  const { country: pinnedCountry } = useJurisdiction();
  const searchParams = new URLSearchParams(location.search);
  const country = searchParams.get('jurisdiction') ?? pinnedCountry;
  const view = deriveIngestView(run.parts, t);
  const sourceLabel = searchParams.get('source') ?? id;
  const canRetry = latestPart(run.parts, 'run')?.status === 'failed';
  const retry = async () => {
    const created = await retryRun(id);
    navigate(
      `/runs/${created.run_id}?source=${encodeURIComponent(sourceLabel)}&jurisdiction=${country}`,
    );
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-6 py-10">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Upload</h1>
        <p className="text-sm text-ink-60">
          {country} · <span className="font-mono text-xs">{id}</span>
        </p>
      </header>
      <IngestProgress
        view={view}
        status={run.status}
        errorText={run.errorText}
        isReconnecting={run.isReconnecting}
        jurisdictionCode={country}
        sourceLabel={sourceLabel}
        onComplete={(lawId) => navigate(`/laws/${lawId}`)}
        onRetry={canRetry ? () => void retry() : undefined}
      />
      <div className="flex justify-end">
        <Link
          to={`/runs/${id}`}
          className="inline-flex items-center gap-1 font-mono text-[0.6875rem] uppercase tracking-wide text-ink-60 hover:text-ink"
        >
          Local run · open in monitor
          <ArrowUpRight className="size-3" aria-hidden />
        </Link>
      </div>
    </div>
  );
}
