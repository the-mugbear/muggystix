/**
 * Outbound webhook management (v2.73.0) — admin config for the current
 * project.  Lists webhooks, supports add / delete / enable-toggle / send-
 * test.  Scoped to the active project (the API client targets it via the
 * `p()` prefix), independent of the member-management project picker.
 */
import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Plus, Send, Trash2 } from 'lucide-react';
import {
  WebhookConfig,
  WebhookCreatePayload,
  WebhookEventType,
  createWebhook,
  deleteWebhook,
  listWebhookEventTypes,
  listWebhooks,
  testWebhook,
  updateWebhook,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { useConfirm } from '../hooks/useConfirm';
import { queryErrorText } from '../lib/query';
import { formatApiError } from '../utils/apiErrors';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import PostureSection from './posture/PostureSection';
import { Checkbox } from './ui/checkbox';
import { CharacterCount } from './ui/character-count';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Switch } from './ui/switch';

const WebhookSettings: React.FC = () => {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [confirmEl, confirm] = useConfirm();

  // Add-form state.
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [secret, setSecret] = useState('');
  const [selectedEvents, setSelectedEvents] = useState<Set<string>>(new Set());

  const hooksQuery = useQuery({ queryKey: ['listWebhooks'], queryFn: () => listWebhooks() });
  const typesQuery = useQuery({ queryKey: ['listWebhookEventTypes'], queryFn: () => listWebhookEventTypes() });
  const webhooks: WebhookConfig[] = hooksQuery.data ?? [];
  const eventTypes: WebhookEventType[] = typesQuery.data ?? [];
  const loading = hooksQuery.isFetching || typesQuery.isFetching;
  const error = loading ? null : queryErrorText(hooksQuery.error ?? typesQuery.error, 'Failed to load webhooks.');
  /** Patch the list in place with what a write is known to have done. */
  const setWebhooks = (update: (prev: WebhookConfig[]) => WebhookConfig[]) => {
    queryClient.setQueryData<WebhookConfig[]>(['listWebhooks'], (prev) => (prev ? update(prev) : prev));
  };

  const resetForm = () => {
    setName('');
    setUrl('');
    setSecret('');
    setSelectedEvents(new Set());
    setShowForm(false);
  };

  const create = useMutation({
    mutationFn: (payload: WebhookCreatePayload) => createWebhook(payload),
    onSuccess: () => {
      toast.success('Webhook created');
      resetForm();
      void queryClient.invalidateQueries({ queryKey: ['listWebhooks'] });
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to create webhook.')),
  });
  const creating = create.isPending;
  const handleCreate = () => create.mutate({
    name: name.trim(),
    url: url.trim(),
    secret: secret.trim() || null,
    events: Array.from(selectedEvents),
    is_active: true,
  });

  const toggle = useMutation({
    mutationFn: (hook: WebhookConfig) => updateWebhook(hook.id, { is_active: !hook.is_active }),
    onSuccess: (_updated, hook) => {
      setWebhooks((prev) => prev.map((h) => (h.id === hook.id ? { ...h, is_active: !h.is_active } : h)));
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to update webhook.')),
  });
  const handleToggle = (hook: WebhookConfig) => toggle.mutate(hook);

  // Not a write: one test delivery, whose outcome is a toast.
  const test = useMutation({
    mutationFn: (hook: WebhookConfig) => testWebhook(hook.id),
    onSuccess: (result) => {
      if (result.ok) {
        toast.success(`Test delivered (HTTP ${result.status_code})`);
      } else {
        toast.error(`Test failed: ${result.error ?? `HTTP ${result.status_code}`}`);
      }
    },
    onError: (err) => toast.error(formatApiError(err, 'Test request failed.')),
  });
  const handleTest = (hook: WebhookConfig) => test.mutate(hook);

  const remove = useMutation({
    mutationFn: (hook: WebhookConfig) => deleteWebhook(hook.id),
    onSuccess: (_void, hook) => {
      setWebhooks((prev) => prev.filter((h) => h.id !== hook.id));
      toast.info('Webhook deleted', { autoHideMs: 2000 });
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to delete webhook.')),
  });
  // One row is busy at a time, whichever action it is.
  const busyId = toggle.isPending
    ? toggle.variables.id
    : test.isPending ? test.variables.id : remove.isPending ? remove.variables.id : null;

  const handleDelete = async (hook: WebhookConfig) => {
    // v4.56.0 (UX·1) — was: delete on icon click with no confirm,
    // taking the stored signing secret with it.  An accidental
    // tap silently stopped downstream notifications and forced the
    // operator to regenerate + redistribute the secret.  Match the
    // confirm pattern used by scope / subnet / saved-view delete.
    const ok = await confirm({
      title: 'Delete webhook',
      body:
        'This removes the webhook configuration and revokes its signing secret. ' +
        'The secret cannot be recovered — you will need to generate and distribute a new one if you re-create the webhook.',
      resourceName: hook.name || hook.url,
      severity: 'danger',
      confirmLabel: 'Delete webhook',
    });
    if (!ok) return;
    remove.mutate(hook);
  };

  const toggleEvent = (key: string) => {
    setSelectedEvents((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const canCreate = name.trim().length > 0 && /^https?:\/\//i.test(url.trim());

  return (
    // v5.265.0 — a section of Project settings, not a card.
    <PostureSection
      title="Outbound webhooks"
      actions={
        <Button size="sm" variant="outline" onClick={() => setShowForm((s) => !s)}>
          <Plus className="size-4" aria-hidden /> Add webhook
        </Button>
      }
    >
      {confirmEl}
      <div>
        <p className="mb-sm text-caption text-muted-foreground">
          POST a JSON payload (Slack-incoming-webhook compatible) to an external URL on selected
          events. Delivery is best-effort; an optional secret signs each request
          (<code className="text-caption">X-BlueStick-Signature</code>, HMAC-SHA256).
        </p>

        {error && <p className="mb-sm text-metadata text-destructive">{error}</p>}

        {showForm && (
          <div className="mb-md space-y-sm rounded-control border border-border bg-muted/30 p-sm">
            <div className="grid gap-sm md:grid-cols-2">
              <div className="space-y-xxs">
                <Label htmlFor="wh-name">Name</Label>
                <Input id="wh-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Team Slack" maxLength={100}
                  aria-describedby="wh-name-count" />
                <CharacterCount id="wh-name-count" value={name} max={100} />
              </div>
              <div className="space-y-xxs">
                <Label htmlFor="wh-url">URL</Label>
                <Input id="wh-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://hooks.slack.com/services/…" maxLength={1000} />
              </div>
            </div>
            <div className="space-y-xxs">
              <Label htmlFor="wh-secret">Signing secret (optional)</Label>
              <Input id="wh-secret" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="Leave blank for unsigned" maxLength={500} />
            </div>
            <div className="space-y-xxs">
              <Label>Events</Label>
              <p className="text-caption text-muted-foreground">Select none to receive all events.</p>
              <div className="flex flex-col gap-xxs">
                {eventTypes.map((et) => (
                  <label key={et.key} className="flex items-start gap-xs text-metadata">
                    <Checkbox checked={selectedEvents.has(et.key)} onCheckedChange={() => toggleEvent(et.key)} />
                    <span className="min-w-0">
                      <span className="font-mono text-caption">{et.key}</span>
                      <span className="block text-caption text-muted-foreground">{et.description}</span>
                    </span>
                  </label>
                ))}
              </div>
            </div>
            <div className="flex gap-xs">
              <Button size="sm" disabled={!canCreate || creating} onClick={handleCreate}>
                {creating && <Loader2 className="size-3.5 animate-spin" aria-hidden />} Create
              </Button>
              <Button size="sm" variant="ghost" onClick={resetForm} disabled={creating}>Cancel</Button>
            </div>
          </div>
        )}

        {loading ? (
          <div className="flex items-center gap-xs text-metadata text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden /> Loading webhooks…
          </div>
        ) : webhooks.length === 0 ? (
          <p className="text-metadata text-muted-foreground">No webhooks configured.</p>
        ) : (
          <ul className="flex flex-col gap-xs">
            {webhooks.map((hook) => (
              <li key={hook.id} className="flex flex-wrap items-center gap-xs rounded-control border border-border p-sm">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-xs">
                    <span className="font-medium">{hook.name}</span>
                    {hook.has_secret && <Badge variant="outline">signed</Badge>}
                    {!hook.is_active && <Badge variant="muted">disabled</Badge>}
                  </div>
                  <p className="truncate text-caption text-muted-foreground" title={hook.url}>{hook.url}</p>
                  <div className="mt-xxs flex flex-wrap gap-xxs">
                    {hook.events.length === 0 ? (
                      <Badge variant="outline">all events</Badge>
                    ) : (
                      hook.events.map((e) => <Badge key={e} variant="outline">{e}</Badge>)
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-xs">
                  <Switch
                    checked={hook.is_active}
                    onCheckedChange={() => handleToggle(hook)}
                    disabled={busyId === hook.id}
                    aria-label={hook.is_active ? 'Disable webhook' : 'Enable webhook'}
                  />
                  <Button size="sm" variant="outline" onClick={() => handleTest(hook)} disabled={busyId === hook.id}>
                    <Send className="size-3.5" aria-hidden /> Test
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => handleDelete(hook)} disabled={busyId === hook.id} aria-label="Delete webhook">
                    <Trash2 className="size-3.5" aria-hidden />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </PostureSection>
  );
};

export default WebhookSettings;
