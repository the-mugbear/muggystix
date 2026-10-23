/** About BlueStick links to the running version's release notes (5.420.0) — analysts and above. */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import UserMenu from '../../components/UserMenu';
import buildInfo from '../../buildInfo.json';

const account = vi.hoisted(() => ({ role: 'member', projects: [{ my_role: 'analyst' }] as Array<{ my_role?: string }> }));
const navigate = vi.hoisted(() => vi.fn());
vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router-dom')>()),
  useNavigate: () => navigate,
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: null, projects: account.projects }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 1, username: 'ana', role: account.role },
    logout: vi.fn(),
    hasPermission: (r: string) => r === 'admin' && account.role === 'admin',
  }),
}));

beforeEach(() => {
  account.role = 'member';
  account.projects = [{ my_role: 'analyst' }];
  navigate.mockReset();
});

const openAbout = async () => {
  render(<MemoryRouter><UserMenu /></MemoryRouter>);
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'Open user menu' }));
  await user.click(await screen.findByText('About BlueStick'));
  return user;
};

describe('About BlueStick — release notes', () => {
  it('opens the notes at the version this build runs', async () => {
    const user = await openAbout();
    await user.click(await screen.findByRole('button', { name: 'What changed in this version' }));
    const backend = process.env.REACT_APP_BACKEND_VERSION || buildInfo.backendVersion || 'unknown';
    const frontend = process.env.REACT_APP_VERSION || buildInfo.frontendVersion || '1.0.0';
    expect(navigate).toHaveBeenCalledWith(`/reference/release-notes#release-${backend}-${frontend}`);
  });

  it('is not offered to an account that is viewer or auditor everywhere', async () => {
    account.projects = [{ my_role: 'viewer' }, { my_role: 'auditor' }];
    await openAbout();
    expect(await screen.findByText('Backend / API version')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'What changed in this version' })).not.toBeInTheDocument();
  });
});
