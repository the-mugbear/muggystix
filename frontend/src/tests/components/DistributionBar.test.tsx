/**
 * 5.414.0 — one design for a population split into parts (Posture's scanner
 * observations by severity and promoted findings by status were two). The
 * rail carries no numbers; the row under it says each count once with its
 * share; a zero is listed quietly, never drawn and never linked; a link says
 * what it opens when that is not the count shown.
 */
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import DistributionBar, { DISTRIBUTION_RAIL_PX } from '../../components/ui/DistributionBar';
import DispositionPipeline from '../../components/posture/DispositionPipeline';

const renderIn = (ui: React.ReactElement) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe('DistributionBar', () => {
  it('draws one rail with no numbers in it, and lists every part with its count and share', () => {
    renderIn(
      <DistributionBar title="Things by kind" summary="10 things" ariaLabel="Things"
        parts={[
          { key: 'a', label: 'Alpha', count: 7, color: 'red', href: '/a', opens: '3 hosts' },
          { key: 'b', label: 'Beta', count: 3, color: 'blue' },
          { key: 'c', label: 'Gamma', count: 0, color: 'green', href: '/c' },
        ]} />,
    );
    expect(screen.getByRole('heading', { name: 'Things by kind', level: 3 })).toBeInTheDocument();
    expect(screen.getByText('10 things')).toBeInTheDocument();
    const rail = screen.getByRole('img', { name: 'Things' });
    expect(rail).toHaveStyle({ height: `${DISTRIBUTION_RAIL_PX}px` });
    expect(rail.children).toHaveLength(2); // a zero is not drawn
    expect(rail).toHaveTextContent('');

    // The rail's segments link too; the row under it is what says the numbers.
    const row = rail.nextElementSibling as HTMLElement;
    const alpha = within(row).getByRole('link', { name: '7 Alpha — view 3 hosts' });
    expect(alpha).toHaveAttribute('href', '/a');
    expect(alpha).toHaveTextContent('7 · 70%');
    expect(alpha).toHaveTextContent('3 hosts →');
    expect(screen.getByText('Beta').closest('div')?.parentElement).toHaveTextContent('3 · 30%');
    // A zero is listed, quietly, with no link to an empty list.
    expect(screen.getByText('Gamma')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Gamma/ })).toBeNull();
  });
});

describe('DispositionPipeline on the shared design', () => {
  it('names every population, a zero included, and splits a closed population by status', () => {
    const { unmount } = renderIn(
      <DispositionPipeline byStatus={{ open: 40, confirmed: 39 }} statusHref={(s) => `/findings?status=${s}`} />,
    );
    expect(screen.getByRole('heading', { name: 'Findings by status' })).toBeInTheDocument();
    expect(screen.getByText('79 findings')).toBeInTheDocument();
    const row = screen.getByRole('img').nextElementSibling as HTMLElement;
    expect(within(row).getByRole('link', { name: '40 Under investigation — view' })).toHaveAttribute('href', '/findings?status=open');
    expect(within(row).getByRole('link', { name: '39 Confirmed — view' })).toBeInTheDocument();
    expect(screen.getByText('Closed')).toBeInTheDocument();
    unmount();

    renderIn(<DispositionPipeline byStatus={{ open: 2, accepted_risk: 1, remediated: 3 }} />);
    const rail = screen.getByRole('img');
    expect(rail.children).toHaveLength(3);
    expect(within(rail.parentElement as HTMLElement).getByText('Accepted risk')).toBeInTheDocument();
    expect(screen.getByText('Remediated')).toBeInTheDocument();
    expect(screen.getByText('Confirmed')).toBeInTheDocument(); // the empty population, still named
  });
});
