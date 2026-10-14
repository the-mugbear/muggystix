/**
 * /reports/defaults (the report workspace, owner 2026-10-14): the project's
 * report defaults on the same sections as a draft's details, with ONE save
 * that sends only what changed — a stale read never overwrites another
 * person's change to a detail this page did not touch.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const getReportProfile = vi.fn();
const patchReportProfile = vi.fn();
vi.mock('../../services/api', () => ({
  getReportProfile: (...a: unknown[]) => getReportProfile(...a),
  patchReportProfile: (...a: unknown[]) => patchReportProfile(...a),
  listReportTemplates: vi.fn().mockResolvedValue([
    { name: 'pentest', title: 'Penetration test report', formats: ['html', 'docx'], kind: 'client' },
    { name: 'executive-brief', title: 'Executive brief', formats: ['html', 'docx'], kind: 'client' },
  ]),
  listReportTextImages: vi.fn().mockResolvedValue([]),
  uploadReportTextImage: vi.fn(),
  getProjectReportTeam: vi.fn(),
  listProjectMembers: vi.fn().mockResolvedValue([]),
}));
vi.mock('../../hooks/useProjectRole', () => ({ useProjectRole: () => ({ canWrite: true, canExport: true }) }));
vi.mock('../../hooks/useProjectMembers', () => ({ useProjectRoster: () => ({ members: [], status: 'ready', retry: vi.fn() }) }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: { id: 1, username: 'ana', role: 'member' } }) }));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));

import ReportDefaults from '../../pages/ReportDefaults';

const profile = {
  executive_summary: null, client_name: 'Example Corp', classification: 'Confidential', engagement_type: null,
  testers: [{ user_id: 1, name: 'Ana', role: 'Lead', email: null }], distribution: [], system_description: null,
  applications: null, thick_clients: null, other_targets: null, author_name: null, disclaimer: null,
  template: 'pentest', testers_from_project: true, inherited: { author_name: 'Installation Ltd', disclaimer: null },
};

beforeEach(() => {
  getReportProfile.mockReset().mockResolvedValue(profile);
  patchReportProfile.mockReset().mockImplementation(async (_p, body) => ({ ...profile, ...body, testers_from_project: true }));
});

const show = () => render(<MemoryRouter><ReportDefaults /></MemoryRouter>);

describe('Report defaults page', () => {
  it('says where an empty value comes from, and saves only what changed', async () => {
    show();
    expect(await screen.findByText(/Empty: reports use the installation’s — “Installation Ltd”/)).toBeInTheDocument();
    expect(screen.getByText('The project’s analysts and admins, until a team is saved.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save defaults' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Edit Client & engagement' }));
    fireEvent.change(screen.getByLabelText('Client'), { target: { value: 'New Corp' } });
    fireEvent.change(screen.getByLabelText('Classification'), { target: { value: '' } });
    fireEvent.click(within(screen.getByTestId('defaults-section-engagement')).getByRole('button', { name: 'Done' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save defaults' }));

    await waitFor(() => expect(patchReportProfile).toHaveBeenCalledWith(1, { client_name: 'New Corp', classification: null }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save defaults' })).toBeDisabled());
    expect(screen.getByText('All changes saved')).toBeInTheDocument();
  });

  it('an empty disclaimer with the installation’s shows it on request, and offers a copy to edit as the project’s', async () => {
    getReportProfile.mockResolvedValue({ ...profile, inherited: { author_name: null, disclaimer: 'The **installation’s** terms.' } });
    show();
    const note = await screen.findByTestId('defaults-disclaimer-inherited');
    expect(within(note).getByText('installation’s')).toBeInTheDocument();          // rendered Markdown
    fireEvent.click(within(note).getByRole('button', { name: 'Copy it here to edit' }));
    expect(screen.getByRole('textbox', { name: 'Disclaimer' })).toHaveValue('The **installation’s** terms.');
    expect(screen.getByRole('button', { name: 'Save defaults' })).toBeEnabled();
  });

  it('a follow status is a draft\'s: the defaults page shows none', async () => {
    show();
    await screen.findByTestId('defaults-section-engagement');
    expect(screen.queryByText('Uses the project’s defaults')).toBeNull();
  });
});
