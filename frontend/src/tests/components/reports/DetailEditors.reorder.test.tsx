/**
 * The assessment team and the distribution list print in the order shown.
 * The order used to be changeable only by deleting people and adding them
 * again; a row is now dragged by its grip or moved with Move up / Move down
 * (and the arrow keys on the grip).
 */
import React, { useState } from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../../services/api', () => ({
  getProjectReportTeam: vi.fn(),
}));
vi.mock('../../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../../contexts/ProjectContext', () => ({ useProject: () => ({ currentProject: { id: 1, name: 'P' } }) }));

import type { EngagementSettings } from '../../../services/api';
// The report workspace's list editors (5.394.0; they were part of the old
// `EngagementSettingsFields`).  A section that is not open shows the rows as
// text (`DetailRows`): nothing to reorder there.
import { DetailRows, DistributionEditor, TeamEditor } from '../../../components/reports/workspace/DetailEditors';

const person = (name: string, user_id: number | null = null) => ({ user_id, name, role: null, email: null });
const settings = (over: Partial<EngagementSettings> = {}): EngagementSettings => ({
  client_name: 'Acme', classification: null, engagement_type: null,
  testers: [person('Alice', 1), person('Bob'), person('Carol')],
  distribution: [{ name: 'Dana', email: null }, { name: 'Eli', email: 'eli@example.test' }],
  system_description: null,
  ...over,
} as EngagementSettings);

const onChange = vi.fn();
const Harness: React.FC<{ initial: EngagementSettings; readOnly?: boolean; disabled?: boolean }> = (
  { initial, readOnly, disabled },
) => {
  const [value, setValue] = useState(initial);
  const set = (patch: Partial<EngagementSettings>) => {
    const next = { ...value, ...patch };
    onChange(next);
    setValue(next);
  };
  if (readOnly) {
    return (
      <>
        <DetailRows rows={value.testers} empty="Nobody listed" />
        <DetailRows rows={value.distribution} empty="Nobody listed" />
      </>
    );
  }
  return (
    <MemoryRouter>
      <TeamEditor value={value.testers} members={[]} disabled={disabled} onChange={(testers) => set({ testers })} />
      <DistributionEditor value={value.distribution} disabled={disabled} onChange={(distribution) => set({ distribution })} />
    </MemoryRouter>
  );
};
const show = (props: { initial?: EngagementSettings; readOnly?: boolean; disabled?: boolean } = {}) =>
  render(<Harness initial={props.initial ?? settings()} readOnly={props.readOnly} disabled={props.disabled} />);

/** What the form was last told. */
const lastChange = () => onChange.mock.calls[onChange.mock.calls.length - 1][0] as EngagementSettings;
const lastTeam = () => lastChange().testers.map((t) => t.name);
const shownTeam = () => [1, 2, 3].map((n) => (screen.getByLabelText(`Team member ${n} name`) as HTMLInputElement).value);
const button = (name: string) => screen.getByRole('button', { name });
const rowOf = (label: string) => screen.getByLabelText(label).closest('[data-reorder-row]') as HTMLElement;
const transfer = () => ({ setData: vi.fn(), setDragImage: vi.fn(), effectAllowed: 'none', dropEffect: 'none' });

beforeEach(() => { onChange.mockReset(); });

