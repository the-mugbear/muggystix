/**
 * Outbound webhooks, a section of Project settings: the project's webhooks,
 * a form to add one, and per row a switch (enabled), Test and Delete.
 *
 * Pinned: the two reads (project first, the query's signal), what each row
 * says, a failed load said, and for each write what is sent, what the reader
 * is told, and what the list shows afterwards.
 *
 * The section has no role check of its own: Project settings mounts it for a
 * project admin only (`canSeeWebhooks`), which that page's test covers.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createQueryClient } from '../../lib/query';
import type { WebhookConfig, WebhookTestResult } from '../../services/api';
import { heldByMutations, withClient } from '../helpers/heldByMutations';

const api = vi.hoisted(() => ({
  listWebhooks: vi.fn(),
  listWebhookEventTypes: vi.fn(),
  createWebhook: vi.fn(),
  updateWebhook: vi.fn(),
  deleteWebhook: vi.fn(),
  testWebhook: vi.fn(),
}));
vi.mock('../../services/api', () => api);
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));
const project = vi.hoisted(() => ({ current: { id: 1, name: 'Demo' } as { id: number; name: string } | null }));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: project.current }),
}));

import WebhookSettings from '../../components/WebhookSettings';

const hook = (over: Partial<WebhookConfig> = {}): WebhookConfig => ({
  id: 5, project_id: 1, name: 'Team Slack', url: 'https://hooks.example.test/services/T1/B1',
  has_secret: true, events: ['host_assigned', 'finding_confirmed'], is_active: true,
  created_at: '2026-10-01T00:00:00Z', updated_at: null, ...over,
});
const SLACK = hook();
const PAGER = hook({ id: 6, name: 'Pager', url: 'http://pager.internal/hook', has_secret: false, events: [], is_active: false });
const EVENT_TYPES = [
  { key: 'host_assigned', description: 'A host was assigned to someone.' },
  { key: 'finding_confirmed', description: 'A finding was confirmed.' },
];
const SECRET = 'whsec_5up3r-s1gning-s3cret';
const refused = (status: number, detail: string) => ({ response: { status, data: { detail } } });

/** The server's list; a write the test lets through changes it. */
let stored: WebhookConfig[] = [];

const show = () => render(<WebhookSettings />);
/** Waits for the first reads to answer. */
const shown = async () => {
  show();
  await waitFor(() => expect(screen.queryByText('Loading webhooks…')).toBeNull());
};
/** The list's rows. */
const rows = (): HTMLElement[] => screen.queryAllByRole('listitem');
const rowOf = (name: string): HTMLElement => rows().find((li) => within(li).queryByText(name)) as HTMLElement;
const type = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const openForm = () => fireEvent.click(screen.getByRole('button', { name: 'Add webhook' }));
const createButton = () => screen.getByRole('button', { name: 'Create' });

beforeEach(() => {
  vi.clearAllMocks();
  project.current = { id: 1, name: 'Demo' };
  stored = [SLACK, PAGER];
  api.listWebhooks.mockImplementation(async () => stored);
  api.listWebhookEventTypes.mockResolvedValue(EVENT_TYPES);
  api.createWebhook.mockImplementation(async (_projectId: number, payload: { name: string; url: string }) => {
    const created = hook({ id: 9, name: payload.name, url: payload.url, has_secret: false, events: [] });
    stored = [...stored, created];
    return created;
  });
  api.updateWebhook.mockImplementation(async (_projectId: number, id: number, payload: Partial<WebhookConfig>) => (
    { ...(stored.find((h) => h.id === id) as WebhookConfig), ...payload }
  ));
  api.deleteWebhook.mockResolvedValue(undefined);
  api.testWebhook.mockResolvedValue({ ok: true, status_code: 200 });
});

