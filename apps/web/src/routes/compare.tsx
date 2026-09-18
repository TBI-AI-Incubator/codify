import { LawReader, type VersionDocument as SteleVersionDocument } from '../vendor/stele';
import { useQuery } from '@tanstack/react-query';
import { useRef } from 'react';
import { Link, useParams, useSearchParams } from 'react-router';

import { BluebellSourceView } from '@/components/bluebell-source-view';
import { getLaw, getVersionDocument, getVersionSource, listLawVersions } from '@/lib/api-laws';

export default function CompareRoute() {
  const { id = '' } = useParams<{ id: string }>();
  const [params] = useSearchParams();
  const previewRef = useRef<HTMLDivElement>(null);
  const requestedVersionId = params.get('v');

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

  const requestedVersionValid =
    !requestedVersionId || versions.data?.items.some((v) => v.id === requestedVersionId);
  const versionId = requestedVersionId
    ? requestedVersionValid
      ? requestedVersionId
      : law.data?.latest_version?.id
    : law.data?.latest_version?.id;

  const doc = useQuery({
    queryKey: ['version-document', versionId],
    queryFn: () => getVersionDocument(versionId!),
    enabled: !!versionId,
  });
  const source = useQuery({
    queryKey: ['version-source', versionId],
    queryFn: () => getVersionSource(versionId!, 'bluebell'),
    enabled: !!versionId,
  });

  if (
    law.isLoading ||
    doc.isLoading ||
    source.isLoading ||
    (!!requestedVersionId && versions.isLoading)
  ) {
    return <p className="p-6 text-sm text-ink-70">Loading…</p>;
  }
  if (law.isError || doc.isError || source.isError) {
    return (
      <p role="alert" className="p-6 text-sm text-destructive">
        {(law.error ?? doc.error ?? source.error)?.message ?? 'Failed to load this version.'}
      </p>
    );
  }
  if (!doc.data || !source.data) {
    return <p className="p-6 text-sm text-ink-70">This law has no readable version yet.</p>;
  }

  const docData = doc.data as unknown as SteleVersionDocument;

  const syncPreviewFromSource = (ratio: number) => {
    const preview = previewRef.current;
    if (!preview) return;
    const max = Math.max(0, preview.scrollHeight - preview.clientHeight);
    preview.scrollTop = ratio * max;
  };

  return (
    <div className="flex h-[calc(100svh-3rem)] flex-col px-6 py-10">
      <div className="mb-6 flex shrink-0 items-baseline justify-between">
        <h1 className="text-xl font-semibold tracking-tight text-ink">{law.data?.title}</h1>
        <Link
          to={versionId ? `/laws/${id}?v=${versionId}` : `/laws/${id}`}
          className="text-sm text-ink-60 hover:underline"
        >
          Back to reader
        </Link>
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="min-h-0 overflow-auto">
          <BluebellSourceView content={source.data.content} onScrollRatio={syncPreviewFromSource} />
        </div>
        <div ref={previewRef} className="min-h-0 overflow-auto">
          <LawReader
            document={docData}
            chapterLabel={(num) => `Chapter ${num}`}
            enactingFormulaLabel="Enacting formula"
            copyCitationLabel={(citation) => `Copy citation ${citation}`}
            copyLabel={(citation) => `Copy ${citation}`}
            copiedLabel="Copied"
          />
        </div>
      </div>
    </div>
  );
}