describe('Assessment team — order', () => {
  it('Move down moves the row, and the focus stays on the row that moved', () => {
    show();
    const down = button('Move Alice down');
    down.focus();
    fireEvent.click(down);
    expect(lastTeam()).toEqual(['Bob', 'Alice', 'Carol']);
    // The inputs show the new order: no row shows another row's text.
    expect(shownTeam()).toEqual(['Bob', 'Alice', 'Carol']);
    // The same press again moves the same person again.
    expect(document.activeElement).toBe(button('Move Alice down'));

    fireEvent.click(button('Move Alice down'));
    expect(lastTeam()).toEqual(['Bob', 'Carol', 'Alice']);
    // At the end there is no further "down": the row's grip has the focus.
    expect(button('Move Alice down')).toBeDisabled();
    expect(document.activeElement).toBe(button('Reorder Alice'));
  });

  it('Move up moves the row up, keeping the focus', () => {
    show();
    const up = button('Move Carol up');
    up.focus();
    fireEvent.click(up);
    expect(lastTeam()).toEqual(['Alice', 'Carol', 'Bob']);
    expect(document.activeElement).toBe(button('Move Carol up'));
    // The row keeps the rest of what was written for it.
    expect(lastChange().testers[0]).toEqual(person('Alice', 1));
  });

  it('the arrow keys on the grip move the row, and the grip keeps the focus', () => {
    show();
    const grip = button('Reorder Bob');
    grip.focus();
    fireEvent.keyDown(grip, { key: 'ArrowUp' });
    expect(lastTeam()).toEqual(['Bob', 'Alice', 'Carol']);
    expect(document.activeElement).toBe(button('Reorder Bob'));
    // Already first: nothing is sent.
    onChange.mockClear();
    fireEvent.keyDown(button('Reorder Bob'), { key: 'ArrowUp' });
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.keyDown(button('Reorder Bob'), { key: 'ArrowDown' });
    fireEvent.keyDown(button('Reorder Bob'), { key: 'ArrowDown' });
    expect(lastTeam()).toEqual(['Alice', 'Carol', 'Bob']);
  });

  it('the first row cannot move up and the last cannot move down', () => {
    show();
    expect(button('Move Alice up')).toBeDisabled();
    expect(button('Move Alice down')).toBeEnabled();
    expect(button('Move Bob up')).toBeEnabled();
    expect(button('Move Bob down')).toBeEnabled();
    expect(button('Move Carol up')).toBeEnabled();
    expect(button('Move Carol down')).toBeDisabled();
  });

  it('a row dragged by its grip lands where it is dropped', () => {
    show();
    const dataTransfer = transfer();
    fireEvent.dragStart(button('Reorder Alice'), { dataTransfer });
    expect(dataTransfer.setData).toHaveBeenCalled();
    // The dragged row is dimmed; the row under the pointer shows where it lands.
    expect(rowOf('Team member 1 name')).toHaveAttribute('data-dragging');
    fireEvent.dragOver(rowOf('Team member 3 name'), { dataTransfer });
    expect(rowOf('Team member 3 name')).toHaveAttribute('data-drop-target', 'after');
    expect(rowOf('Team member 2 name')).not.toHaveAttribute('data-drop-target');

    fireEvent.drop(rowOf('Team member 3 name'), { dataTransfer });
    expect(lastTeam()).toEqual(['Bob', 'Carol', 'Alice']);
    expect(shownTeam()).toEqual(['Bob', 'Carol', 'Alice']);
    expect(rowOf('Team member 3 name')).not.toHaveAttribute('data-dragging');
    expect(rowOf('Team member 3 name')).not.toHaveAttribute('data-drop-target');
  });

  it('a row dragged upwards lands before the row it is dropped on', () => {
    show();
    const dataTransfer = transfer();
    fireEvent.dragStart(button('Reorder Carol'), { dataTransfer });
    fireEvent.dragOver(rowOf('Team member 1 name'), { dataTransfer });
    expect(rowOf('Team member 1 name')).toHaveAttribute('data-drop-target', 'before');
    fireEvent.drop(rowOf('Team member 1 name'), { dataTransfer });
    expect(lastTeam()).toEqual(['Carol', 'Alice', 'Bob']);
  });

  it('only the grip is draggable, and something else dropped on a row is not a reorder', () => {
    show();
    expect(button('Reorder Alice')).toHaveAttribute('draggable', 'true');
    expect(rowOf('Team member 1 name')).not.toHaveAttribute('draggable');
    expect(screen.getByLabelText('Team member 1 name')).not.toHaveAttribute('draggable');
    // Text dragged from elsewhere: no drag of this list is under way.
    fireEvent.drop(rowOf('Team member 3 name'), { dataTransfer: transfer() });
    expect(onChange).not.toHaveBeenCalled();
    // A drag that was given up leaves nothing marked.
    fireEvent.dragStart(button('Reorder Alice'), { dataTransfer: transfer() });
    fireEvent.dragEnd(button('Reorder Alice'));
    expect(rowOf('Team member 1 name')).not.toHaveAttribute('data-dragging');
    fireEvent.drop(rowOf('Team member 3 name'), { dataTransfer: transfer() });
    expect(onChange).not.toHaveBeenCalled();
  });

  it('an unnamed row is named by its place', () => {
    show({ initial: settings({ testers: [person('Alice'), person('')] }) });
    fireEvent.click(button('Move team member 2 up'));
    expect(lastTeam()).toEqual(['', 'Alice']);
  });

  it('removing a row leaves the others as they were', () => {
    show();
    fireEvent.click(button('Remove Bob'));
    expect(lastTeam()).toEqual(['Alice', 'Carol']);
    fireEvent.click(button('Move Carol up'));
    expect(lastTeam()).toEqual(['Carol', 'Alice']);
  });

  it('read-only shows the people and no control to reorder or remove them', () => {
    show({ readOnly: true });
    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^(Reorder|Move|Remove) / })).not.toBeInTheDocument();
    expect(document.querySelector('[draggable="true"]')).toBeNull();
  });

  it('while saving the controls stay and are inert', () => {
    show({ disabled: true });
    for (const name of ['Reorder Bob', 'Move Bob up', 'Move Bob down', 'Remove Bob']) {
      expect(button(name)).toBeDisabled();
    }
    expect(button('Reorder Bob')).toHaveAttribute('draggable', 'false');
    fireEvent.dragStart(button('Reorder Bob'), { dataTransfer: transfer() });
    fireEvent.keyDown(button('Reorder Bob'), { key: 'ArrowDown' });
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe('Distribution — order (the same rows)', () => {
  it('moves a recipient by button and by drag', () => {
    show();
    const recipients = () => lastChange().distribution.map((r) => r.name);
    fireEvent.click(button('Move Eli up'));
    expect(recipients()).toEqual(['Eli', 'Dana']);
    expect(screen.getByLabelText('Recipient 1 contact')).toHaveValue('eli@example.test');

    const dataTransfer = transfer();
    fireEvent.dragStart(button('Reorder Eli'), { dataTransfer });
    fireEvent.dragOver(rowOf('Recipient 2 name'), { dataTransfer });
    fireEvent.drop(rowOf('Recipient 2 name'), { dataTransfer });
    expect(recipients()).toEqual(['Dana', 'Eli']);
    // The team was not touched.
    expect(lastTeam()).toEqual(['Alice', 'Bob', 'Carol']);
  });
});
