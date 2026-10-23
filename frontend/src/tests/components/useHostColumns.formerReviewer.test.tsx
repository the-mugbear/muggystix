/**
 * Owner, 2026-10-22 — a deleted account's host review marks are KEPT,
 * attributed to no one: the server sends such a reviewer as
 * `{user_id: null, name: null}`.  The Hosts row still states the review, and
 * names its reviewer "a former member" — never a blank, "null" or "undefined".
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useHostColumns } from '../../components/hosts/useHostColumns';
import type { Host } from '../../services/api';
import { FORMER_MEMBER, reviewerName } from '../../utils/people';

const hostWith = (over: Partial<Host>) => ({
  id: 7, ip_address: '10.0.0.7', hostname: null, state: 'up', ports: [], tags: [],
  follow: null, assignees: [], other_reviewers: [], reviewed_by: [], note_count: 0,
  ...over,
}) as unknown as Host;

const ReviewCell: React.FC<{ host: Host }> = ({ host }) => {
  const columns = useHostColumns({
    updatingHostId: null, onFollowChange: vi.fn(), onOpen: vi.fn(), onAddFilter: vi.fn(), canSetReview: false,
  });
  const review = columns.find((c) => c.id === 'review');
  const Cell = review!.cell as (ctx: { row: { original: Host } }) => React.ReactNode;
  return <div className="group">{Cell({ row: { original: host } })}</div>;
};

describe('a review mark whose reviewer was deleted', () => {
  it('reviewerName falls back to "a former member" only when there is no name', () => {
    expect(reviewerName(null, null)).toBe(FORMER_MEMBER);
    expect(reviewerName('  ', undefined)).toBe('a former member');
    expect(reviewerName(null, 'alice')).toBe('alice');
    expect(reviewerName('Alice Ng', 'alice')).toBe('Alice Ng');
  });

  it('a host in review by a former member says so on the row', () => {
    render(<ReviewCell host={hostWith({ other_reviewers: [{ user_id: null, name: null }] })} />);
    const detail = screen.getByText('a former member reviewing');
    expect(detail).toHaveAttribute('title', 'Reviewing: a former member');
    expect(document.querySelector('[data-review-state="in_review"]')).not.toBeNull();
  });

  it('a host reviewed by a former member still reads Reviewed, and names them', () => {
    render(<ReviewCell host={hostWith({
      follow: { status: 'in_review' } as Host['follow'],
      reviewed_by: [{ user_id: null, name: null }, { user_id: 3, name: 'Bo' }],
    })} />);
    const detail = screen.getByText('Reviewed by a former member +1');
    expect(detail).toHaveAttribute('title', 'Reviewed by: a former member, Bo');
    expect(document.body.textContent).not.toMatch(/null|undefined/);
  });
});
