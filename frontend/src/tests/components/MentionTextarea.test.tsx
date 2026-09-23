import React, { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../../hooks/useProjectMembers', () => ({
  useProjectMembers: () => [
    { username: 'eval-ana', full_name: 'Ana Ortiz' },
    { username: 'eval-ben', full_name: 'Ben Okafor' },
  ],
}));

import MentionTextarea from '../../components/MentionTextarea';
import MentionText from '../../components/MentionText';

const Harness: React.FC<{ onEscape?: () => void }> = ({ onEscape }) => {
  const [value, setValue] = useState('');
  return (
    <>
      <MentionTextarea
        aria-label="Comment"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Escape') onEscape?.(); }}
      />
      <output data-testid="value">{value}</output>
    </>
  );
};

const type = (el: HTMLTextAreaElement, value: string) => {
  fireEvent.change(el, { target: { value, selectionStart: value.length, selectionEnd: value.length } });
};

describe('MentionTextarea', () => {
  it('suggests members while an @name is typed and inserts the pick', () => {
    render(<Harness />);
    const box = screen.getByLabelText('Comment') as HTMLTextAreaElement;
    type(box, 'please look @eva');
    const options = screen.getAllByRole('option');
    expect(options.map((o) => o.textContent)).toEqual(['@eval-anaAna Ortiz', '@eval-benBen Okafor']);

    fireEvent.keyDown(box, { key: 'ArrowDown' });
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(screen.getByTestId('value').textContent).toBe('please look @eval-ben ');
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('picks with the mouse and matches on the full name', () => {
    render(<Harness />);
    const box = screen.getByLabelText('Comment') as HTMLTextAreaElement;
    type(box, '@ortiz');
    fireEvent.mouseDown(screen.getByRole('option', { name: /eval-ana/ }));
    expect(screen.getByTestId('value').textContent).toBe('@eval-ana ');
  });

  it('Escape closes the list without reaching the parent; then it does', () => {
    const onEscape = vi.fn();
    render(<Harness onEscape={onEscape} />);
    const box = screen.getByLabelText('Comment') as HTMLTextAreaElement;
    type(box, '@e');
    fireEvent.keyDown(box, { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(onEscape).not.toHaveBeenCalled();
    fireEvent.keyDown(box, { key: 'Escape' });
    expect(onEscape).toHaveBeenCalledTimes(1);
  });

  it('shows nothing for an e-mail address or a finished mention', () => {
    render(<Harness />);
    const box = screen.getByLabelText('Comment') as HTMLTextAreaElement;
    type(box, 'mail ana@ev');
    expect(screen.queryByRole('listbox')).toBeNull();
    type(box, '@eval-ana then');
    expect(screen.queryByRole('listbox')).toBeNull();
  });
});

describe('MentionText', () => {
  it('marks mentions of members only', () => {
    render(<p data-testid="body"><MentionText text="@eval-ana see this, not @nobody" /></p>);
    const marked = screen.getByTitle('Mentions eval-ana');
    expect(marked.textContent).toBe('@eval-ana');
    expect(screen.getByTestId('body').textContent).toBe('@eval-ana see this, not @nobody');
    expect(screen.queryByTitle(/nobody/)).toBeNull();
  });
});
