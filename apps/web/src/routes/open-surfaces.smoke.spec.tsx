import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import IngestRoute from '@/routes/ingest';
import JurisdictionsRoute from '@/routes/jurisdictions/index';
import LawReaderRoute from '@/routes/law-reader';
import LawsCorpusPage from '@/routes/laws-list';
import SearchPage from '@/routes/search';

vi.mock('../vendor/stele', () => ({
  LawReader: () => <div>Rendered law text</div>,
}));
vi.mock('@/components/law/law-download-dialog', () => ({
  LawDownloadDialog: () => <button type="button">Download</button>,
}));
vi.mock('@/components/laws/law-row', () => ({
  LawRow: ({ law }: { law: { title: string } }) => <li>{law.title}</li>,
}));
vi.mock('@/components/upload/dropzone', () => ({
  Dropzone: () => <div>Drop PDFs or folders here</div>,
}));
vi.mock('@/lib/jurisdiction-context', () => ({
  useJurisdiction: () => ({ country: 'xa' }),
}));
vi.mock('@/lib/use-allowed-jurisdictions', () => ({
  useAllowedJurisdictions: () => ({ items: [], isPending: false, isError: false }),
}));
vi.mock('@/lib/api-jurisdictions', () => ({
  listJurisdictions: vi.fn(async () => [{ code: 'xa', name: 'Atlantis', languages: ['eng'] }]),
  getJurisdiction: vi.fn(),
}));
vi.mock('@/lib/api-laws', () => ({
  getLaw: vi.fn(async () => ({
    id: 'law-1',
    title: 'Sample Act',
    jurisdiction: 'xa',
    work_uri: '/akn/xa/act/2026/1',
    versions: [{ id: 'version-1', language: 'eng' }],
  })),
  getVersionDocument: vi.fn(async () => ({
    version_id: 'version-1',
    law_id: 'law-1',
    frbr_work_uri: '/akn/xa/act/2026/1',
    frbr_expression_uri: '/akn/xa/act/2026/1/eng@2026-01-01',
    language: 'eng',
    expression_date: '2026-01-01',
    sections: [],
    provisions: [],
  })),
}));
vi.mock('@/lib/api-misc', () => ({
  listLaws: vi.fn(async () => ({
    items: [{ id: 'law-1', title: 'Sample Act', jurisdiction_code: 'xa' }],
    limit: 25,
    offset: 0,
  })),
  searchProvisions: vi.fn(async () => ({ query: 'x', matches: [] })),
}));
vi.mock('@/lib/api-runs', () => ({
  startIngestRun: vi.fn(),
  startIngestUrlRun: vi.fn(),
}));

function renderRoute(path: string, element: ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>{element}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('the open-core screens render against the server shapes', () => {
  it('renders the jurisdiction-scoped laws browser', async () => {
    renderRoute(
      '/jurisdictions/xa/laws',
      <Routes>
        <Route path="/jurisdictions/:code/laws" element={<LawsCorpusPage />} />
      </Routes>,
    );
    expect(await screen.findByRole('heading', { name: 'Laws' })).toBeInTheDocument();
    expect(await screen.findByText('Sample Act')).toBeInTheDocument();
  });

  it('renders the reader and the download', async () => {
    renderRoute(
      '/laws/law-1',
      <Routes>
        <Route path="/laws/:id" element={<LawReaderRoute />} />
      </Routes>,
    );
    expect(await screen.findByRole('heading', { name: 'Sample Act' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Download' })).toBeInTheDocument();
    expect(screen.getByText('Rendered law text')).toBeInTheDocument();
  });

  it('renders search with no query as an invitation', () => {
    renderRoute('/search?jurisdiction=xa', <SearchPage />);
    expect(screen.getByRole('heading', { name: 'Search' })).toBeInTheDocument();
    expect(screen.getByRole('search')).toBeInTheDocument();
  });

  it('renders the jurisdiction list', async () => {
    renderRoute('/jurisdictions', <JurisdictionsRoute />);
    expect(await screen.findByRole('heading', { name: 'Jurisdictions' })).toBeInTheDocument();
    expect(await screen.findByText('Atlantis')).toBeInTheDocument();
  });

  it('renders ingest scoped by its URL', () => {
    renderRoute('/ingest?jurisdiction=xa', <IngestRoute />);
    expect(screen.getByRole('heading', { name: 'Upload' })).toBeInTheDocument();
    expect(screen.getByText('Drop PDFs or folders here')).toBeInTheDocument();
  });
});
