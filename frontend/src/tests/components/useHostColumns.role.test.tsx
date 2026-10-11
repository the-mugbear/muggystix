/**
 * Owner, 2026-10-11 — setting a host's review status needs the project
 * analyst role (the server's floor on `POST /hosts/{id}/follow`).  The Hosts
 * row still STATES where the review stands for every reader; the control that
 * changes it is hidden — not disabled — when the page says the reader may not
 * set it (`canSetReview`, the page's `useProjectRole().canWrite`).
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useHostColumns } from '../../components/hosts/useHostColumns';
import type { Host } from '../../services/api';

const host = {
  id: 7, ip_address: '10.0.0.7', hostname: null, state: 'up', ports: [], tags: [],
  follow: { status: 'in_review' }, assignees: [], other_reviewers: [], reviewed_by: [], note_count: 0,
} as unknown as Host;

const ReviewCell: React.FC<{ canSetReview?: boolean }> = ({ canSetReview }) => {
  const columns = useHostColumns({
    updatingHostId: null, onFollowChange: vi.fn(), onOpen: vi.fn(), onAddFilter: vi.fn(), canSetReview,
  });
  const review = columns.find((c) => c.id === 'review');
  const Cell = review!.cell as (ctx: { row: { original: Host } }) => React.ReactNode;
  return <div className="group">{Cell({ row: { original: host } })}</div>;
};

const CONTROL = { name: 'Change review for 10.0.0.7' };

describe('Hosts row — the review control follows what the page says the reader may do', () => {
  it('a reader who may not set a review status reads the state and gets no control', () => {
    const { container } = render(<ReviewCell canSetReview={false} />);
    expect(container.querySelector('[data-review-state]')).not.toBeNull();
    expect(screen.queryByRole('button', CONTROL)).toBeNull();
  });

  it.each([true, undefined])('canSetReview=%s shows the control', (value) => {
    const { container } = render(<ReviewCell canSetReview={value} />);
    expect(container.querySelector('[data-review-state]')).not.toBeNull();
    expect(screen.getByRole('button', CONTROL)).toBeInTheDocument();
  });
});
