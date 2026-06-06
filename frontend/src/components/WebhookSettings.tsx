/**
 * Outbound webhook management (v2.73.0) — admin config for the current
 * project.  Lists webhooks, supports add / delete / enable-toggle / send-
 * test.  Scoped to the active project (the API client targets it via the
 * `p()` prefix), independent of the member-management project picker.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Loader2, Plus, Send, Trash2 } from 'lucide-react';
import {
  WebhookConfig,
  WebhookEventType,
  createWebhook,
  deleteWebhook,
  listWebhookEventTypes,
  listWebhooks,
  testWebhook,
  updateWebhook,
} from '../services/api';
import { useProject } from '../contexts/ProjectContext';
import { useToast } from '../contexts/ToastContext';
import { useConfirm } from '../hooks/useConfirm';
import { formatApiError } from '../utils/apiErrors';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { Checkbox } from './ui/checkbox';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Switch } from './ui/switch';

const WebhookSettings: React.FC = () => {
  const { currentProject } = useProject();
  const toast = useToast();
  const [confirmEl, confirm] = useConfirm();
  const [webhooks, setWebhooks] = useState<WebhookConfig[]>([]);
  const [eventTypes, setEventTypes] = useState<WebhookEventType[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  // Add-form state.
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [secret, setSecret] = useState('');
  const [selectedEvents, setSelectedEvents] = useState<Set<string>>(new Set());
  const [creating, setCreating] = useState(false);

  const projectId = currentProject?.id;

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [hooks, types] = await Promise.all([listWebhooks(), listWebhookEventTypes()]);
      setWebhooks(hooks);
      setEventTypes(types);
    } catch (err) {
      setError(formatApiError(err, 'Failed to load webhooks.'));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    reload();
  }, [reload]);

  const resetForm = () => {
    setName('');
    setUrl('');
    setSecret('');
    setSelectedEvents(new Set());
    setShowForm(false);
  };

  const handleCreate = async () => {
    setCreating(true);
    try {
      await createWebhook({
        name: name.trim(),
        url: url.trim(),
        secret: secret.trim() || null,
        events: Array.from(selectedEvents),
        is_active: true,
      });
      toast.success('Webhook created');
      resetForm();
      reload();
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to create webhook.'));
    } finally {
      setCreating(false);
    }
  };

  const handleToggle = async (hook: WebhookConfig) => {
    setBusyId(hook.id);
    try {
      await updateWebhook(hook.id, { is_active: !hook.is_active });
      setWebhooks((prev) => prev.map((h) => (h.id === hook.id ? { ...h, is_active: !h.is_active } : h)));
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to update webhook.'));
    } finally {
      setBusyId(null);
    }
  };

  const handleTest = async (hook: WebhookConfig) => {
    setBusyId(hook.id);
    try {
      const result = await testWebhook(hook.id);
      if (result.ok) {
        toast.success(`Test delivered (HTTP ${result.status_code})`);
      } else {
        toast.error(`Test failed: ${result.error ?? `HTTP ${result.status_code}`}`);
      }
    } catch (err) {
      toast.error(formatApiError(err, 'Test request failed.'));
    } finally {
      setBusyId(null);
    }
  };

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
    setBusyId(hook.id);
    try {
      await deleteWebhook(hook.id);
      setWebhooks((prev) => prev.filter((h) => h.id !== hook.id));
      toast.info('Webhook deleted', { autoHideMs: 2000 });
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to delete webhook.'));
    } finally {
      setBusyId(null);
    }
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
    <Card className="mb-md">
      {confirmEl}
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Outbound Webhooks{currentProject ? ` — ${currentProject.name}` : ''}</CardTitle>
        <Button size="sm" variant="outline" onClick={() => setShowForm((s) => !s)}>
          <Plus className="size-4" aria-hidden /> Add webhook
        </Button>
      </CardHeader>
      <CardContent>
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
                <Input id="wh-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Team Slack" maxLength={100} />
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
      </CardContent>
    </Card>
  );
};

export default WebhookSettings;
