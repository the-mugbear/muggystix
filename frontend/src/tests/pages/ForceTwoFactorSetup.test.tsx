/**
 * Forced two-factor enrollment (mandatory 2FA, not yet enrolled): choose a new
 * or an imported secret, confirm a code, save the recovery codes, continue.
 *
 * Every step sends or receives a secret — the TOTP secret, a one-time code,
 * the recovery codes.  Besides the flow as the reader sees it, each test pins
 * that the client holds none of them once the step that used it has ended
 * (`SECRET_MUTATION` + `reset()`, lib/query).
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createQueryClient } from '../../lib/query';
import { heldByMutations, withClient } from '../helpers/heldByMutations';

const api = vi.hoisted(() => ({ startTwoFactorSetup: vi.fn(), enableTwoFactor: vi.fn() }));
vi.mock('../../services/api', () => api);
const { logout, navigate, toast } = vi.hoisted(() => ({
  logout: vi.fn(), navigate: vi.fn(), toast: { success: vi.fn(), error: vi.fn() },
}));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ logout }) }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual<typeof import('react-router-dom')>('react-router-dom')),
  useNavigate: () => navigate,
}));

import ForceTwoFactorSetup from '../../pages/ForceTwoFactorSetup';

const SECRET = 'JBSWY3DPEHPK3PXPSECRETSEED';
const CODE = '481516';
const RECOVERY = ['aaaa-1111', 'bbbb-2222', 'cccc-3333'];
const setup = {
  secret: SECRET,
  otpauth_uri: `otpauth://totp/BlueStick:ana?secret=${SECRET}`,
  qr_svg: `data:image/svg+xml;base64,${SECRET}`,
  imported: false,
};
const refused = (detail: string) => ({ response: { status: 400, data: { detail } } });

const show = () => {
  const client = createQueryClient();
  render(<ForceTwoFactorSetup />, { wrapper: withClient(client) });
  return client;
};
/** None of these is held by a mutation any more. */
const expectForgotten = async (client: ReturnType<typeof createQueryClient>, ...secrets: string[]) => {
  await waitFor(() => {
    const held = heldByMutations(client);
    for (const secret of secrets) expect(held).not.toContain(secret);
  });
};

beforeEach(() => vi.clearAllMocks());

describe('ForceTwoFactorSetup', () => {
  it('a new secret: the QR and secret, a code, the recovery codes once, then into the app', async () => {
    api.startTwoFactorSetup.mockResolvedValue(setup);
    api.enableTwoFactor.mockResolvedValue(RECOVERY);
    const client = show();

    expect(screen.getByRole('heading', { name: 'Two-Factor Authentication Required' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Set up with a new secret' }));

    // Step 2: the material to enroll with.
    expect(await screen.findByText(SECRET)).toBeInTheDocument();
    expect(api.startTwoFactorSetup).toHaveBeenCalledWith({});
    expect(screen.getByAltText('TOTP enrollment QR code')).toHaveAttribute('src', setup.qr_svg);
    // It is on screen from this page's own copy: the mutation has let go.
    await expectForgotten(client, SECRET);
    expect(screen.getByText(SECRET)).toBeInTheDocument();

    const verify = screen.getByRole('button', { name: 'Verify & enable' });
    expect(verify).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Authentication code'), { target: { value: ` ${CODE} ` } });
    fireEvent.click(verify);

    // Step 3: the recovery codes; the secret and its QR are gone already.
    for (const code of RECOVERY) expect(await screen.findByText(code)).toBeInTheDocument();
    expect(api.enableTwoFactor).toHaveBeenCalledWith(CODE);
    expect(screen.getByText(/They won't be shown again/)).toBeInTheDocument();
    expect(screen.queryByText(SECRET)).toBeNull();
    expect(screen.queryByAltText('TOTP enrollment QR code')).toBeNull();
    await expectForgotten(client, SECRET, CODE, ...RECOVERY);
    expect(screen.getByText(RECOVERY[0])).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /I've saved my recovery codes/ }));
    expect(navigate).toHaveBeenCalledWith('/', { replace: true });
  });

  it('an imported secret is sent as typed and shows no QR', async () => {
    api.startTwoFactorSetup.mockResolvedValue({ ...setup, imported: true });
    api.enableTwoFactor.mockResolvedValue(RECOVERY);
    const client = show();

    fireEvent.click(screen.getByRole('button', { name: 'Import an existing authenticator secret' }));
    const proceed = screen.getByRole('button', { name: 'Continue' });
    expect(proceed).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Existing base32 secret'), { target: { value: ` ${SECRET} ` } });
    fireEvent.click(proceed);

    expect(await screen.findByText(/confirm the imported secret/)).toBeInTheDocument();
    expect(api.startTwoFactorSetup).toHaveBeenCalledWith({ existing_secret: SECRET });
    expect(screen.queryByAltText('TOTP enrollment QR code')).toBeNull();
    expect(screen.queryByText(SECRET)).toBeNull();
    await expectForgotten(client, SECRET);

    fireEvent.change(screen.getByLabelText('Authentication code'), { target: { value: CODE } });
    fireEvent.click(screen.getByRole('button', { name: 'Verify & enable' }));
    expect(await screen.findByText(RECOVERY[0])).toBeInTheDocument();
    await expectForgotten(client, SECRET, CODE, ...RECOVERY);
  });

  it('says why setup could not start, and stays on the first step', async () => {
    api.startTwoFactorSetup.mockRejectedValue(refused('That is not a base32 secret.'));
    const client = show();

    fireEvent.click(screen.getByRole('button', { name: 'Import an existing authenticator secret' }));
    fireEvent.change(screen.getByLabelText('Existing base32 secret'), { target: { value: SECRET } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));

    expect(await screen.findByText('That is not a base32 secret.')).toBeInTheDocument();
    expect(screen.queryByLabelText('Authentication code')).toBeNull();
    // What was typed stays in its field, to correct; the request's copy does not.
    expect(screen.getByLabelText('Existing base32 secret')).toHaveValue(SECRET);
    await expectForgotten(client, SECRET);
    expect(screen.getByText('That is not a base32 secret.')).toBeInTheDocument();
  });

  it('says why a code was refused, keeps the step, and Start over drops the secret and the code', async () => {
    api.startTwoFactorSetup.mockResolvedValue(setup);
    api.enableTwoFactor.mockRejectedValue(refused('Invalid authentication code'));
    const client = show();

    fireEvent.click(screen.getByRole('button', { name: 'Set up with a new secret' }));
    fireEvent.change(await screen.findByLabelText('Authentication code'), { target: { value: CODE } });
    fireEvent.click(screen.getByRole('button', { name: 'Verify & enable' }));

    expect(await screen.findByText('Invalid authentication code')).toBeInTheDocument();
    expect(screen.getByText(SECRET)).toBeInTheDocument();              // still enrolling
    expect(navigate).not.toHaveBeenCalled();
    await expectForgotten(client, CODE, SECRET);
    expect(screen.getByText('Invalid authentication code')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Start over' }));
    expect(screen.getByRole('button', { name: 'Set up with a new secret' })).toBeInTheDocument();
    expect(screen.queryByText(SECRET)).toBeNull();

    // A second enrollment starts with an empty code field.
    fireEvent.click(screen.getByRole('button', { name: 'Set up with a new secret' }));
    expect(await screen.findByLabelText('Authentication code')).toHaveValue('');
  });

  it('offers Sign out as the only other way out', () => {
    show();
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(logout).toHaveBeenCalledTimes(1);
    expect(api.startTwoFactorSetup).not.toHaveBeenCalled();
  });
});
