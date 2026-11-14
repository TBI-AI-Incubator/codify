import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { expect, it, vi } from 'vitest';

import IngestRoute from '@/routes/ingest';

const installed = vi.hoisted(() => ({ codes: ['xa'] }));
vi.mock('@/components/upload/dropzone', () => ({ Dropzone: () => <div /> }));
vi.mock('@/lib/use-allowed-jurisdictions', () => ({
  useAllowedJurisdictions: () => ({
    items: installed.codes.map((code) => ({ code, name: code, languages: [] })),
    isPending: false,
    isError: false,
  }),
}));

function urlField(jurisdiction: string, codes: string[]) {
  cleanup();
  installed.codes = codes;
  render(
    <MemoryRouter initialEntries={[`/ingest?jurisdiction=${jurisdiction}`]}>
      <IngestRoute />
    </MemoryRouter>,
  );
  return screen.queryByRole('textbox', { name: 'Source URL' });
}

it('offers URL ingest only under an installed eu config', () => {
  expect(urlField('eu', ['xa'])).toBeNull();
  expect(urlField('xa', ['xa', 'eu'])).toBeNull();
  expect(urlField('eu', ['xa', 'eu'])).toBeInTheDocument();
});
