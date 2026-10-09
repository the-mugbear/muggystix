/**
 * Two-factor authentication on the Profile page: enroll (a new secret or an
 * imported one), the recovery codes shown once, disable, regenerate.
 *
 * Every step here sends or receives a secret — the TOTP secret, a one-time
 * code, the password, the recovery codes.  Besides the flow as the reader
 * sees it, each test pins that the client holds none of them once the step
 * that used it has ended (`SECRET_MUTATION` + `reset()`, lib/query): not in
 * the mutation cache, and not on screen.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createQueryClient } from '../../lib/query';
import { heldByMutations, withClient } from '../helpers/heldByMutations';

const api = vi.hoisted(() => ({
  getTwoFactorStatus: vi.fn(),
  startTwoFactorSetup: vi.fn(),
  enableTwoFactor: vi.fn(),
  disableTwoFactor: vi.fn(),
  regenerateRecoveryCodes: vi.fn(),
}));
vi.mock('../../services/api', () => api);const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));

import TwoFactorCard from '../../components/TwoFactorCard';

const SECRET = 'JBSWY3DPEHPK3PXPSECRETSEED';
const CODE = '481516';
const PASSWORD = 'correct-Horse9!battery';
const RECOVERY = ['aaaa-1111', 'bbbb-2222', 'cccc-3333'];
const setup = {
  secret: SECRET,
  otpauth_uri: `otpauth://totp/BlueStick:ana?secret=${SECRET}`,
  qr_svg: `data:image/svg+xml;base64,${SECRET}`,
  imported: false,
};
const off = { enabled: false, pending: false, unused_recovery_codes: 0 };
const on = { enabled: true, pending: false, unused_recovery_codes: 8 };
const refused = (detail: string) => ({ response: { status: 400, data: { detail } } });

const show = () => {
  const client = createQueryClient();
  render(<TwoFactorCard />, { wrapper: withClient(client) });
  return client;
};
/** None of these is held by a mutation any more. */
const expectForgotten = async (client: ReturnType<typeof createQueryClient>, ...secrets: string[]) => {
  await waitFor(() => {
    const held = heldByMutations(client);
    for (const secret of secrets) expect(held).not.toContain(secret);
  });
};

beforeEach(() => {
  vi.clearAllMocks();
  api.getTwoFactorStatus.mockResolvedValue(off);
});

