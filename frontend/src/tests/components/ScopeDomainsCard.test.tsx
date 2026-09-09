import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

import ScopeDomainsCard from '../../components/ScopeDomainsCard';
import { TooltipProvider } from '../../components/ui/tooltip';

const toastMock = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../hooks/useConfirm', () => ({
  useConfirm: () => [null, vi.fn().mockResolvedValue(false)],
}));
vi.mock('../../services/api', () => ({
  listScopeDomains: vi.fn().mockResolvedValue({
    items: [
      { id: 1, scope_id: 1, domain: 'acme.com', include_subdomains: true, description: null, name_count: 12 },
      { id: 2, scope_id: 1, domain: 'portal.acme.com', include_subdomains: false, description: null, name_count: 0 },
    ],
    total: 2,
    skip: 0,
    limit: 100,
  }),
  addScopeDomains: vi.fn(),
  deleteScopeDomain: vi.fn(),
}));

const renderCard = () =>
  render(
    <TooltipProvider>
      <ScopeDomainsCard scopeId={1} />
    </TooltipProvider>,
  );

describe('ScopeDomainsCard tooltips', () => {
  it('explains every derived presentation with its own (i)', async () => {
    renderCard();
    await waitFor(() => expect(screen.getByText('*.acme.com')).toBeInTheDocument());

    // One distinct accessible name per concept — a screen-reader user can
    // tell "names covered" from "match" without opening each.
    for (const label of ['About domain scope', 'About include subdomains', 'About match', 'About names covered']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    }
  });

  // Radix tooltips do not open under jsdom (no other test opens one either),
  // so the copy itself is not asserted here — only that each concept has its
  // own trigger and that the presentation the copy describes is on screen.
  it('renders the exact-vs-subdomain presentation the tips describe', async () => {
    renderCard();
    await waitFor(() => expect(screen.getByText('*.acme.com')).toBeInTheDocument());
    expect(screen.getByText('name + subdomains')).toBeInTheDocument();
    expect(screen.getByText('exact name')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText('0')).toBeInTheDocument();
  });
});
