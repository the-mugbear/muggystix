/**
 * PostureMeasure — a long label wraps (two lines, then clamps) instead of being
 * cut on one line (UX review 2026-09-24: Oversight read "Targets tested (in
 * review or revi…").
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect } from 'vitest';

import PostureMeasure from '../../components/posture/PostureMeasure';
import { TooltipProvider } from '../../components/ui/tooltip';

describe('PostureMeasure', () => {
  it('wraps a long label to two lines and keeps the whole of it on hover', () => {
    const label = 'Targets tested (in review or reviewed) across every project in the period';
    render(
      <MemoryRouter>
        <TooltipProvider>
          <PostureMeasure label={label} info="How it is counted." value={84} />
        </TooltipProvider>
      </MemoryRouter>,
    );
    const el = screen.getByText(label);
    expect(el).toHaveClass('line-clamp-2', 'break-words', 'min-w-0');
    expect(el).not.toHaveClass('truncate');
    expect(el).toHaveAttribute('title', label);
  });
});
