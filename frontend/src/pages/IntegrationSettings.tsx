import React, { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { SECRET_MUTATION, queryErrorText } from '../lib/query';
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
  openvas: 'https://gvm.local:9392 (GSA web UI)',
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
    help1: 'The secret an agent is given when it requests this integration',
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
  // Every signed-in user may READ the integrations — one list for the whole
  // installation, whoever configured each; adding, editing, testing and
  // deleting one need the GLOBAL administrator — the server's rule, on any
  // row.  The controls are not rendered for anyone else (style guide §40:
  // hidden, not disabled; no copy pointing at a control the reader does not
  // have).
  const canManage = useAuth().user?.role === 'admin';
  const toast = useToast();
  const [confirmEl, confirm] = useConfirm();
  const queryClient = useQueryClient();
  // Integrations are the installation's, not a project's and not a user's:
  // the keys name neither.
  const integrationsQuery = useQuery({
    queryKey: ['listIntegrations'],
    queryFn: ({ signal }) => listIntegrations(signal),
  });
  const typesQuery = useQuery({
    queryKey: ['listIntegrationTypes'],
    queryFn: ({ signal }) => listIntegrationTypes(signal),
  });
  const integrations: IntegrationEntry[] = integrationsQuery.data ?? [];
  const types: Array<{ value: string; label: string }> = typesQuery.data ?? [];
  // The skeleton is for the FIRST load only (no integrations read yet).  A
  // later read — after a save, or Retry — keeps the rows on screen; if it
  // fails they stay, with the failure said above them.
  const loading = integrationsQuery.data === undefined && integrationsQuery.isFetching;
  const failed = [integrationsQuery, typesQuery].find((q) => q.isError && !q.isFetching);
  const error = queryErrorText(failed?.error, 'Failed to load integrations.');
  // A failed load is said on the page, where the integrations would be, with
  // Retry — never as a toast over "No integrations configured yet."  Retry
  // asks again for what failed; integrations already read stay beside it.
  const retryLoad = () => {
    if (integrationsQuery.isError) void integrationsQuery.refetch();
    if (typesQuery.isError) void typesQuery.refetch();
  };

  const [dialogOpen, setDialogOpen] = useState(false);
  // WHICH integration is being edited (null: a new one).  The integration
  // itself is the list's row, read when it is shown — a copy taken when the
  // dialog opened still said "a secret is stored" after it had been cleared.
  const [editingId, setEditingId] = useState<number | null>(null);
  const isEdit = editingId != null;
  const editing = isEdit ? integrations.find((r) => r.id === editingId) ?? null : null;
  const [form, setForm] = useState<IntegrationCreatePayload>(emptyForm);
  // Nessus-only: operator-supplied license cap (hosts per registered
  // Nessus scan).  Stored on save in `extra_config.max_hosts_per_scan`;
  // an agent reads it with the scanner (a fact, not an instruction).
  const [maxHostsPerScan, setMaxHostsPerScan] = useState<string>('');
  // OpenVAS/Greenbone-only: where gvmd listens for GMP. The Base URL is GSA
  // (the web UI), which can't verify a login, so Test connection
  // authenticates over GMP instead. Stored in `extra_config.gmp_port`.
  const [gmpPort, setGmpPort] = useState<string>('');
  // Test-connection state: result of the most recent `POST /integrations/test`.
  // Cleared whenever the form changes so a stale "ok" doesn't outlast
  // the input it referred to.
  const [testResult, setTestResult] = useState<IntegrationTestResult | null>(null);

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
    gmpPort,
  ]);

  /** Per-type extras the backend stores in `extra_config`: the Nessus license
   *  cap and the GVM GMP port (the port the connection test authenticates
   *  against). */
  const buildExtraConfig = (): Record<string, unknown> | undefined => {
    if (form.integration_type === 'nessus' && maxHostsPerScan.trim()) {
      return { max_hosts_per_scan: Number(maxHostsPerScan) };
    }
    if (form.integration_type === 'openvas' && gmpPort.trim()) {
      return { gmp_port: Number(gmpPort) };
    }
    return undefined;
  };

  const openNew = () => {
    setEditingId(null);
    setForm(emptyForm);
    setMaxHostsPerScan('');
    setGmpPort('');
    setTestResult(null);
    setDialogOpen(true);
  };
  const openEdit = (r: IntegrationEntry) => {
    setEditingId(r.id);
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
    const existingGmpPort = (r.extra_config || {})['gmp_port'];
    setGmpPort(existingGmpPort != null ? String(existingGmpPort) : '');
    setTestResult(null);
    setDialogOpen(true);
  };

  /** Pre-save connection test.  Hands the current form values to
   *  `POST /integrations/test`; result renders inline below the Test
   *  button regardless of outcome (the endpoint always returns 200
   *  with a tri-state `ok` field).  It SENDS the typed secrets: nothing of
   *  the request is kept once it has settled (`SECRET_MUTATION`, and the
   *  `reset` where it is called); the outcome shown is `testResult`. */
  const connectionTest = useMutation({
    ...SECRET_MUTATION,
    mutationFn: (payload: IntegrationCreatePayload) => testIntegrationConfig(payload),
    onMutate: () => setTestResult(null),
    onSuccess: (result) => setTestResult(result),
    // Network-level failure (e.g. the test endpoint itself errored).
    // Render as a failure so the user still sees something actionable.
    onError: (err, payload) => setTestResult({
      ok: false,
      integration_type: payload.integration_type,
      message: formatApiError(err, 'Test request failed.'),
      duration_ms: 0,
    }),
  });
  const testing = connectionTest.isPending;
  const handleTestConnection = () => connectionTest.mutate({
    ...form,
    base_url: form.base_url || undefined,
    secret: form.secret || undefined,
    secret2: form.secret2 || undefined,
    extra_config: buildExtraConfig(),
  }, { onSettled: () => connectionTest.reset() });

  const integrationsChanged = () => queryClient.invalidateQueries({ queryKey: ['listIntegrations'] });

  const save = useMutation({
    // It carries the secrets as typed: nothing of it is kept once it has
    // settled (`SECRET_MUTATION`, and the `reset` where it is called).
    ...SECRET_MUTATION,
    // What is saved is what was handed over with the click, not whatever the
    // form holds when the request is built.
    mutationFn: async ({ id, form, extraConfig }: {
      id: number | null;
      form: IntegrationCreatePayload;
      extraConfig: Record<string, unknown> | undefined;
    }): Promise<'updated' | 'added'> => {
      if (id != null) {
        const payload: any = {
          name: form.name,
          base_url: form.base_url || null,
          is_active: form.is_active,
        };
        if (form.secret) payload.secret = form.secret;
        if (form.secret2) payload.secret2 = form.secret2;
        if (extraConfig) payload.extra_config = extraConfig;
        await updateIntegration(id, payload);
        return 'updated';
      }
      await createIntegration({
        ...form,
        base_url: form.base_url || undefined,
        secret: form.secret || undefined,
        secret2: form.secret2 || undefined,
        extra_config: extraConfig,
      });
      return 'added';
    },
    onSuccess: (outcome) => {
      toast.success(outcome === 'updated' ? 'Integration updated.' : 'Integration added.');
      setDialogOpen(false);
      return integrationsChanged();
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to save integration.')),
  });
  const saving = save.isPending;
  const handleSave = () => save.mutate(
    { id: editingId, form, extraConfig: buildExtraConfig() },
    { onSettled: () => save.reset() },
  );

  // The edit dialog's "clear" beside a stored secret: removed at once, not on Save.
  const clearSecret = useMutation({
    mutationFn: ({ id, which }: { id: number; which: 'secret' | 'secret2' }) =>
      updateIntegration(id, which === 'secret' ? { clear_secret: true } : { clear_secret2: true }),
    onSuccess: (_updated, { which }) => {
      toast.success(which === 'secret' ? 'Primary secret cleared.' : 'Secondary secret cleared.');
      setForm((f) => ({ ...f, [which]: '' }));
      return integrationsChanged();
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to clear secret.')),
  });

  const remove = useMutation({
    mutationFn: (r: IntegrationEntry) => deleteIntegration(r.id),
    onSuccess: () => {
      toast.success('Integration deleted.');
      return integrationsChanged();
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to delete integration.')),
  });

  const handleDelete = async (r: IntegrationEntry) => {
    const ok = await confirm({
      title: 'Delete integration',
      body: 'The stored credentials will be permanently removed and agents, in every project, will no longer see this scanner. This cannot be undone.',
      resourceName: r.name,
      severity: 'danger',
      confirmLabel: 'Delete',
    });
    if (!ok) return;
    remove.mutate(r);
  };

  const labels = SECRET_LABELS[form.integration_type] || SECRET_LABELS.generic_api;
  const urlError = validateBaseUrl(form.base_url);

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-col gap-xs sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-page-title">Scanner Integrations</h1>
          <p className="mt-xxs text-metadata text-muted-foreground">
            The scanners this installation has configured (Nessus, OpenVAS, Nuclei, Burp, etc),
            for every project. Secrets are encrypted at rest and shown to no one here. An agent
            can see that a scanner is configured and is told to ask its operator before using it;
            it is given that scanner's credentials only when it then requests them, and every
            request is recorded.
            {!canManage && ' A global administrator adds and changes them.'}
          </p>
        </div>
        {canManage && (
          <Button onClick={openNew}>
            <Plus className="size-4" aria-hidden /> Add Integration
          </Button>
        )}
      </div>

      {error && (
        <p role="alert" className="mb-md break-words text-metadata text-destructive">
          {error}{' '}
          <button type="button" className="text-info hover:underline" onClick={retryLoad}>Retry</button>
        </p>
      )}

      {loading ? (
        <CardListSkeleton count={3} cardHeight={180} />
      ) : integrations.length === 0 ? !error && (
        <Card>
          <CardContent className="flex flex-col items-center gap-sm p-xxl text-center">
            <KeyRound className="size-12 text-muted-foreground" aria-hidden />
            <p className="text-metadata text-muted-foreground">No integrations configured yet.</p>
            {canManage && (
              <>
                <p className="text-caption text-muted-foreground">
                  Add one so that agents can see it and ask to use it.
                </p>
                <Button onClick={openNew}>
                  <Plus className="size-4" aria-hidden /> Add Your First Integration
                </Button>
              </>
            )}
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
                </div>
                <p className="mt-xs truncate text-caption text-muted-foreground">
                  Configured by {r.created_by ?? 'an account that has since been removed'}
                </p>
                {canManage && (<>
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
                </>)}
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Create / Edit dialog */}
      <Dialog open={dialogOpen} onOpenChange={(next) => !next && !saving && setDialogOpen(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{isEdit ? 'Edit Integration' : 'Add Integration'}</DialogTitle>
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
                disabled={isEdit}
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
                {isEdit ? `${labels.one} (leave blank to keep current)` : labels.one}
              </Label>
              <PasswordInput
                id="int-secret"
                value={form.secret || ''}
                onChange={(e) => setForm((f) => ({ ...f, secret: e.target.value }))}
                onClear={
                  editing && editing.has_secret
                    ? () => clearSecret.mutate({ id: editing.id, which: 'secret' })
                    : undefined
                }
                clearTooltip="Remove the stored primary secret"
              />
              <p className="text-caption text-muted-foreground">{labels.help1}</p>
            </div>
            {labels.two && (
              <div className="flex flex-col gap-xs">
                <Label htmlFor="int-secret2">
                  {isEdit ? `${labels.two} (leave blank to keep current)` : labels.two}
                </Label>
                <PasswordInput
                  id="int-secret2"
                  value={form.secret2 || ''}
                  onChange={(e) => setForm((f) => ({ ...f, secret2: e.target.value }))}
                  onClear={
                    editing && editing.has_secret2
                      ? () => clearSecret.mutate({ id: editing.id, which: 'secret2' })
                      : undefined
                  }
                  clearTooltip="Remove the stored secondary secret"
                />
                <p className="text-caption text-muted-foreground">{labels.help2}</p>
              </div>
            )}
            {/* Nessus-only license cap (v2.49.4).  Lives in
                extra_config.max_hosts_per_scan; an agent reads the
                figure with the scanner. */}
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
                  256 / 512 / 1024).  An agent sees this figure with the
                  scanner.  Leave blank if unknown.
                </p>
              </div>
            )}
            {/* OpenVAS/Greenbone (v5.208.0).  Classic GVM verifies a login
                only over GMP — the Base URL above is GSA, the web UI — so the
                connection test dials gvmd on this port. */}
            {form.integration_type === 'openvas' && (
              <div className="flex flex-col gap-xs">
                <Label htmlFor="int-gmp-port">
                  GMP port <span className="text-muted-foreground">(optional)</span>
                </Label>
                <Input
                  id="int-gmp-port"
                  type="number"
                  min={1}
                  max={65535}
                  inputMode="numeric"
                  value={gmpPort}
                  onChange={(e) => setGmpPort(e.target.value)}
                  placeholder="9390"
                />
                <p className="text-caption text-muted-foreground">
                  Where gvmd listens for GMP (default 9390). Test connection authenticates the
                  username and password there, because the GSA web UI in Base URL can't verify
                  them. If gvmd listens only on a unix socket, the test says so and you can
                  still save.
                </p>
              </div>
            )}
            <div className="flex items-center gap-xs">
              <Switch
                id="int-active"
                checked={!!form.is_active}
                onCheckedChange={(v) => setForm((f) => ({ ...f, is_active: Boolean(v) }))}
              />
              <Label htmlFor="int-active">Active (an inactive scanner is not shown to agents, and its credentials cannot be requested)</Label>
            </div>

            {/* Pre-save connection test (v2.49.4).  Probe-by-type:
                Nessus, Ollama and OpenVAS/Greenbone are implemented;
                other types return an honest "not yet implemented" so
                the button is universal.  Result clears the moment any
                form field changes (see the useEffect above). */}
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
