import React, { useCallback, useEffect, useState } from 'react';
import {
  Plus,
  Pencil,
  Trash2,
  KeyRound,
  Loader2,
  CheckCircle2,
  AlertCircle,
  HelpCircle,
  PlugZap,
} from 'lucide-react';
import {
  listIntegrations,
  listIntegrationTypes,
  createIntegration,
  updateIntegration,
  deleteIntegration,
  testIntegrationConfig,
  IntegrationEntry,
  IntegrationCreatePayload,
  IntegrationTestResult,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import { useConfirm } from '../hooks/useConfirm';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Alert, AlertDescription } from '../components/ui/alert';
import { CardListSkeleton } from '../components/PageSkeleton';
import { Switch } from '../components/ui/switch';
import { Separator } from '../components/ui/separator';
import { PasswordInput, validateBaseUrl } from '../components/ui/password-input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../components/ui/select';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../components/ui/dialog';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '../components/ui/tooltip';

const BASE_URL_HINTS: Record<string, string> = {
  nessus: 'https://nessus.local:8834',
  openvas: 'https://gvm.local:9392',
  nuclei: '(usually blank — local binary)',
  burp: 'http://127.0.0.1:1337',
  generic_api: 'https://your-tool/api',
};

const SECRET_LABELS: Record<
  string,
  { one: string; two?: string; help1: string; help2?: string }
> = {
  nessus: {
    one: 'Access Key',
    two: 'Secret Key',
    help1: 'Nessus API access key',
    help2: 'Nessus API secret key',
  },
  openvas: {
    one: 'Username',
    two: 'Password',
    help1: 'GVM / OpenVAS username',
    help2: 'GVM / OpenVAS password',
  },
  nuclei: {
    one: 'Nuclei API token',
    help1: 'Optional — for PDCP / nuclei cloud',
  },
  burp: {
    one: 'Burp API key',
    help1: 'Burp Enterprise / Professional API key',
  },
  generic_api: {
    one: 'API key / token',
    help1: 'The secret the agent should use',
  },
};

const emptyForm: IntegrationCreatePayload = {
  name: '',
  integration_type: 'nessus',
  base_url: '',
  secret: '',
  secret2: '',
  is_active: true,
};

