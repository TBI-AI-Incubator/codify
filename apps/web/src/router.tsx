import { createBrowserRouter } from 'react-router';

import AppShell from '@/components/app-shell';
import Home from '@/routes/home';
import JurisdictionDetailRoute from '@/routes/jurisdictions/detail';
import JurisdictionsRoute from '@/routes/jurisdictions/index';
import LawReaderRoute from '@/routes/law-reader';
import LawsListRoute from '@/routes/laws-list';
import SearchRoute from '@/routes/search/index';
import IngestRoute from '@/routes/ingest';
import RunRoute from '@/routes/run';

export const router = createBrowserRouter([
  {
    Component: AppShell,
    children: [
      { path: '/', Component: Home },
      { path: '/laws', Component: LawsListRoute },
      { path: '/laws/:id', Component: LawReaderRoute },
      { path: '/jurisdictions', Component: JurisdictionsRoute },
      { path: '/jurisdictions/:code', Component: JurisdictionDetailRoute },
      { path: '/jurisdictions/:code/laws', Component: LawsListRoute },
      { path: '/jurisdictions/:code/laws/:id', Component: LawReaderRoute },
      { path: '/search', Component: SearchRoute },
      { path: '/ingest', Component: IngestRoute },
      { path: '/runs/:id', Component: RunRoute },
    ],
  },
]);
