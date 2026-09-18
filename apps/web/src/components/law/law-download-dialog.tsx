
import {
  AlignLeft,
  Download,
  FileCode,
  Loader2,
  Paperclip,
  PenLine,
  type LucideIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { toast } from '@codify/tbi-ui';

import { Dialog, DialogContent, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { downloadOriginalSource, downloadVersionText } from '@/lib/api-laws';
import { cn } from '@/lib/utils';
import { useEffect, useRef, useState } from 'react';

export function LawDownloadDialog({
  versionId,
  hasSourceFile,
}: {
  versionId: string;
  hasSourceFile: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  const txt = useDirectDownload(versionId, () => downloadVersionText(versionId, 'txt'));
  const bluebell = useDirectDownload(versionId, () => downloadVersionText(versionId, 'bluebell'));
  const aknXml = useDirectDownload(versionId, () => downloadVersionText(versionId, 'akn-xml'));
  const original = useDirectDownload(versionId, () => downloadOriginalSource(versionId));

  useFailureToasts([
    { label: t('Plain text (.txt)'), status: txt },
    { label: t('Bluebell (.txt)'), status: bluebell },
    { label: t('Akoma Ntoso (.xml)'), status: aknXml },
    { label: t('Uploaded source file'), status: original },
  ]);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-ink-20 px-2.5 text-[0.8125rem] font-medium text-ink transition-colors hover:bg-paper-sunken">
        <Download className="size-3.5 text-ink-60" aria-hidden />
        <span>{t('Download')}</span>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogTitle className="text-base font-medium text-ink">{t('Download')}</DialogTitle>

        <Group title={t('Text')} sub={t('for search, diff and reuse')}>
          <Row
            icon={AlignLeft}
            label={t('Plain text (.txt)')}
            state={txt.state}
            error={txt.error}
            onSelect={txt.start}
          />
          <Row
            icon={PenLine}
            label={t('Bluebell (.txt)')}
            state={bluebell.state}
            error={bluebell.error}
            onSelect={bluebell.start}
          />
          <Row
            icon={FileCode}
            label={t('Akoma Ntoso (.xml)')}
            state={aknXml.state}
            error={aknXml.error}
            onSelect={aknXml.start}
          />
        </Group>

        <Group title={t('Original')} sub={t('as supplied')}>
          <Row
            icon={Paperclip}
            label={t('Uploaded source file')}
            state={original.state}
            error={original.error}
            onSelect={original.start}
            disabled={!hasSourceFile}
            unavailableNote={t('Not retained for this version')}
          />
        </Group>
      </DialogContent>
    </Dialog>
  );
}

function useDirectDownload(versionId: string, fetcher: () => Promise<void>): ExportStatus {
  const [state, setState] = useState<ExportState>('idle');
  const [error, setError] = useState<string | null>(null);

  const currentVersion = useRef(versionId);

  const [prevVersionId, setPrevVersionId] = useState(versionId);
  if (versionId !== prevVersionId) {
    setPrevVersionId(versionId);
    setState('idle');
    setError(null);
    // eslint-disable-next-line react-hooks/refs
    currentVersion.current = versionId;
  }

  const start = () => {
    setState('preparing');
    setError(null);
    const startedOn = versionId;
    fetcher()
      .then(() => {
        if (currentVersion.current !== startedOn) return;
        setState('idle');
      })
      .catch((err: unknown) => {
        if (currentVersion.current !== startedOn) return;
        setState('failed');
        setError(err instanceof Error ? err.message : 'Download failed.');
      });
  };
  return { state, error, start };
}

type ExportState = 'idle' | 'preparing' | 'failed';
interface ExportStatus { state: ExportState; error: string | null; start: () => void }

function useFailureToasts(rows: { label: string; status: ExportStatus }[]): void {
  const announced = useRef(new Set<string>());
  const signature = rows
    .map((r) => `${r.label}:${r.status.state}:${r.status.error ?? ''}`)
    .join('|');
  useEffect(() => {
    for (const { label, status } of rows) {
      if (status.state !== 'failed' || !status.error) {
        announced.current.delete(label);
        continue;
      }
      if (announced.current.has(label)) continue;
      announced.current.add(label);
      toast.error(`${label}: ${status.error}`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);
}

function Group({
  title,
  sub,
  children,
}: {
  title: string;
  sub: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-1">
      <h3 className="flex items-baseline gap-2">
        <span className="text-xs font-medium uppercase tracking-wide text-ink-60">{title}</span>
        <span className="text-[0.6875rem] text-ink-60">{sub}</span>
      </h3>
      <ul role="list" className="border-t border-ink-20">
        {children}
      </ul>
    </section>
  );
}

function Row({
  icon: Icon,
  label,
  state,
  error,
  onSelect,
  disabled = false,
  unavailableNote,
}: {
  icon: LucideIcon;
  label: string;
  state: ExportState;
  error?: string | null;
  onSelect: () => void;
  disabled?: boolean;
  unavailableNote?: string;
}) {
  const { t } = useTranslation();
  const busy = state === 'preparing';
  const failed = state === 'failed';
  const note = failed
    ? error || t('Download failed, try again')
    : disabled
      ? unavailableNote
      : undefined;
  return (
    <li>
      <button
        type="button"
        disabled={disabled || busy}
        onClick={onSelect}
        className={cn(
          'flex w-full items-center gap-2.5 border-b border-ink-20 px-2 py-2 text-start transition-colors',
          'focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50',
          disabled ? 'cursor-not-allowed' : 'hover:bg-paper-sunken',
        )}
      >
        <Icon
          className={cn('size-4 shrink-0', disabled ? 'text-ink-40' : 'text-ink-60')}
          aria-hidden
        />
        <span className="flex min-w-0 flex-1 items-baseline gap-2">
          <span className={cn('text-sm font-medium', disabled ? 'text-ink-40' : 'text-ink')}>
            {label}
          </span>
          {note ? (
            <span className={cn('text-[0.6875rem]', failed ? 'text-coral-strong' : 'text-ink-40')}>
              {note}
            </span>
          ) : null}
        </span>
        {busy ? (
          <Loader2 className="size-3.5 shrink-0 animate-spin text-ink-60" aria-hidden />
        ) : (
          <Download
            className={cn('size-3.5 shrink-0', disabled ? 'text-ink-20' : 'text-ink-40')}
            aria-hidden
          />
        )}
      </button>
    </li>
  );
}
