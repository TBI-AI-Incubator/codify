import { Download, Loader2 } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { toast } from '../../vendor/tbi-ui';

import { downloadAknXml } from '@/lib/api-laws';

/* One format: the Akoma Ntoso the server holds. */
export function LawDownloadDialog({ versionId }: { versionId: string }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const start = async () => {
    setBusy(true);
    try {
      await downloadAknXml(versionId);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t('Download failed'));
    } finally {
      setBusy(false);
    }
  };
  return (
    <button
      type="button"
      disabled={busy}
      onClick={() => void start()}
      className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-ink-20 px-2.5 text-[0.8125rem] font-medium text-ink transition-colors hover:bg-paper-sunken disabled:opacity-60"
    >
      {busy ? (
        <Loader2 className="size-3.5 animate-spin text-ink-60" aria-hidden />
      ) : (
        <Download className="size-3.5 text-ink-60" aria-hidden />
      )}
      <span>{t('Akoma Ntoso (.xml)')}</span>
    </button>
  );
}
