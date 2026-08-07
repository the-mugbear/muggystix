import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import { AgentAuthorBadge } from '../../components/AgentAuthorBadge';
import { TooltipProvider } from '../../components/ui/tooltip';

const renderBadge = (actorType?: 'user' | 'agent' | null) =>
  render(
    <TooltipProvider>
      <AgentAuthorBadge actorType={actorType} />
    </TooltipProvider>,
  );

describe('AgentAuthorBadge', () => {
  it('marks agent-written content', () => {
    renderBadge('agent');
    expect(screen.getByText('Agent')).toBeInTheDocument();
  });

  // The important half: a human-typed note must look exactly as it did
  // before agent authorship existed, so the badge stays meaningful.
  it('renders nothing for a human author', () => {
    const { container } = renderBadge('user');
    expect(container).toBeEmptyDOMElement();
  });

  // Notes created before the actor_type column existed come back without it;
  // those are human-written and must not be mislabelled as agent output.
  it('renders nothing when actor_type is absent or null', () => {
    expect(renderBadge(undefined).container).toBeEmptyDOMElement();
    expect(renderBadge(null).container).toBeEmptyDOMElement();
  });
});
