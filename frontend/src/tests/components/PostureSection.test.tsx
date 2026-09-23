import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import { PostureSection, SectionCount } from '../../components/posture/PostureSection';

// v5.269.0 — the heading is what separates sections: an accent bar before a
// sentence-case title in the foreground colour, not small grey capitals.
describe('PostureSection heading', () => {
  it('leads with an accent bar and keeps the title as written', () => {
    render(
      <PostureSection title={<><span>Worth a look</span><SectionCount>8</SectionCount></>}>
        <p>body</p>
      </PostureSection>,
    );
    const heading = screen.getByRole('heading', { level: 2 });
    expect(heading).toHaveTextContent('Worth a look8');
    expect(heading).not.toHaveClass('uppercase');
    expect(heading).toHaveClass('text-foreground');
    expect(heading.querySelector('[aria-hidden].bg-primary')).not.toBeNull();
    expect(screen.getByText('8')).toHaveClass('text-muted-foreground');
  });
});
