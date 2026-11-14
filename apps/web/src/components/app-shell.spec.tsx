import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { expect, it, vi } from 'vitest';

import AppShell from '@/components/app-shell';

vi.mock('@/components/jurisdiction-switcher', () => ({ JurisdictionSwitcher: () => null }));

it('tells an MCP client where the MCP server listens, not where this app does', async () => {
  render(
    <MemoryRouter>
      <AppShell />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByTitle('MCP server'));

  const dialog = await screen.findByRole('dialog');
  expect(dialog).toHaveTextContent('codify mcp --http');
  expect(dialog).toHaveTextContent('http://127.0.0.1:8001/mcp');
  expect(dialog).not.toHaveTextContent(window.location.origin);
});
