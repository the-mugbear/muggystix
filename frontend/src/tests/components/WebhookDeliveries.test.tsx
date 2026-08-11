/**
 * The delivery outbox exists to distinguish "configured" from "working".
 * These pin the states where getting it wrong would mislead an operator.
 */
import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import WebhookDeliveries from '../../components/WebhookDeliveries';
import type { WebhookDeliveryRow } from '../../services/api';

const listWebhookDeliveries = vi.fn();
const retryWebhookDelivery = vi.fn();
vi.mock('../../services/api', () => ({
  listWebhookDeliveries: (...a: unknown[]) => listWebhookDeliveries(...a),
  retryWebhookDelivery: (...a: unknown[]) => retryWebhookDelivery(...a),
}));

const success = vi.fn();
const error = vi.fn();
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success, error, info: vi.fn(), warning: vi.fn() }),
}));

vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Proj' } }),
}));

const row = (over: Partial<WebhookDeliveryRow> = {}): WebhookDeliveryRow => ({
  id: 1,
  webhook_config_id: 5,
  webhook_name: 'Team Slack',
  event: 'host_assigned',
  status: 'delivered',
  attempts: 1,
  max_attempts: 5,
  last_error: null,
  response_status: 200,
  next_attempt_at: null,
  created_at: '2026-08-10T12:00:00Z',
  delivered_at: '2026-08-10T12:00:01Z',
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  listWebhookDeliveries.mockResolvedValue([]);
  retryWebhookDelivery.mockResolvedValue(row());
});

describe('WebhookDeliveries', () => {
  it('renders a delivery with its event and webhook name', async () => {
    listWebhookDeliveries.mockResolvedValue([row()]);
    render(<WebhookDeliveries />);
    expect(await screen.findByText('host_assigned')).toBeInTheDocument();
    expect(screen.getByText('Team Slack')).toBeInTheDocument();
  });

  it('surfaces the failure reason rather than just a red dot', async () => {
    listWebhookDeliveries.mockResolvedValue([
      row({ id: 2, status: 'failed', attempts: 5, last_error: 'connect ECONNREFUSED 10.0.0.9:443' }),
    ]);
    render(<WebhookDeliveries />);
    // The error text is the actionable part — "failed" alone doesn't tell an
    // operator whether to fix DNS, a firewall, or the receiver.
    expect(await screen.findByText(/ECONNREFUSED/)).toBeInTheDocument();
  });

  it('offers no retry for a delivered row', async () => {
    listWebhookDeliveries.mockResolvedValue([row({ id: 3, status: 'delivered' })]);
    render(<WebhookDeliveries />);
    await screen.findByText('host_assigned');
    expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument();
  });

  it('offers retry for a failed row', async () => {
    listWebhookDeliveries.mockResolvedValue([row({ id: 4, status: 'failed' })]);
    render(<WebhookDeliveries />);
    expect(await screen.findByRole('button', { name: /retry/i })).toBeInTheDocument();
  });

  it('requeues a failed delivery and reloads', async () => {
    listWebhookDeliveries.mockResolvedValue([row({ id: 9, status: 'failed' })]);
    render(<WebhookDeliveries />);
    fireEvent.click(await screen.findByRole('button', { name: /retry/i }));
    await waitFor(() => expect(retryWebhookDelivery).toHaveBeenCalledWith(9));
    // Reloaded so the row's new state is visible without a manual refresh.
    await waitFor(() => expect(listWebhookDeliveries).toHaveBeenCalledTimes(2));
  });

  it('a failed load reads as an error, never as "no deliveries"', async () => {
    // The dangerous confusion: an empty table looks like a healthy webhook
    // with nothing to send.
    listWebhookDeliveries.mockRejectedValue(new Error('boom'));
    render(<WebhookDeliveries />);
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.queryByText(/No delivery attempts recorded/i)).not.toBeInTheDocument();
  });

  it('says plainly when there is genuinely nothing to show', async () => {
    listWebhookDeliveries.mockResolvedValue([]);
    render(<WebhookDeliveries />);
    expect(await screen.findByText(/No delivery attempts recorded/i)).toBeInTheDocument();
  });
});
