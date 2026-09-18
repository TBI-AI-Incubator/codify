import { LawReader, type VersionDocument as SteleVersionDocument } from '../vendor/stele';
import { useQuery } from '@tanstack/react-query';
import { useLocation, useParams, useSearchParams } from 'react-router';

import { LawDownloadDialog } from '@/components/law/law-download-dialog';
import { getLaw, getVersionDocument, listLawVersions } from '@/lib/api-laws';

export default function LawReaderRoute() {
  const { id = '' } = useParams<{ id: string }>();
  const [params] = useSearchParams();
  const { hash } = useLocation();
  const requestedVersionId = params.get('v');
  const flashTarget = hash ? decodeURIComponent(hash.slice(1)) : null;

  const law = useQuery({
    queryKey: ['law', id],
    queryFn: () => getLaw(id),
    enabled: !!id,
  });

  const versions = useQuery({
    queryKey: ['law-versions', id],
    queryFn: () => listLawVersions(id, { limit: 200 }),
    enabled: !!id && !!requestedVersionId,
  });

  const latest = law.data?.versions[0] ?? null; // newest expression first
  const versionSummary = requestedVersionId
    ? (versions.data?.items.find((v) => v.id === requestedVersionId) ?? null)
    : latest;
  const versionMismatched = !!requestedVersionId && !versions.isLoading && versionSummary === null;
  const effectiveVersionSummary = versionMismatched ? latest : versionSummary;
  const versionId = effectiveVersionSummary?.id ?? undefined;

  const doc = useQuery({
    queryKey: ['version-document', versionId],
    queryFn: () => getVersionDocument(versionId!),
    enabled: !!versionId,
  });

  if (law.isLoading || doc.isLoading || (!!requestedVersionId && versions.isLoading)) {
    return <p className="p-6 text-sm text-ink-70">Loading…</p>;
  }
  if (law.isError || doc.isError) {
    return (
      <p role="alert" className="p-6 text-sm text-destructive">
        {(law.error ?? doc.error)?.message ?? 'Failed to load this law.'}
      </p>
    );
  }
  if (!law.data || !versionId || !doc.data) {
    return <p className="p-6 text-sm text-ink-70">This law has no readable version yet.</p>;
  }

  const docData = doc.data as unknown as SteleVersionDocument;

  return (
    <article className="mx-auto flex h-[calc(100svh-3rem)] max-w-3xl flex-col px-6 py-10">
      <div className="mb-6 flex shrink-0 items-baseline justify-between">
        <h1 className="text-xl font-semibold tracking-tight text-ink">{law.data.title}</h1>
        <div className="flex items-center gap-3">
          <button className="text-sm text-ink-60 hover:underline" onClick={() => window.print()}>
            Print
          </button>
          <LawDownloadDialog versionId={versionId} />
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <LawReader
          document={docData}
          flashTarget={flashTarget}
          chapterLabel={(num) => `Chapter ${num}`}
          enactingFormulaLabel="Enacting formula"
          copyCitationLabel={(citation) => `Copy citation ${citation}`}
          copyLabel={(citation) => `Copy ${citation}`}
          copiedLabel="Copied"
        />
      </div>
    </article>
  );
}
