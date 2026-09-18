import { CircleFlag } from '@codify/tbi-ui';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useSearchParams } from 'react-router';

import { Dropzone, type SelectedPdf } from '@/components/upload/dropzone';
import { countryName } from '@/lib/countries';
import { useJurisdiction } from '@/lib/jurisdiction-context';
import { startIngestRun, startIngestUrlRun } from '@/lib/api-runs';

export default function IngestRoute() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { country: pinnedCountry } = useJurisdiction();
  const country = params.get('jurisdiction') ?? pinnedCountry;
  const [selected, setSelected] = useState<SelectedPdf[]>([]);
  const [extraFilesIgnored, setExtraFilesIgnored] = useState(false);
  const [url, setUrl] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleFilesSelected = (files: SelectedPdf[]) => {
    setSelected(files.slice(0, 1));
    setExtraFilesIgnored(files.length > 1);
  };

  const submitFile = async () => {
    const file = selected[0]?.file;
    if (!file) return;
    setSubmitting(true);
    setError('');
    try {
      const created = await startIngestRun(file, { jurisdiction_code: country });
      navigate(
        `/runs/${created.run_id}?source=${encodeURIComponent(file.name)}&jurisdiction=${country}`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : t('Ingest could not be started.'));
    } finally {
      setSubmitting(false);
    }
  };

  const submitUrl = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!url.trim()) return;
    try {
      if (new URL(url.trim()).hostname !== 'eur-lex.europa.eu') {
        setError(t('URL ingest accepts EUR-Lex XML links only.'));
        return;
      }
    } catch {
      setError(t('Enter a valid EUR-Lex XML URL.'));
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      const created = await startIngestUrlRun({
        source_url: url.trim(),
        jurisdiction_code: country,
      });
      navigate(
        `/runs/${created.run_id}?source=${encodeURIComponent(url.trim())}&jurisdiction=${country}`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : t('Ingest could not be started.'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">{t('Upload')}</h1>
        <p className="mt-2 flex items-center gap-2 text-sm text-ink-60">
          <CircleFlag countryCode={country} height="16" width="16" title={countryName(country)} />
          <span>
            {t('Ingesting into {{country}}', { country: countryName(country) })}
            <span className="ms-1.5 font-mono text-xs text-ink-60">({country})</span>
            <span className="ms-2 text-xs text-ink-60">· {t('change in the sidebar')}</span>
          </span>
        </p>
      </header>

      <section className="space-y-4">
        <Dropzone
          onFilesSelected={handleFilesSelected}
          onReadOutcome={() => {}}
          onReadingChange={() => {}}
          disabled={submitting}
        />
        {extraFilesIgnored ? (
          <p className="text-xs text-ink-60">
            {t(
              'Only one document ingests at a time here; the rest of the selection was not added.',
            )}
          </p>
        ) : null}
        {selected.length > 0 ? (
          <div className="flex items-center justify-between rounded-lg border border-ink-20 bg-paper-sunken px-4 py-3 text-sm">
            <span className="truncate text-ink">{selected[0]?.file.name}</span>
            <button
              type="button"
              onClick={() => void submitFile()}
              disabled={submitting}
              className="rounded-lg bg-emphasis-strong px-3 py-1.5 text-paper disabled:opacity-50"
            >
              {submitting ? t('Starting…') : t('Ingest document')}
            </button>
          </div>
        ) : null}
      </section>

      <div className="my-8 flex items-center gap-3 text-xs uppercase tracking-wide text-ink-40">
        <span className="h-px flex-1 bg-ink-20" />
        {t('or use a source URL')}
        <span className="h-px flex-1 bg-ink-20" />
      </div>

      <form onSubmit={submitUrl} className="flex gap-2">
        <input
          type="url"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder={t('https://eur-lex.europa.eu/…/document.xml')}
          aria-label={t('Source URL')}
          className="h-10 min-w-0 flex-1 rounded-lg border border-ink-20 bg-paper px-3 text-sm text-ink outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <button
          type="submit"
          disabled={submitting || !url.trim()}
          className="rounded-lg bg-emphasis-strong px-4 text-sm font-medium text-paper disabled:opacity-50"
        >
          {submitting ? t('Starting…') : t('Ingest URL')}
        </button>
      </form>

      {error ? (
        <p role="alert" className="mt-4 text-sm text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
