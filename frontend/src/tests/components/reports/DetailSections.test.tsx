/**
 * The report workspace's detail sections (owner, 2026-10-14; the proposal's
 * "Editing and saving", revised after its second review):
 * - a section reads as text with one status line; customised fields are
 *   marked with "Use the project's";
 * - opening a section and cancelling leaves nothing pending;
 * - Done keeps changes pending; Cancel returns to the last Edit, not the
 *   last save; "Discard changes in this section" returns to the last save;
 * - nothing in one section touches another.
 */
import React, { useMemo } from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../../../services/api', () => ({ getProjectReportTeam: vi.fn() }));

import DetailSections from '../../../components/reports/workspace/DetailSections';
import { toSave } from '../../../components/reports/workspace/detailModel';
import { useDetailEdits, type DetailEdits } from '../../../components/reports/workspace/useDetailEdits';
import type { EngagementOverrides, EngagementSettings } from '../../../services/api';

const followed: EngagementSettings = {
  executive_summary: 'The project’s summary.', client_name: 'Example Corp', classification: 'Confidential',
  engagement_type: 'Internal test', testers: [{ user_id: 1, name: 'Ana', role: 'Lead', email: null }],
  distribution: [{ name: 'CISO', email: null }], system_description: null, author_name: 'Installation Ltd',
  disclaimer: null, applications: null, thick_clients: null, other_targets: null,
};

let latest: DetailEdits | null = null;
const Harness: React.FC<{ saved?: EngagementOverrides; canEdit?: boolean }> = ({ saved = {}, canEdit = true }) => {
  const base = useMemo(() => ({ saved, followed, sources: { author_name: 'installation' as const }, ownOnly: [] }), [saved]);
  const edits = useDetailEdits(base);
  latest = edits;
  // The team editor links to Project settings: a router is needed.
  return <MemoryRouter><DetailSections edits={edits} canEdit={canEdit} idPrefix="report" members={[]} /></MemoryRouter>;
};
const section = (id: string) => within(screen.getByTestId(`report-section-${id}`));