describe('TwoFactorCard — enrolling', () => {
  it('a new secret: the QR and secret, a code, then the recovery codes once', async () => {
    api.startTwoFactorSetup.mockResolvedValue(setup);
    api.enableTwoFactor.mockResolvedValue(RECOVERY);
    const client = show();

    expect(await screen.findByText('Not enabled')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Set up with a new secret' }));

    // Step 2: the material to enroll with.
    expect(await screen.findByText(SECRET)).toBeInTheDocument();
    expect(api.startTwoFactorSetup).toHaveBeenCalledWith({});
    expect(screen.getByAltText('TOTP enrollment QR code')).toHaveAttribute('src', setup.qr_svg);
    expect(screen.getByText(/Scan this QR code with your authenticator app/)).toBeInTheDocument();
    // It is on screen from this component's own copy: the mutation has let go.
    await expectForgotten(client, SECRET);
    expect(screen.getByText(SECRET)).toBeInTheDocument();

    const verify = screen.getByRole('button', { name: 'Verify & enable' });
    expect(verify).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Authentication code'), { target: { value: ` ${CODE} ` } });
    api.getTwoFactorStatus.mockResolvedValue(on);
    fireEvent.click(verify);

    // Step 3: the recovery codes, and the status read again.
    for (const code of RECOVERY) expect(await screen.findByText(code)).toBeInTheDocument();
    expect(api.enableTwoFactor).toHaveBeenCalledWith(CODE);
    expect(screen.getByText(/They won't be shown again/)).toBeInTheDocument();
    await waitFor(() => expect(api.getTwoFactorStatus).toHaveBeenCalledTimes(2));
    // Shown from the component's copy; the secret and its QR are gone already.
    await expectForgotten(client, SECRET, CODE, ...RECOVERY);
    expect(screen.queryByText(SECRET)).toBeNull();
    expect(screen.queryByAltText('TOTP enrollment QR code')).toBeNull();
    expect(screen.getByText(RECOVERY[0])).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(await screen.findByText('Enabled')).toBeInTheDocument();
    expect(screen.getByText('8 recovery codes remaining')).toBeInTheDocument();
    for (const code of RECOVERY) expect(screen.queryByText(code)).toBeNull();
    await expectForgotten(client, SECRET, CODE, ...RECOVERY);
  });

  it('an imported secret is sent as typed, shows no QR, and is not kept', async () => {
    api.startTwoFactorSetup.mockResolvedValue({ ...setup, imported: true });
    api.enableTwoFactor.mockResolvedValue(RECOVERY);
    const client = show();

    fireEvent.click(await screen.findByRole('button', { name: 'Import existing secret' }));
    const proceed = screen.getByRole('button', { name: 'Continue' });
    expect(proceed).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Existing base32 secret'), { target: { value: ` ${SECRET} ` } });
    fireEvent.click(proceed);

    expect(await screen.findByText(/Confirm your imported secret/)).toBeInTheDocument();
    expect(api.startTwoFactorSetup).toHaveBeenCalledWith({ existing_secret: SECRET });
    expect(screen.queryByAltText('TOTP enrollment QR code')).toBeNull();
    expect(screen.queryByText(SECRET)).toBeNull();
    await expectForgotten(client, SECRET);

    fireEvent.change(screen.getByLabelText('Authentication code'), { target: { value: CODE } });
    fireEvent.click(screen.getByRole('button', { name: 'Verify & enable' }));
    expect(await screen.findByText(RECOVERY[0])).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));

    // Back on the status view the import box is closed and empty.
    await waitFor(() => expect(screen.queryByText(RECOVERY[0])).toBeNull());
    expect(screen.queryByLabelText('Existing base32 secret')).toBeNull();
    await expectForgotten(client, SECRET, CODE, ...RECOVERY);
  });

  it('says why setup could not start, and stays on the first step', async () => {
    api.startTwoFactorSetup.mockRejectedValue(refused('That is not a base32 secret.'));
    const client = show();

    fireEvent.click(await screen.findByRole('button', { name: 'Import existing secret' }));
    fireEvent.change(screen.getByLabelText('Existing base32 secret'), { target: { value: SECRET } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));

    expect(await screen.findByText('That is not a base32 secret.')).toBeInTheDocument();
    expect(screen.queryByLabelText('Authentication code')).toBeNull();
    // What was typed stays in its field, to correct; the request's copy does not.
    expect(screen.getByLabelText('Existing base32 secret')).toHaveValue(SECRET);
    await expectForgotten(client, SECRET);
    expect(screen.getByText('That is not a base32 secret.')).toBeInTheDocument();
  });

  it('says why a code was refused, keeps the step, and Cancel drops the secret and the code', async () => {
    api.startTwoFactorSetup.mockResolvedValue(setup);
    api.enableTwoFactor.mockRejectedValue(refused('Invalid authentication code'));
    const client = show();

    fireEvent.click(await screen.findByRole('button', { name: 'Set up with a new secret' }));
    fireEvent.change(await screen.findByLabelText('Authentication code'), { target: { value: CODE } });
    fireEvent.click(screen.getByRole('button', { name: 'Verify & enable' }));

    expect(await screen.findByText('Invalid authentication code')).toBeInTheDocument();
    expect(screen.getByText(SECRET)).toBeInTheDocument();              // still enrolling
    expect(api.getTwoFactorStatus).toHaveBeenCalledTimes(1);           // nothing changed: not read again
    await expectForgotten(client, CODE, SECRET);
    expect(screen.getByText('Invalid authentication code')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(await screen.findByText('Not enabled')).toBeInTheDocument();
    expect(screen.queryByText(SECRET)).toBeNull();
    expect(screen.queryByText('Invalid authentication code')).toBeNull();

    // A second enrollment starts with an empty code field.
    fireEvent.click(screen.getByRole('button', { name: 'Set up with a new secret' }));
    expect(await screen.findByLabelText('Authentication code')).toHaveValue('');
  });
});

describe('TwoFactorCard — when it is enabled', () => {
  beforeEach(() => api.getTwoFactorStatus.mockResolvedValue(on));

  it('disables after the password, says so, and keeps the password nowhere', async () => {
    api.disableTwoFactor.mockResolvedValue(undefined);
    const client = show();

    fireEvent.click(await screen.findByRole('button', { name: 'Disable 2FA' }));
    const confirmButton = screen.getByRole('button', { name: 'Confirm' });
    expect(confirmButton).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Confirm your password to disable 2FA'), { target: { value: PASSWORD } });
    api.getTwoFactorStatus.mockResolvedValue(off);
    fireEvent.click(confirmButton);

    expect(await screen.findByText('Not enabled')).toBeInTheDocument();
    expect(api.disableTwoFactor).toHaveBeenCalledWith(PASSWORD);
    expect(api.regenerateRecoveryCodes).not.toHaveBeenCalled();
    expect(toast.success).toHaveBeenCalledWith('Two-factor authentication disabled.');
    expect(screen.queryByLabelText(/Confirm your password/)).toBeNull();
    await expectForgotten(client, PASSWORD);
  });

  it('regenerates the recovery codes after the password and shows them once', async () => {
    api.regenerateRecoveryCodes.mockResolvedValue(RECOVERY);
    const client = show();

    fireEvent.click(await screen.findByRole('button', { name: 'Regenerate recovery codes' }));
    fireEvent.change(
      screen.getByLabelText('Confirm your password to regenerate recovery codes'), { target: { value: PASSWORD } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));

    for (const code of RECOVERY) expect(await screen.findByText(code)).toBeInTheDocument();
    expect(api.regenerateRecoveryCodes).toHaveBeenCalledWith(PASSWORD);
    expect(api.disableTwoFactor).not.toHaveBeenCalled();
    // On screen from the component's copy, not from the mutation.
    await expectForgotten(client, PASSWORD, ...RECOVERY);
    expect(screen.getByText(RECOVERY[0])).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(await screen.findByText('Enabled')).toBeInTheDocument();
    for (const code of RECOVERY) expect(screen.queryByText(code)).toBeNull();
    // The password prompt is closed and, opened again, empty.
    fireEvent.click(screen.getByRole('button', { name: 'Disable 2FA' }));
    expect(screen.getByLabelText('Confirm your password to disable 2FA')).toHaveValue('');
    await expectForgotten(client, PASSWORD, ...RECOVERY);
  });

  it('says why a wrong password was refused and lets the reader try again', async () => {
    api.disableTwoFactor.mockRejectedValue(refused('Incorrect password'));
    const client = show();

    fireEvent.click(await screen.findByRole('button', { name: 'Disable 2FA' }));
    fireEvent.change(screen.getByLabelText('Confirm your password to disable 2FA'), { target: { value: PASSWORD } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));

    expect(await screen.findByText('Incorrect password')).toBeInTheDocument();
    expect(screen.getByText('Enabled')).toBeInTheDocument();
    expect(toast.success).not.toHaveBeenCalled();
    // The field keeps what was typed, to correct; the request's copy is gone
    // and the message is still there.
    await expectForgotten(client, PASSWORD);
    expect(screen.getByText('Incorrect password')).toBeInTheDocument();
    expect(screen.getByLabelText('Confirm your password to disable 2FA')).toHaveValue(PASSWORD);

    // Cancel empties the field.
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    fireEvent.click(screen.getByRole('button', { name: 'Disable 2FA' }));
    expect(screen.getByLabelText('Confirm your password to disable 2FA')).toHaveValue('');
  });
});
