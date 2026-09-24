/**
 * Project settings → Imports (UX review 2026-09-24): the "skip informational
 * Nessus" project setting moved here from a switch in the upload dialog.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const updateMock = vi.hoisted(() => vi.fn());
vi.mock('../../services/api', () => ({ updateProjectIngestSettings: updateMock }));
const refreshProjects = vi.fn();
let effective = false;
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({
    currentProject: { id: 3, name: 'Demo', skip_informational_effective: effective },
    refreshProjects,
  }),
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import ProjectIngestSettings from '../../components/scans/ProjectIngestSettings';

beforeEach(() => {
  vi.clearAllMocks();
  effective = false;
  updateMock.mockResolvedValue({});
});

describe('ProjectIngestSettings', () => {
  it('names scanner observations, not findings, and saves the project setting', async () => {
    const { container } = render(<ProjectIngestSettings canEdit />);
    expect(container.querySelector('#imports')).not.toBeNull();
    expect(screen.queryByText(/findings/)).toBeNull();
    const toggle = screen.getByRole('switch', { name: 'Skip informational Nessus scanner observations' });
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    await waitFor(() => expect(updateMock).toHaveBeenCalledWith(3, { skip_informational_findings: true }));
    expect(refreshProjects).toHaveBeenCalled();
  });

  it('shows the setting read-only to a role that cannot change it', () => {
    effective = true;
    render(<ProjectIngestSettings canEdit={false} />);
    const toggle = screen.getByRole('switch', { name: 'Skip informational Nessus scanner observations' });
    expect(toggle).toBeChecked();
    expect(toggle).toBeDisabled();
  });
});
