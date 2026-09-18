import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import CompareRoute from '@/routes/compare';
import IngestRoute from '@/routes/ingest';
import JurisdictionsRoute from '@/routes/jurisdictions/index';
import LawReaderRoute from '@/routes/law-reader';
import LawsCorpusPage from '@/routes/laws-list';
import SearchPage from '@/routes/search';

vi.mock('@codify/stele', () => ({
  LawReader: () => <div>Rendered law text</div>,
}));
vi.mock('@/components/bluebell-source-view', () => ({
  BluebellSourceView: () => <div>Bluebell source</div>,
}));
vi.mock('@/components/law/law-download-dialog', () => ({
  LawDownloadDialog: () => <button type="button">Download</button>,
}));
vi.mock('@/components/laws/law-row', () => ({
  LawRow: ({ law }: { law: { title: string } }) => <li>{law.title}</li>,
}));
vi.mock('@/components/corpus/world-map', () => ({
  WorldMap: () => <div>World map</div>,
}));
vi.mock('@/components/corpus/jurisdiction-table', () => ({
  JurisdictionTable: () => <div>Jurisdiction table</div>,
}));
vi.mock('@/components/upload/dropzone', () => ({
  Dropzone: () => <div>Drop PDFs or folders here</div>,
}));
vi.mock('@/lib/jurisdiction-context', () => ({
  useJurisdiction: () => ({ country: 'gb' }),
}));
vi.mock('@/lib/use-allowed-jurisdictions', () => ({
  useAllowedJurisdictions: () => ({ items: [], isPending: false, isError: false }),
}));
vi.mock('@/lib/api-laws', () => ({
  getLaw: vi.fn(async () => ({
    id: 'law-1',
    title: 'Sample Act',
    jurisdiction_code: 'gb',
    frbr_work_uri: '/akn/gb/act/2026/1',
    latest_version: { id: 'version-1', has_source_file: false },
  })),
  getVersionDocument: vi.fn(async () => ({
    version_id: 'version-1',
    law_id: 'law-1',
    frbr_work_uri: '/akn/gb/act/2026/1',
    frbr_expression_uri: '/akn/gb/act/2026/1/eng@2026-01-01',
    language: 'eng',
    expression_date: '2026-01-01',
    sections: [],
    provisions: [],
  })),
  getVersionSource: vi.fn(async () => ({ content: 'SECTION 1 - Sample' })),
}));
vi.mock('@/lib/api-misc', () => ({
  getCorpusSummary: vi.fn(async () => ({
    total: 1,
    total_documents_examined: 1,
    jurisdictions: [],
    bodies: [],
  })),
  listLawsCorpus: vi.fn(async () => ({
    items: [{ id: 'law-1', title: 'Sample Act', jurisdiction_code: 'gb' }],
    facets: { doctype: [], year: [] },
    total: 1,
  })),
  searchProvisions: vi.fn(async () => ({ groups: [], returned: 0, has_more: false })),
}));
vi.mock('@/lib/api-runs', () => ({
  startIngestRun: vi.fn(),
  startIngestUrlRun: vi.fn(),
}));

function renderRoute(path: string, element: React.ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>{element}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('open-core screen smoke tests', () => {
  it('renders the jurisdiction-scoped laws browser', async () => {
    renderRoute(
      '/jurisdictions/gb/laws',
      <Routes>
        <Route path="/jurisdictions/:code/laws" element={<LawsCorpusPage />} />
      </Routes>,
    );
    expect(await screen.findByRole('heading', { name: 'Laws' })).toBeInTheDocument();
    expect(await screen.findByText('Sample Act')).toBeInTheDocument();
  });

  it('renders the reader and export affordance without a login', async () => {
    renderRoute(
      '/laws/law-1',
      <Routes>
        <Route path="/laws/:id" element={<LawReaderRoute />} />
      </Routes>,
    );
    expect(await screen.findByRole('heading', { name: 'Sample Act' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Download' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument();
  });

  it('renders the source comparison without a login', async () => {
    renderRoute(
      '/laws/law-1/compare',
      <Routes>
        <Route path="/laws/:id/compare" element={<CompareRoute />} />
      </Routes>,
    );
    expect(await screen.findByText('Bluebell source')).toBeInTheDocument();
    expect(screen.getByText('Rendered law text')).toBeInTheDocument();
  });

  it('renders scoped search without a login', () => {
    renderRoute('/search?jurisdiction=gb', <SearchPage />);
    expect(screen.getByRole('heading', { name: 'Search the corpus' })).toBeInTheDocument();
  });

  it('renders jurisdiction browse without a login', async () => {
    renderRoute('/jurisdictions', <JurisdictionsRoute />);
    expect(await screen.findByRole('heading', { name: 'Jurisdictions' })).toBeInTheDocument();
    expect(screen.getByText('World map')).toBeInTheDocument();
  });

  it('renders ingest scoped by its URL without a login', () => {
    renderRoute('/ingest?jurisdiction=al', <IngestRoute />);
    expect(screen.getByRole('heading', { name: 'Upload' })).toBeInTheDocument();
    expect(screen.getByText('Drop PDFs or folders here')).toBeInTheDocument();
  });
});
