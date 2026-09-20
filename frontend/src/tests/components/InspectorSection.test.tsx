import React from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';

import { InspectorSection, openInspectorSection } from '../../components/host-inspector/InspectorSection';

const renderSection = (id = 'host-detail-x') =>
  render(
    <InspectorSection id={id} title="Ports" count={3}>
      <p>body</p>
    </InspectorSection>,
  );

beforeEach(() => window.localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe('InspectorSection', () => {
  it('is open by default and collapses from its heading', () => {
    renderSection();
    const heading = screen.getByRole('button', { name: /Ports\s*3/ });
    expect(heading).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('body')).toBeInTheDocument();
    fireEvent.click(heading);
    expect(heading).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('body')).not.toBeInTheDocument();
  });

  it('remembers the collapse per section for the next host', () => {
    const first = renderSection();
    fireEvent.click(screen.getByRole('button', { name: /Ports/ }));
    first.unmount();

    renderSection();
    expect(screen.getByRole('button', { name: /Ports/ })).toHaveAttribute('aria-expanded', 'false');
    // …and only that section.
    renderSection('host-detail-y');
    expect(screen.getAllByRole('button', { name: /Ports/ })[1]).toHaveAttribute('aria-expanded', 'true');
  });

  it('a jump link re-opens its target, so it never looks like a dead link', () => {
    renderSection();
    fireEvent.click(screen.getByRole('button', { name: /Ports/ }));
    act(() => openInspectorSection('host-detail-other'));
    expect(screen.queryByText('body')).not.toBeInTheDocument();
    act(() => openInspectorSection('host-detail-x'));
    expect(screen.getByText('body')).toBeInTheDocument();
  });

  it('still toggles when storage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    renderSection();
    fireEvent.click(screen.getByRole('button', { name: /Ports/ }));
    expect(screen.queryByText('body')).not.toBeInTheDocument();
  });
});