describe('WebhookSettings — reading', () => {
  it('asks for the project’s webhooks and the event types, once each, with the query’s signal', async () => {
    await shown();
    expect(api.listWebhooks).toHaveBeenCalledTimes(1);
    expect(api.listWebhooks).toHaveBeenCalledWith(1, expect.any(AbortSignal));
    expect(api.listWebhookEventTypes).toHaveBeenCalledTimes(1);
    expect(api.listWebhookEventTypes).toHaveBeenCalledWith(1, expect.any(AbortSignal));
  });

  it('names another project when that is the one on screen', async () => {
    project.current = { id: 7, name: 'Other' };
    await shown();
    expect(api.listWebhooks).toHaveBeenCalledWith(7, expect.any(AbortSignal));
    expect(api.listWebhookEventTypes).toHaveBeenCalledWith(7, expect.any(AbortSignal));
  });

  it('says it is loading, and neither "none" nor a row, until the list answers', async () => {
    let answer: (value: WebhookConfig[]) => void = () => {};
    api.listWebhooks.mockReturnValue(new Promise<WebhookConfig[]>((resolve) => { answer = resolve; }));
    show();
    expect(screen.getByText('Loading webhooks…')).toBeInTheDocument();
    expect(screen.queryByText('No webhooks configured.')).toBeNull();
    expect(rows()).toHaveLength(0);

    answer([SLACK]);
    expect(await screen.findByText('Team Slack')).toBeInTheDocument();
    expect(screen.queryByText('Loading webhooks…')).toBeNull();
  });

  it('each row says its name, address, whether it is signed or disabled, and its events', async () => {
    await shown();
    expect(rows()).toHaveLength(2);

    const slack = within(rowOf('Team Slack'));
    expect(slack.getByText('https://hooks.example.test/services/T1/B1')).toBeInTheDocument();
    expect(slack.getByText('signed')).toBeInTheDocument();
    expect(slack.queryByText('disabled')).toBeNull();
    expect(slack.getByText('host_assigned')).toBeInTheDocument();
    expect(slack.getByText('finding_confirmed')).toBeInTheDocument();
    expect(slack.queryByText('all events')).toBeNull();
    expect(slack.getByRole('switch', { name: 'Disable webhook' })).toBeChecked();

    const pager = within(rowOf('Pager'));
    expect(pager.queryByText('signed')).toBeNull();
    expect(pager.getByText('disabled')).toBeInTheDocument();
    // No event chosen means every event — said, not left blank.
    expect(pager.getByText('all events')).toBeInTheDocument();
    expect(pager.getByRole('switch', { name: 'Enable webhook' })).not.toBeChecked();
  });

  it('a 200-character name and a 1000-character address are shown in full text, the address also as a title', async () => {
    const longName = `Hook-${'n'.repeat(195)}`;
    const longUrl = `https://hooks.example.test/${'u'.repeat(973)}`;
    stored = [hook({ name: longName, url: longUrl, events: [], created_at: null })];
    await shown();
    expect(screen.getByText(longName)).toBeInTheDocument();
    expect(screen.getByTitle(longUrl)).toHaveTextContent(longUrl);
  });

  it('an empty list says no webhook is configured, and no error', async () => {
    stored = [];
    await shown();
    expect(screen.getByText('No webhooks configured.')).toBeInTheDocument();
    expect(rows()).toHaveLength(0);
    expect(screen.queryByText(/Failed to load|could not/i)).toBeNull();
  });

  it('a list that could not be read is said, with the server’s reason', async () => {
    api.listWebhooks.mockRejectedValue(refused(403, 'Only a project admin can manage webhooks.'));
    await shown();
    expect(screen.getByText('Only a project admin can manage webhooks.')).toBeInTheDocument();
    expect(rows()).toHaveLength(0);
    // Not asked again behind the reader's back.
    expect(api.listWebhooks).toHaveBeenCalledTimes(1);
  });

  it('a failure with no reason from the server still says the load failed', async () => {
    api.listWebhooks.mockRejectedValue(new Error('boom'));
    await shown();
    expect(screen.getByText('The webhooks could not be loaded.')).toBeInTheDocument();
  });

  // DEFECT (WebhookSettings.tsx:56 and :223-224): a list that could not be
  // read falls back to `[]`, so UNDER the error the section also prints "No
  // webhooks configured." — "could not be read" shown as "none".  Correct: the
  // failure alone.  It fails today.
  it('a list that could not be read is not also said to be empty', async () => {
    api.listWebhooks.mockRejectedValue(refused(500, 'database unavailable'));
    await shown();
    expect(screen.getByText('database unavailable')).toBeInTheDocument();
    expect(screen.queryByText('No webhooks configured.')).toBeNull();
  });

  // DEFECT (WebhookSettings.tsx:175): the failure is a line of text with no
  // Retry — the reader has to leave the page and come back (UI_STYLE_GUIDE
  // §45: said where it would have been shown, with Retry).  It fails today.
  it('a failed load offers Retry, which reads the list again', async () => {
    api.listWebhooks.mockRejectedValueOnce(refused(500, 'database unavailable'));
    await shown();
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(await screen.findByText('Team Slack')).toBeInTheDocument();
  });

  it('when only the event types could not be read, the webhooks are still listed and the failure is said', async () => {
    api.listWebhookEventTypes.mockRejectedValue(refused(500, 'event types unavailable'));
    await shown();
    expect(screen.getByRole('alert')).toHaveTextContent('event types unavailable');
    expect(rows()).toHaveLength(2);
    expect(screen.queryByText('No webhooks configured.')).toBeNull();
  });

  // Owner decision 53: it said "Failed to load webhooks." over a list of
  // webhooks that HAD loaded.  Each failure names what could not be loaded.
  it('says WHICH read failed: the event types, not the webhooks listed under it', async () => {
    api.listWebhookEventTypes.mockRejectedValue(new Error('boom'));
    await shown();
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('The event types could not be loaded');
    expect(alert).not.toHaveTextContent(/Failed to load webhooks|webhooks could not/i);
    expect(rows()).toHaveLength(2);

    // Retry asks for the event types only.
    api.listWebhookEventTypes.mockResolvedValue(EVENT_TYPES);
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
    expect(api.listWebhookEventTypes).toHaveBeenCalledTimes(2);
    expect(api.listWebhooks).toHaveBeenCalledTimes(1);
  });

  it('a server reason for the event types is said after what it is about', async () => {
    api.listWebhookEventTypes.mockRejectedValue(refused(500, 'event types unavailable'));
    await shown();
    expect(screen.getByRole('alert')).toHaveTextContent(/The event types could not be loaded.*event types unavailable/);
  });

  it('when both reads fail, both are said', async () => {
    api.listWebhooks.mockRejectedValue(new Error('boom'));
    api.listWebhookEventTypes.mockRejectedValue(new Error('boom'));
    await shown();
    const alerts = screen.getAllByRole('alert');
    expect(alerts).toHaveLength(2);
    expect(alerts[0]).toHaveTextContent('The webhooks could not be loaded.');
    expect(alerts[1]).toHaveTextContent('The event types could not be loaded');
  });

  // Owner decision 52: the loading line is for the first load only.
  it('the rows stay on screen while the list is read again, and when that read fails', async () => {
    await shown();
    let fail: (reason: unknown) => void = () => {};
    api.listWebhooks.mockReturnValueOnce(new Promise<WebhookConfig[]>((_resolve, reject) => { fail = reject; }));
    openForm();
    type('Name', 'Ops channel');
    type('URL', 'https://hooks.example.test/ops');
    fireEvent.click(createButton());
    await waitFor(() => expect(api.listWebhooks).toHaveBeenCalledTimes(2));

    expect(screen.queryByText('Loading webhooks…')).toBeNull();
    expect(rows()).toHaveLength(2);

    fail(refused(500, 'database unavailable'));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('database unavailable');
    expect(rows()).toHaveLength(2);
    expect(screen.getByText('Team Slack')).toBeInTheDocument();

    // Retry: the rows stay while it asks, and the new webhook then shows.
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Ops channel')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

describe('WebhookSettings — adding one', () => {
  it('the form is closed until asked for, lists the event types, and Create waits for a name and an http(s) address', async () => {
    await shown();
    expect(screen.queryByLabelText('Name')).toBeNull();
    openForm();

    expect(screen.getByRole('checkbox', { name: /host_assigned\s*A host was assigned to someone\./ })).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: /finding_confirmed\s*A finding was confirmed\./ })).not.toBeChecked();
    expect(screen.getByText('Select none to receive all events.')).toBeInTheDocument();
    expect(screen.getByLabelText('Signing secret (optional)')).toHaveAttribute('type', 'password');

    expect(createButton()).toBeDisabled();
    type('Name', 'Ops channel');
    expect(createButton()).toBeDisabled();
    type('URL', 'ftp://example.test/hook');
    expect(createButton()).toBeDisabled();
    type('URL', 'hooks.example.test/no-scheme');
    expect(createButton()).toBeDisabled();
    type('URL', ' HTTPS://hooks.example.test/x ');
    expect(createButton()).toBeEnabled();
    type('Name', '   ');
    expect(createButton()).toBeDisabled();
    expect(api.createWebhook).not.toHaveBeenCalled();
  });

  it('sends what the form holds — trimmed, the chosen events, enabled — then says so, reads the list again and empties the form', async () => {
    await shown();
    openForm();
    type('Name', '  Ops channel ');
    type('URL', ' https://hooks.example.test/ops ');
    type('Signing secret (optional)', ` ${SECRET} `);
    fireEvent.click(screen.getByRole('checkbox', { name: /finding_confirmed/ }));
    fireEvent.click(createButton());

    expect(await screen.findByText('Ops channel')).toBeInTheDocument();
    expect(api.createWebhook).toHaveBeenCalledTimes(1);
    expect(api.createWebhook).toHaveBeenCalledWith(1, {
      name: 'Ops channel',
      url: 'https://hooks.example.test/ops',
      secret: SECRET,
      events: ['finding_confirmed'],
      is_active: true,
    });
    expect(toast.success).toHaveBeenCalledWith('Webhook created');
    // The new row came from the server's list, read again; the event types were not.
    expect(api.listWebhooks).toHaveBeenCalledTimes(2);
    expect(api.listWebhookEventTypes).toHaveBeenCalledTimes(1);
    expect(rows()).toHaveLength(3);

    // The form closed, and opens empty — the secret included.
    expect(screen.queryByLabelText('Name')).toBeNull();
    openForm();
    expect(screen.getByLabelText('Name')).toHaveValue('');
    expect(screen.getByLabelText('URL')).toHaveValue('');
    expect(screen.getByLabelText('Signing secret (optional)')).toHaveValue('');
    expect(screen.getByRole('checkbox', { name: /finding_confirmed/ })).not.toBeChecked();
  });

  it('no secret is sent as null (unsigned) and no event as an empty list (all events); an event ticked twice is not sent', async () => {
    await shown();
    openForm();
    type('Name', 'Ops channel');
    type('URL', 'http://hooks.internal/ops');
    type('Signing secret (optional)', '   ');
    const box = screen.getByRole('checkbox', { name: /host_assigned/ });
    fireEvent.click(box);
    fireEvent.click(box);
    fireEvent.click(createButton());

    await waitFor(() => expect(api.createWebhook).toHaveBeenCalledWith(1, {
      name: 'Ops channel', url: 'http://hooks.internal/ops', secret: null, events: [], is_active: true,
    }));
  });

  it('a refusal is said, the form stays as filled, and the list is not read again', async () => {
    api.createWebhook.mockRejectedValue(refused(422, 'URL must be an absolute http(s) URL'));
    await shown();
    openForm();
    type('Name', 'Ops channel');
    type('URL', 'https://hooks.example.test/ops');
    type('Signing secret (optional)', SECRET);
    fireEvent.click(createButton());

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('URL must be an absolute http(s) URL'));
    expect(toast.success).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Name')).toHaveValue('Ops channel');
    expect(screen.getByLabelText('URL')).toHaveValue('https://hooks.example.test/ops');
    expect(screen.getByLabelText('Signing secret (optional)')).toHaveValue(SECRET);
    expect(api.listWebhooks).toHaveBeenCalledTimes(1);
    expect(rows()).toHaveLength(2);
    await waitFor(() => expect(createButton()).toBeEnabled());
  });

  it('while it is being created it cannot be sent twice', async () => {
    let answer: (value: WebhookConfig) => void = () => {};
    api.createWebhook.mockReturnValue(new Promise<WebhookConfig>((resolve) => { answer = resolve; }));
    await shown();
    openForm();
    type('Name', 'Ops channel');
    type('URL', 'https://hooks.example.test/ops');
    fireEvent.click(createButton());
    await waitFor(() => expect(createButton()).toBeDisabled());
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    fireEvent.click(createButton());

    answer(hook({ id: 9, name: 'Ops channel' }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Webhook created'));
    expect(api.createWebhook).toHaveBeenCalledTimes(1);
  });

  it('Cancel sends nothing and forgets what was typed', async () => {
    await shown();
    openForm();
    type('Name', 'Ops channel');
    type('Signing secret (optional)', SECRET);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByLabelText('Name')).toBeNull();
    openForm();
    expect(screen.getByLabelText('Name')).toHaveValue('');
    expect(screen.getByLabelText('Signing secret (optional)')).toHaveValue('');
    expect(api.createWebhook).not.toHaveBeenCalled();
  });

  // DEFECT (WebhookSettings.tsx:73-81): the create mutation sends the signing
  // secret but does not spread `SECRET_MUTATION` and is never `reset()`, so
  // the secret stays in the client's mutation cache (and on the observer)
  // after the webhook was created and the form emptied — the rule in
  // .claude/rules/frontend.md ("A mutation that sends or returns a secret
  // spreads SECRET_MUTATION and is reset() once used").  It fails today.
  it('once the webhook is created, the client no longer holds its signing secret', async () => {
    const client = createQueryClient();
    render(<WebhookSettings />, { wrapper: withClient(client) });
    await waitFor(() => expect(screen.queryByText('Loading webhooks…')).toBeNull());
    openForm();
    type('Name', 'Ops channel');
    type('URL', 'https://hooks.example.test/ops');
    type('Signing secret (optional)', SECRET);
    fireEvent.click(createButton());
    await screen.findByText('Ops channel');

    await waitFor(() => expect(heldByMutations(client)).not.toContain(SECRET), { timeout: 300 });
  });
});

describe('WebhookSettings — the switch', () => {
  it('disabling sends only the new state for that webhook, and the row then says disabled — without reading the list again', async () => {
    await shown();
    fireEvent.click(within(rowOf('Team Slack')).getByRole('switch', { name: 'Disable webhook' }));

    expect(await within(rowOf('Team Slack')).findByText('disabled')).toBeInTheDocument();
    expect(api.updateWebhook).toHaveBeenCalledTimes(1);
    expect(api.updateWebhook).toHaveBeenCalledWith(1, 5, { is_active: false });
    expect(within(rowOf('Team Slack')).getByRole('switch', { name: 'Enable webhook' })).not.toBeChecked();
    // The other row is as it was.
    expect(within(rowOf('Pager')).getByRole('switch', { name: 'Enable webhook' })).not.toBeChecked();
    expect(api.listWebhooks).toHaveBeenCalledTimes(1);
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('enabling a disabled one sends is_active true', async () => {
    await shown();
    fireEvent.click(within(rowOf('Pager')).getByRole('switch', { name: 'Enable webhook' }));
    await waitFor(() => expect(within(rowOf('Pager')).queryByText('disabled')).toBeNull());
    expect(api.updateWebhook).toHaveBeenCalledWith(1, 6, { is_active: true });
    expect(within(rowOf('Pager')).getByRole('switch', { name: 'Disable webhook' })).toBeChecked();
  });

  it('a refusal is said and the row stays as the server has it', async () => {
    api.updateWebhook.mockRejectedValue(refused(403, 'Only a project admin can manage webhooks.'));
    await shown();
    fireEvent.click(within(rowOf('Team Slack')).getByRole('switch', { name: 'Disable webhook' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Only a project admin can manage webhooks.'));
    expect(within(rowOf('Team Slack')).queryByText('disabled')).toBeNull();
    expect(within(rowOf('Team Slack')).getByRole('switch', { name: 'Disable webhook' })).toBeChecked();
  });
});

describe('WebhookSettings — Test', () => {
  const pressTest = (name: string) => fireEvent.click(within(rowOf(name)).getByRole('button', { name: 'Test' }));

  it('asks the server to deliver one test for that webhook and says it was delivered, with the status', async () => {
    api.testWebhook.mockResolvedValue({ ok: true, status_code: 204 });
    await shown();
    pressTest('Pager');
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Test delivered (HTTP 204)'));
    expect(api.testWebhook).toHaveBeenCalledTimes(1);
    expect(api.testWebhook).toHaveBeenCalledWith(1, 6);
    expect(toast.error).not.toHaveBeenCalled();
    // A test changes nothing: the list is not read again.
    expect(api.listWebhooks).toHaveBeenCalledTimes(1);
  });

  it('a delivery the receiver refused is a failure, with its status', async () => {
    api.testWebhook.mockResolvedValue({ ok: false, status_code: 500 });
    await shown();
    pressTest('Team Slack');
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Test failed: HTTP 500'));
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('a delivery that never arrived is a failure, with the reason', async () => {
    api.testWebhook.mockResolvedValue({ ok: false, error: 'connection timed out' });
    await shown();
    pressTest('Team Slack');
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Test failed: connection timed out'));
  });

  it('a request the server refused is said with its reason', async () => {
    api.testWebhook.mockRejectedValue(refused(404, 'Webhook not found'));
    await shown();
    pressTest('Team Slack');
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Webhook not found'));
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('while one row’s test is out, that row’s controls wait and the other row’s do not', async () => {
    let answer: (value: WebhookTestResult) => void = () => {};
    api.testWebhook.mockReturnValue(new Promise<WebhookTestResult>((resolve) => { answer = resolve; }));
    await shown();
    pressTest('Team Slack');

    const slack = within(rowOf('Team Slack'));
    await waitFor(() => expect(slack.getByRole('button', { name: 'Test' })).toBeDisabled());
    expect(slack.getByRole('switch')).toBeDisabled();
    expect(slack.getByRole('button', { name: 'Delete webhook' })).toBeDisabled();
    const pager = within(rowOf('Pager'));
    expect(pager.getByRole('button', { name: 'Test' })).toBeEnabled();
    expect(pager.getByRole('switch')).toBeEnabled();

    answer({ ok: true, status_code: 200 });
    await waitFor(() => expect(within(rowOf('Team Slack')).getByRole('button', { name: 'Test' })).toBeEnabled());
    expect(api.testWebhook).toHaveBeenCalledTimes(1);
  });
});

describe('WebhookSettings — deleting one', () => {
  const pressDelete = (name: string) =>
    fireEvent.click(within(rowOf(name)).getByRole('button', { name: 'Delete webhook' }));
  const confirmDialog = () => screen.findByRole('dialog', { name: 'Delete webhook' });

  it('asks first, naming the webhook and what is lost; nothing is sent until confirmed', async () => {
    await shown();
    pressDelete('Team Slack');
    const dialog = await confirmDialog();
    expect(within(dialog).getByText('Team Slack')).toBeInTheDocument();
    expect(dialog).toHaveTextContent('revokes its signing secret');
    expect(api.deleteWebhook).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(api.deleteWebhook).not.toHaveBeenCalled();
    expect(rows()).toHaveLength(2);
    expect(toast.info).not.toHaveBeenCalled();
  });

  it('confirmed: that webhook is deleted, its row goes, the other stays, and it is said', async () => {
    await shown();
    pressDelete('Team Slack');
    fireEvent.click(within(await confirmDialog()).getByRole('button', { name: 'Delete webhook' }));

    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(api.deleteWebhook).toHaveBeenCalledTimes(1);
    expect(api.deleteWebhook).toHaveBeenCalledWith(1, 5);
    expect(screen.queryByText('Team Slack')).toBeNull();
    expect(screen.getByText('Pager')).toBeInTheDocument();
    expect(toast.info).toHaveBeenCalledWith('Webhook deleted', { autoHideMs: 2000 });
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('deleting the last one leaves "No webhooks configured."', async () => {
    stored = [SLACK];
    await shown();
    pressDelete('Team Slack');
    fireEvent.click(within(await confirmDialog()).getByRole('button', { name: 'Delete webhook' }));
    expect(await screen.findByText('No webhooks configured.')).toBeInTheDocument();
  });

  it('a webhook without a name is confirmed by its address', async () => {
    stored = [hook({ name: '', url: 'https://hooks.example.test/nameless' })];
    await shown();
    fireEvent.click(screen.getByRole('button', { name: 'Delete webhook' }));
    const dialog = await confirmDialog();
    expect(within(dialog).getByText('https://hooks.example.test/nameless')).toBeInTheDocument();
  });

  it('a refusal is said and the row stays', async () => {
    api.deleteWebhook.mockRejectedValue(refused(403, 'Only a project admin can manage webhooks.'));
    await shown();
    pressDelete('Pager');
    fireEvent.click(within(await confirmDialog()).getByRole('button', { name: 'Delete webhook' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Only a project admin can manage webhooks.'));
    expect(api.deleteWebhook).toHaveBeenCalledWith(1, 6);
    expect(rows()).toHaveLength(2);
    expect(screen.getByText('Pager')).toBeInTheDocument();
    expect(toast.info).not.toHaveBeenCalled();
  });
});
