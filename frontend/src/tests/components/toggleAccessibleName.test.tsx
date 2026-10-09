/**
 * A Switch or a Checkbox has no text of its own.  The shared wrappers warn in
 * development when one is mounted with no accessible name — and stay quiet for
 * each of the ways a toggle is legitimately named in this app.
 */
import { createRef } from 'react';
import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { Checkbox } from '../../components/ui/checkbox';
import { Label } from '../../components/ui/label';
import { Switch } from '../../components/ui/switch';

let warn: ReturnType<typeof vi.spyOn>;
beforeEach(() => { warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined); });
afterEach(() => { warn.mockRestore(); });

const unnamed = () => warn.mock.calls.filter(([message]) => /has no accessible name/.test(String(message)));

describe.each([
  ['Switch', Switch, 'switch'],
  ['Checkbox', Checkbox, 'checkbox'],
] as const)('%s', (name, Toggle, role) => {
  it('warns when it is mounted with no name', () => {
    render(<Toggle />);
    expect(unnamed()).toHaveLength(1);
    expect(String(unnamed()[0][0])).toContain(`<${name}>`);
  });

  it('is named by aria-label', () => {
    render(<Toggle aria-label="Skip informational rows" />);
    expect(screen.getByRole(role, { name: 'Skip informational rows' })).toBeInTheDocument();
    expect(unnamed()).toHaveLength(0);
  });

  it('is named by a Label for its id (the “Auto” refresh switch, the remediation switch)', () => {
    render(<><Toggle id="t-auto" /><Label htmlFor="t-auto">Auto</Label></>);
    expect(screen.getByRole(role, { name: 'Auto' })).toBeInTheDocument();
    expect(unnamed()).toHaveLength(0);
  });

  it('is named by a label wrapped around it (Oversight’s overlap filter)', () => {
    render(<label><Toggle /> Only projects whose window overlaps these dates</label>);
    expect(screen.getByRole(role, { name: 'Only projects whose window overlaps these dates' })).toBeInTheDocument();
    expect(unnamed()).toHaveLength(0);
  });

  it('is named by aria-labelledby, when the element it names exists', () => {
    const { unmount } = render(<><span id="t-heading">Track remediation</span><Toggle aria-labelledby="t-heading" /></>);
    expect(unnamed()).toHaveLength(0);
    unmount();
    render(<Toggle aria-labelledby="t-nothing" />);
    expect(unnamed()).toHaveLength(1);
  });

  it('an empty label or an empty aria-label is not a name', () => {
    render(<><Toggle id="t-empty" aria-label=" " /><Label htmlFor="t-empty" /></>);
    expect(unnamed()).toHaveLength(1);
  });

  it('still hands its element to a caller’s ref', () => {
    const ref = createRef<HTMLButtonElement>();
    render(<Toggle ref={ref} aria-label="Select row" />);
    expect(ref.current).toBe(screen.getByRole(role));
  });
});