const IntegrationSettings: React.FC = () => {
  const toast = useToast();
  const [confirmEl, confirm] = useConfirm();
  const [integrations, setIntegrations] = useState<IntegrationEntry[]>([]);
  const [types, setTypes] = useState<Array<{ value: string; label: string }>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<IntegrationEntry | null>(null);
  const [form, setForm] = useState<IntegrationCreatePayload>(emptyForm);
  const [saving, setSaving] = useState(false);
  // Nessus-only: operator-supplied license cap (hosts per registered
  // Nessus scan).  Stored on save in `extra_config.max_hosts_per_scan`
  // so the recon prompt's Nessus block can steer the agent to chunk
  // large scopes into multiple license-sized scans.
  const [maxHostsPerScan, setMaxHostsPerScan] = useState<string>('');
  // Test-connection state: result of the most recent `POST /integrations/test`.
  // Cleared whenever the form changes so a stale "ok" doesn't outlast
  // the input it referred to.
  const [testResult, setTestResult] = useState<IntegrationTestResult | null>(null);
  const [testing, setTesting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [list, t] = await Promise.all([listIntegrations(), listIntegrationTypes()]);
      setIntegrations(list);
      setTypes(t);
    } catch (err: unknown) {
      const msg = formatApiError(err, 'Failed to load integrations.');
      setError(msg);
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  // Invalidate any prior test result the moment the form changes —
  // an "ok" result that refers to a base_url the user has since
  // edited would be misleading.
  useEffect(() => {
    setTestResult(null);
  }, [
    form.integration_type,
    form.base_url,
    form.secret,
    form.secret2,
    maxHostsPerScan,
  ]);

  const openNew = () => {
    setEditing(null);
    setForm(emptyForm);
    setMaxHostsPerScan('');
    setTestResult(null);
    setDialogOpen(true);
  };
  const openEdit = (r: IntegrationEntry) => {
    setEditing(r);
    setForm({
      name: r.name,
      integration_type: r.integration_type,
      base_url: r.base_url || '',
      secret: '',
      secret2: '',
      is_active: r.is_active,
    });
    const existingMax = (r.extra_config || {})['max_hosts_per_scan'];
    setMaxHostsPerScan(existingMax != null ? String(existingMax) : '');
    setTestResult(null);
    setDialogOpen(true);
  };

  /** Pre-save connection test.  Hands the current form values to
   *  `POST /integrations/test`; result renders inline below the Test
   *  button regardless of outcome (the endpoint always returns 200
   *  with a tri-state `ok` field). */
  const handleTestConnection = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const payload: IntegrationCreatePayload = {
        ...form,
        base_url: form.base_url || undefined,
        secret: form.secret || undefined,
        secret2: form.secret2 || undefined,
        extra_config:
          form.integration_type === 'nessus' && maxHostsPerScan.trim()
            ? { max_hosts_per_scan: Number(maxHostsPerScan) }
            : undefined,
      };
      const result = await testIntegrationConfig(payload);
      setTestResult(result);
    } catch (err: unknown) {
      // Network-level failure (e.g. the test endpoint itself errored).
      // Render as a failure so the user still sees something actionable.
      setTestResult({
        ok: false,
        integration_type: form.integration_type,
        message: formatApiError(err, 'Test request failed.'),
        duration_ms: 0,
      });
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      // Build extra_config from the Nessus-only license-cap field so
      // the recon prompt's chunking guidance can pick it up.
      const extraConfig =
        form.integration_type === 'nessus' && maxHostsPerScan.trim()
          ? { max_hosts_per_scan: Number(maxHostsPerScan) }
          : undefined;
      if (editing) {
        const payload: any = {
          name: form.name,
          base_url: form.base_url || null,
          is_active: form.is_active,
        };
        if (form.secret) payload.secret = form.secret;
        if (form.secret2) payload.secret2 = form.secret2;
        if (extraConfig) payload.extra_config = extraConfig;
        await updateIntegration(editing.id, payload);
        toast.success('Integration updated.');
      } else {
        await createIntegration({
          ...form,
          base_url: form.base_url || undefined,
          secret: form.secret || undefined,
          secret2: form.secret2 || undefined,
          extra_config: extraConfig,
        });
        toast.success('Integration added.');
      }
      setDialogOpen(false);
      await load();
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to save integration.'));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (r: IntegrationEntry) => {
    const ok = await confirm({
      title: 'Delete integration',
      body: 'The stored credentials will be permanently removed and any agent prompt that references this integration will stop seeing it. This cannot be undone.',
      resourceName: r.name,
      severity: 'danger',
      confirmLabel: 'Delete',
    });
    if (!ok) return;
    try {
      await deleteIntegration(r.id);
      toast.success('Integration deleted.');
      await load();
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to delete integration.'));
    }
  };

  const labels = SECRET_LABELS[form.integration_type] || SECRET_LABELS.generic_api;
  const urlError = validateBaseUrl(form.base_url);

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-col gap-xs sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-page-title">Scanner Integrations</h1>
          <p className="mt-xxs text-metadata text-muted-foreground">
            Credentials for external scanning tools (Nessus, OpenVAS, Nuclei, Burp, etc). Secrets
            are encrypted at rest and surfaced to agents via the recon prompt when relevant.
          </p>
        </div>
        <Button onClick={openNew}>
          <Plus className="size-4" aria-hidden /> Add Integration
        </Button>
      </div>

      {error && (
        <Alert variant="destructive" className="mb-md">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {loading ? (
        <CardListSkeleton count={3} cardHeight={180} />
      ) : integrations.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-sm p-xxl text-center">
            <KeyRound className="size-12 text-muted-foreground" aria-hidden />
            <p className="text-metadata text-muted-foreground">No integrations configured yet.</p>
            <p className="text-caption text-muted-foreground">
              Add one to make its credentials available to the agentic recon prompt.
            </p>
            <Button onClick={openNew}>
              <Plus className="size-4" aria-hidden /> Add Your First Integration
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-md sm:grid-cols-2 lg:grid-cols-3">
          {integrations.map((r) => (
            <Card key={r.id} className={r.is_active ? '' : 'opacity-60'}>
              <CardContent className="p-md">
                <div className="mb-xs flex items-start justify-between gap-xs">
                  <div className="min-w-0">
                    <p className="truncate text-subheading font-semibold">{r.name}</p>
                    <p className="text-caption text-muted-foreground">{r.integration_type}</p>
                  </div>
                  {!r.is_active && <Badge variant="muted">disabled</Badge>}
                </div>
                {r.base_url && (
                  <p className="text-metadata break-words">
                    <strong className="text-foreground">URL:</strong> {r.base_url}
                  </p>
                )}
                <div className="mt-xs flex flex-wrap gap-xxs">
                  <Badge variant={r.has_secret ? 'success' : 'muted'}>
                    {r.has_secret ? 'Secret set' : 'No secret'}
                  </Badge>
                  {r.has_secret2 && <Badge variant="success">Secondary secret</Badge>}
                  {r.project_id == null && <Badge variant="outline">all projects</Badge>}
                </div>
                <Separator className="my-sm" />
                <div className="flex gap-xxs">
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => openEdit(r)}
                        aria-label={`Edit integration ${r.name}`}
                      >
                        <Pencil className="size-4" aria-hidden />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Edit</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => handleDelete(r)}
                        aria-label={`Delete integration ${r.name}`}
                        className="text-muted-foreground hover:text-destructive"
                      >
                        <Trash2 className="size-4" aria-hidden />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Delete</TooltipContent>
                  </Tooltip>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Create / Edit dialog */}
      <Dialog open={dialogOpen} onOpenChange={(next) => !next && !saving && setDialogOpen(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? 'Edit Integration' : 'Add Integration'}</DialogTitle>
          </DialogHeader>
          <DialogBody className="flex flex-col gap-md">
            <div className="flex flex-col gap-xs">
              <Label htmlFor="int-name">Name</Label>
              <Input
                id="int-name"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                autoFocus
                required
              />
              <p className="text-caption text-muted-foreground">
                Human-readable label like "Client X Nessus" or "Internal OpenVAS".
              </p>
            </div>
            <div className="flex flex-col gap-xs">
              <Label htmlFor="int-type">Integration Type</Label>
              <Select
                value={form.integration_type}
                onValueChange={(v) => setForm((f) => ({ ...f, integration_type: v }))}
                disabled={!!editing}
              >
                <SelectTrigger id="int-type">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {types.map((t) => (
                    <SelectItem key={t.value} value={t.value}>
                      {t.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-xs">
              <Label htmlFor="int-url">Base URL</Label>
              <Input
                id="int-url"
                value={form.base_url || ''}
                onChange={(e) => setForm((f) => ({ ...f, base_url: e.target.value }))}
                placeholder={BASE_URL_HINTS[form.integration_type] || ''}
                aria-invalid={!!urlError}
                aria-describedby="int-url-help"
              />
              <p
                id="int-url-help"
                role={urlError ? 'alert' : undefined}
                className={`text-caption ${urlError ? 'text-destructive' : 'text-muted-foreground'}`}
              >
                {urlError || BASE_URL_HINTS[form.integration_type] || 'Optional'}
              </p>
            </div>
            <div className="flex flex-col gap-xs">
              <Label htmlFor="int-secret">
                {editing ? `${labels.one} (leave blank to keep current)` : labels.one}
              </Label>
              <PasswordInput
                id="int-secret"
                value={form.secret || ''}
                onChange={(e) => setForm((f) => ({ ...f, secret: e.target.value }))}
                onClear={
                  editing && editing.has_secret
                    ? async () => {
                        try {
                          await updateIntegration(editing.id, { clear_secret: true });
                          toast.success('Primary secret cleared.');
                          setForm((f) => ({ ...f, secret: '' }));
                          await load();
                        } catch (err: unknown) {
                          toast.error(formatApiError(err, 'Failed to clear secret.'));
                        }
                      }
                    : undefined
                }
                clearTooltip="Remove the stored primary secret"
              />
              <p className="text-caption text-muted-foreground">{labels.help1}</p>
            </div>
            {labels.two && (
              <div className="flex flex-col gap-xs">
                <Label htmlFor="int-secret2">
                  {editing ? `${labels.two} (leave blank to keep current)` : labels.two}
                </Label>
                <PasswordInput
                  id="int-secret2"
                  value={form.secret2 || ''}
                  onChange={(e) => setForm((f) => ({ ...f, secret2: e.target.value }))}
                  onClear={
                    editing && editing.has_secret2
                      ? async () => {
                          try {
                            await updateIntegration(editing.id, { clear_secret2: true });
                            toast.success('Secondary secret cleared.');
                            setForm((f) => ({ ...f, secret2: '' }));
                            await load();
                          } catch (err: unknown) {
                            toast.error(formatApiError(err, 'Failed to clear secret.'));
                          }
                        }
                      : undefined
                  }
                  clearTooltip="Remove the stored secondary secret"
                />
                <p className="text-caption text-muted-foreground">{labels.help2}</p>
              </div>
            )}
            {/* Nessus-only license cap (v2.49.4).  Lives in
                extra_config.max_hosts_per_scan so the recon prompt
                can steer the agent to chunk large scopes into
                multiple license-sized scans instead of one oversize
                scan Nessus rejects or truncates. */}
            {form.integration_type === 'nessus' && (
              <div className="flex flex-col gap-xs">
                <Label htmlFor="int-max-hosts">
                  Max hosts per scan <span className="text-muted-foreground">(optional)</span>
                </Label>
                <Input
                  id="int-max-hosts"
                  type="number"
                  min={1}
                  inputMode="numeric"
                  value={maxHostsPerScan}
                  onChange={(e) => setMaxHostsPerScan(e.target.value)}
                  placeholder="e.g. 512"
                />
                <p className="text-caption text-muted-foreground">
                  Your Nessus license's per-scan host limit (typical Pro tiers:
                  256 / 512 / 1024).  When set, the recon prompt instructs the
                  agent to split scopes larger than this into multiple
                  sequential Nessus scans.  Leave blank if unknown.
                </p>
              </div>
            )}
            <div className="flex items-center gap-xs">
              <Switch
                id="int-active"
                checked={!!form.is_active}
                onCheckedChange={(v) => setForm((f) => ({ ...f, is_active: Boolean(v) }))}
              />
              <Label htmlFor="int-active">Active (inactive credentials are not surfaced to agents)</Label>
            </div>

            {/* Pre-save connection test (v2.49.4).  Probe-by-type:
                Nessus + Ollama are implemented; other types return
                an honest "not yet implemented" so the button is
                universal.  Result clears the moment any form field
                changes (see the useEffect above). */}
            <div className="flex flex-col gap-xs">
              <div className="flex items-center gap-xs">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={handleTestConnection}
                  disabled={testing || !form.integration_type}
                >
                  {testing ? (
                    <Loader2 className="size-4 animate-spin" aria-hidden />
                  ) : (
                    <PlugZap className="size-4" aria-hidden />
                  )}
                  Test connection
                </Button>
                <p className="text-caption text-muted-foreground">
                  Verify the URL + credentials before saving.  Doesn't persist
                  anything; the result is also written to the backend log.
                </p>
              </div>
              {testResult && (
                <Alert
                  variant={
                    testResult.ok === true
                      ? 'success'
                      : testResult.ok === false
                        ? 'destructive'
                        : 'info'
                  }
                >
                  <AlertDescription className="flex items-start gap-xs">
                    {testResult.ok === true ? (
                      <CheckCircle2 className="size-4 shrink-0" aria-hidden />
                    ) : testResult.ok === false ? (
                      <AlertCircle className="size-4 shrink-0" aria-hidden />
                    ) : (
                      <HelpCircle className="size-4 shrink-0" aria-hidden />
                    )}
                    <span className="min-w-0 break-words">
                      {testResult.message}
                      {testResult.http_status != null && (
                        <span className="text-caption opacity-80">
                          {' '}(HTTP {testResult.http_status})
                        </span>
                      )}
                      {testResult.duration_ms > 0 && (
                        <span className="text-caption opacity-60">
                          {' · '}{testResult.duration_ms}ms
                        </span>
                      )}
                    </span>
                  </AlertDescription>
                </Alert>
              )}
            </div>
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={handleSave} disabled={saving || !form.name || urlError !== null}>
              {saving ? (
                <>
                  <Loader2 className="size-4 animate-spin" aria-hidden /> Saving…
                </>
              ) : (
                'Save'
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {confirmEl}
    </div>
  );
};

export default IntegrationSettings;