describe('Report details — sections', () => {
  it('read as text, with one status line per section and the source of a value not the project’s', () => {
    render(<Harness saved={{ classification: 'Restricted' }} />);
    expect(section('engagement').getByTestId('report-section-engagement-status')).toHaveTextContent('1 customised for this report');
    expect(section('summary').getByTestId('report-section-summary-status')).toHaveTextContent('Uses the project’s defaults');
    expect(section('engagement').getByText('Restricted')).toBeInTheDocument();
    expect(section('engagement').getByTestId('report-classification-customised')).toHaveTextContent('Customised for this report');
    expect(section('engagement').getByText('From the installation’s defaults')).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).toBeNull();                        // nothing is a field until Edit
  });

  // Walkthrough 2026-10-14: the Executive summary said it twice.
  it('a section of one field says "Customised" once, in its status line; the field keeps "Use the project’s"', () => {
    render(<Harness saved={{ executive_summary: 'Ours.' }} />);
    expect(section('summary').getByTestId('report-section-summary-status')).toHaveTextContent('Customised for this report');
    const mark = section('summary').getByTestId('report-summary-customised');
    expect(mark).not.toHaveTextContent('Customised for this report');
    expect(within(mark).getByRole('button', { name: 'Use the project’s Executive summary' })).toBeInTheDocument();
    expect(section('summary').getAllByText(/Customised for this report/)).toHaveLength(1);
  });

  it('opening a section and cancelling leaves nothing pending', () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit Client & engagement' }));
    expect(screen.getByLabelText('Client')).toHaveValue('Example Corp');
    fireEvent.click(section('engagement').getByRole('button', { name: 'Cancel' }));
    expect(latest?.dirty).toBe(false);
  });

  it('Done keeps a change pending; Cancel returns to the last Edit, Discard to the last save', () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit Client & engagement' }));
    fireEvent.change(screen.getByLabelText('Client'), { target: { value: 'First' } });
    fireEvent.click(section('engagement').getByRole('button', { name: 'Done' }));
    expect(section('engagement').getByText('First')).toBeInTheDocument();
    expect(section('engagement').getByTestId('report-section-engagement-unsaved')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Edit Client & engagement' }));
    fireEvent.change(screen.getByLabelText('Client'), { target: { value: 'Second' } });
    fireEvent.click(section('engagement').getByRole('button', { name: 'Cancel' }));
    expect(section('engagement').getByText('First')).toBeInTheDocument();     // the Done before stays
    expect(toSave(latest!.ctx)).toEqual({ client_name: 'First' });
  });

  it('a change in one section leaves another alone; "Use the project’s" removes an override', () => {
    render(<Harness saved={{ classification: 'Restricted' }} />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit Summary' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Executive summary' }), { target: { value: 'Ours.' } });
    fireEvent.click(section('engagement').getByRole('button', { name: 'Use the project’s Classification' }));
    expect(section('engagement').getByText('Confidential')).toBeInTheDocument();
    // Only the edited details are sent; `null` = follow the project again.
    expect(toSave(latest!.ctx)).toEqual({ executive_summary: 'Ours.', classification: null });
  });

  // Ported from the old EngagementSettingsFields tests (what still applies).
  it('emptying a list the project fills is said, never silent', () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit Team & distribution' }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove CISO' }));
    expect(screen.getByTestId('report-distribution-nobody')).toHaveTextContent('This report will list nobody here.');
    expect(toSave(latest!.ctx)).toEqual({ distribution: [] });
  });

  it('a disclaimer nobody wrote says the template’s own is printed', () => {
    render(<Harness />);
    expect(section('disclaimer').getByText('None written: the template’s own disclaimer is printed')).toBeInTheDocument();
  });

  it('only the system description takes images', () => {
    const images = { placeable: [], urls: {}, resolver: { lookup: () => null, ensure: vi.fn() } };
    const WithImages: React.FC = () => {
      const base = useMemo(() => ({ saved: {}, followed, sources: null, ownOnly: [] }), []);
      const edits = useDetailEdits(base);
      return <DetailSections edits={edits} canEdit idPrefix="report" members={[]} images={images} />;
    };
    render(<WithImages />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit Scope & targets' }));
    fireEvent.click(screen.getByRole('button', { name: 'Edit Summary' }));
    fireEvent.click(screen.getByRole('button', { name: 'Edit Disclaimer' }));
    expect(screen.getAllByRole('button', { name: 'Insert image' })).toHaveLength(1);
    expect(within(screen.getByTestId('report-section-scope')).getByRole('button', { name: 'Insert image' })).toBeInTheDocument();
    // No upload offered: no paste, so nothing said about one.
    expect(screen.queryByTestId('report-system-paste-note')).toBeNull();
  });

  it('says before a paste that a pasted screenshot is the project’s at once, and Cancel keeps it', () => {
    const images = { placeable: [], urls: {}, resolver: { lookup: () => null, ensure: vi.fn() }, upload: vi.fn() };
    const WithUpload: React.FC = () => {
      const base = useMemo(() => ({ saved: {}, followed, sources: null, ownOnly: [] }), []);
      const edits = useDetailEdits(base);
      return <DetailSections edits={edits} canEdit idPrefix="report" members={[]} images={images} />;
    };
    render(<WithUpload />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit Scope & targets' }));
    expect(screen.getByTestId('report-system-paste-note')).toHaveTextContent(
      'A pasted screenshot is added to the project’s images at once. Cancel takes it out of this text, not out of the images.',
    );
  });

  it('without the right to edit, nothing editable is rendered', () => {
    render(<Harness canEdit={false} saved={{ classification: 'Restricted' }} />);
    expect(screen.queryByRole('button', { name: /Edit/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Use the project’s/ })).toBeNull();
  });
});
