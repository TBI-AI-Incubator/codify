import { LawReader, type VersionDocument as SteleVersionDocument } from '../../vendor/stele';
import { Button, buttonVariants, cn, toast } from '../../vendor/tbi-ui';
import { useQuery } from '@tanstack/react-query';
import { ArrowUpRight, Copy } from 'lucide-react';
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { getVersionDocument, listLawVersions } from '@/lib/api-laws';
import { pickExpression, preferredExpressionLanguage } from '@/lib/expression-language';
import { languageName } from '@/lib/languages';
import { aknEidLabel, formatCitation } from '@/lib/citation';
export interface PreviewableProvision {
  version_id: string;
  law_id: string;
  akn_eid: string;
  law_title: string;
  frbr_work_uri: string;
  jurisdiction_code: string;
}

export function ProvisionPreview({
  provision,
  onClose,
}: {
  provision: PreviewableProvision | null;
  onClose: () => void;
}) {
  const open = provision !== null;
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" style={{ maxWidth: 'min(52rem, 94vw)' }} className="w-full gap-0">
        {provision ? <PreviewBody provision={provision} /> : null}
      </SheetContent>
    </Sheet>
  );
}

function PreviewBody({ provision }: { provision: PreviewableProvision }) {
  const { t, i18n } = useTranslation();
  const [copied, setCopied] = useState(false);
  const wanted = preferredExpressionLanguage(i18n.language);
  const versions = useQuery({
    queryKey: ['law-versions', provision.law_id],
    queryFn: () => listLawVersions(provision.law_id, { limit: 100 }),
    staleTime: 5 * 60 * 1000,
    enabled: wanted !== null,
  });
  const items = versions.data?.items ?? [];
  const source = items.find((v) => v.id === provision.version_id);
  const sibling =
    wanted && source && source.language !== wanted ? pickExpression(items, wanted) : undefined;
  const [showSource, setShowSource] = useState(false);

  const shownVersionId = sibling && !showSource ? sibling.id : provision.version_id;
  const onSourceExpression = shownVersionId === provision.version_id;
  const onFlashMissing = useCallback(
    (eid: string) =>
      toast.error(
        onSourceExpression
          ? t('Couldn’t locate {{eid}} in this version.', { eid })
          : t('This translation doesn’t carry {{eid}}. Switch to the original to read it.', {
              eid,
            }),
      ),
    [t, onSourceExpression],
  );
  const doc = useQuery({
    queryKey: ['version-document', shownVersionId],
    queryFn: () => getVersionDocument(shownVersionId),
  });

  const copyCitation = async () => {
    await navigator.clipboard.writeText(
      formatCitation(provision.frbr_work_uri, provision.law_title, provision.akn_eid),
    );
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  return (
    <>
      <SheetHeader className="border-b border-ink-20">
        <SheetTitle className="font-tbi-sans text-base">
          {aknEidLabel(provision.akn_eid)}
        </SheetTitle>
        <SheetDescription className="font-tbi-sans">
          {provision.law_title}
        </SheetDescription>
        {sibling && source ? (
          <div
            role="group"
            aria-label={t('Language')}
            className="mt-2 inline-flex w-fit items-center gap-1 rounded-lg bg-paper-sunken p-0.5 text-xs"
          >
            {[
              { id: sibling.id, label: languageName(sibling.language), src: false },
              { id: provision.version_id, label: languageName(source.language), src: true },
            ].map((option) => (
              <button
                key={option.id}
                type="button"
                aria-pressed={showSource === option.src}
                onClick={() => setShowSource(option.src)}
                className={cn(
                  'rounded-md px-2 py-1 transition-colors',
                  showSource === option.src
                    ? 'bg-paper font-medium text-ink shadow-sm'
                    : 'text-ink-60 hover:text-ink',
                )}
              >
                {option.label}
              </button>
            ))}
          </div>
        ) : null}
      </SheetHeader>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {doc.isLoading ? (
          <div className="space-y-3">
            <Skeleton className="h-5 w-2/3" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : doc.error ? (
          <p className="text-sm text-destructive">{t('Couldn’t load the law body.')}</p>
        ) : doc.data ? (
          <LawReader
            document={doc.data as unknown as SteleVersionDocument}
            flashTarget={provision.akn_eid}
            onFlashTargetMissing={onFlashMissing}
            chapterLabel={(num) => t('Chapter {{number}}', { number: num })}
            enactingFormulaLabel={t('Enacting formula')}
            copyCitationLabel={(citation) => t('Copy citation {{citation}}', { citation })}
            copyLabel={(citation) => t('Copy {{citation}}', { citation })}
            copiedLabel={t('Copied')}
          />
        ) : null}
      </div>

      <SheetFooter className="flex-row items-center justify-between border-t border-ink-20">
        <Button variant="ghost" size="sm" onClick={copyCitation}>
          <Copy />
          {copied ? t('Copied') : t('Cite')}
        </Button>
        <Link
          to={`/jurisdictions/${provision.jurisdiction_code}/laws/${provision.law_id}?v=${shownVersionId}#${provision.akn_eid}`}
          className={buttonVariants({ variant: 'outline', size: 'sm' })}
        >
          {t('Open full law')}
          <ArrowUpRight />
        </Link>
      </SheetFooter>
    </>
  );
}
