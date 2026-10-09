/**
 * Project settings → Imports (UX review 2026-09-24): the "skip informational
 * Nessus" project setting moved here from a switch in the upload dialog.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { useQuery } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const updateMock = vi.hoisted(() => vi.fn());
vi.mock('../../services/api', () => ({ updateProjectIngestSettings: updateMock }));
let effective = false;
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({
    currentProject: { id: 3, name: 'Demo', skip_informational_effective: effective },
  }),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));

import ProjectIngestSettings from '../../components/scans/ProjectIngestSettings';

// The project list as the provider reads it (ProjectContext): the setting is
// shown from it, so a save must put it out of date.  (This asserted a call of
// the context's `refreshProjects`, which put the whole page behind a loader.)
const readProjects = vi.fn();
const ProjectListOnScreen = () => {
  useQuery({ queryKey: ['getProjects'], queryFn: readProjects });
  return null;
};

beforeEach(() => {
  vi.clearAllMocks();
  effective = false;
  updateMock.mockResolvedValue({});
  readProjects.mockResolvedValue([]);
});

describe('ProjectIngestSettings', () => {
  it('names scanner observations, not findings, and saves the project setting', async () => {
    const { container } = render(<><ProjectListOnScreen /><ProjectIngestSettings canEdit /></>);
    expect(container.querySelector('#imports')).not.toBeNull();
    expect(screen.queryByText(/findings/)).toBeNull();
    const toggle = screen.getByRole('switch', { name: 'Skip informational Nessus scanner observations' });
    expect(toggle).not.toBeChecked();
    await waitFor(() => expect(readProjects).toHaveBeenCalledTimes(1));
    fireEvent.click(toggle);
    await waitFor(() => expect(updateMock).toHaveBeenCalledWith(3, { skip_informational_findings: true }));
    // The project list is read again in place, and the save is said after it.
    await waitFor(() => expect(readProjects).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(
      'Informational Nessus observations will be skipped on later uploads.',
    ));
  });

  it('shows the setting read-only to a role that cannot change it', () => {
    effective = true;
    render(<ProjectIngestSettings canEdit={false} />);
    const toggle = screen.getByRole('switch', { name: 'Skip informational Nessus scanner observations' });
    expect(toggle).toBeChecked();
    expect(toggle).toBeDisabled();
  });
});
