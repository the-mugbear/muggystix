import React, { useCallback, useEffect, useState } from 'react';
import {
  Plus,
  Pencil,
  Trash2,
  Wifi,
  Star,
  Loader2,
} from 'lucide-react';
import {
  listLLMProviders,
  listLLMProviderTypes,
  createLLMProvider,
  updateLLMProvider,
  deleteLLMProvider,
  testLLMProvider,
  LLMProviderEntry,
  LLMProviderTypeOption,
  LLMProviderCreatePayload,
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

const MODEL_HINTS: Record<string, string> = {
  openai: 'gpt-4o-mini, gpt-4o, gpt-4.1…',
  anthropic: 'claude-sonnet-4-6, claude-opus-4-6, claude-haiku-4-5-20251001',
  ollama: 'llama3, qwen2.5, mistral, devstral…',
  azure_openai: '(your Azure deployment name)',
  openai_compatible: 'model id your endpoint expects',
};

const BASE_URL_HINTS: Record<string, string> = {
  openai: 'https://api.openai.com (leave blank for default)',
  anthropic: 'https://api.anthropic.com (leave blank for default)',
  ollama: 'http://localhost:11434 (or your LAN Ollama host)',
  azure_openai: 'https://<resource>.openai.azure.com',
  openai_compatible: 'https://your-endpoint/v1',
};

const PROVIDER_NEEDS_KEY: Record<string, boolean> = {
  openai: true,
  anthropic: true,
  azure_openai: true,
  openai_compatible: true,
  ollama: false,
};

const emptyForm: LLMProviderCreatePayload = {
  name: '',
  provider_type: 'openai',
  base_url: '',
  model_id: '',
  api_key: '',
  is_default: false,
};

const LLMSettings: React.FC = () => {
  const toast = useToast();
  const [confirmEl, confirm] = useConfirm();
  const [providers, setProviders] = useState<LLMProviderEntry[]>([]);
  const [types, setTypes] = useState<LLMProviderTypeOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<LLMProviderEntry | null>(null);
  const [form, setForm] = useState<LLMProviderCreatePayload>(emptyForm);
  const [saving, setSaving] = useState(false);
  const [testingId, setTestingId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [list, t] = await Promise.all([listLLMProviders(), listLLMProviderTypes()]);
      setProviders(list);
      setTypes(t);
    } catch (err: unknown) {
      const msg = formatApiError(err, 'Failed to load LLM providers.');
      setError(msg);
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  const openNew = () => {
    setEditing(null);
    setForm(emptyForm);
    setDialogOpen(true);
  };
  const openEdit = (p: LLMProviderEntry) => {
    setEditing(p);
    setForm({
      name: p.name,
      provider_type: p.provider_type,
      base_url: p.base_url || '',
      model_id: p.model_id || '',
      api_key: '',
      is_default: p.is_default,
    });
    setDialogOpen(true);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      if (editing) {
        const payload: any = {
          name: form.name,
          base_url: form.base_url || null,
          model_id: form.model_id || null,
          is_default: form.is_default,
        };
        if (form.api_key) payload.api_key = form.api_key;
        await updateLLMProvider(editing.id, payload);
        toast.success('Provider updated.');
      } else {
        await createLLMProvider({
          ...form,
          base_url: form.base_url || undefined,
          model_id: form.model_id || undefined,
          api_key: form.api_key || undefined,
        });
        toast.success('Provider added.');
      }
      setDialogOpen(false);
      await load();
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to save provider.'));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (p: LLMProviderEntry) => {
    const ok = await confirm({
      title: 'Delete LLM provider',
      body: 'The stored API key will be permanently removed. Any in-app agent runs using this provider will stop working until you reconfigure it.',
      resourceName: p.name,
      severity: 'danger',
      confirmLabel: 'Delete',
    });
    if (!ok) return;
    try {
      await deleteLLMProvider(p.id);
      toast.success('Provider deleted.');
      await load();
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to delete provider.'));
    }
  };

  const handleTest = async (p: LLMProviderEntry) => {
    setTestingId(p.id);
    try {
      const result = await testLLMProvider(p.id);
      if (result.ok) {
        toast.success(`${p.name}: ${result.detail}`);
      } else {
        toast.error(`${p.name}: ${result.detail}`);
      }
    } catch (err: unknown) {
      toast.error(formatApiError(err, `${p.name} test failed`));
    } finally {
      setTestingId(null);
    }
  };

  const needsKey = PROVIDER_NEEDS_KEY[form.provider_type] ?? true;
  const urlError = validateBaseUrl(form.base_url);

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-col gap-xs sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-page-title">LLM Providers</h1>
          <p className="mt-xxs text-metadata text-muted-foreground">
            Configure API keys and base URLs for hosted and local LLMs. API keys are encrypted at
            rest with a key derived from the app's SECRET_KEY.
          </p>
        </div>
        <Button onClick={openNew}>
          <Plus className="size-4" aria-hidden /> Add Provider
        </Button>
      </div>

      {error && (
        <Alert variant="destructive" className="mb-md">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {loading ? (
        <CardListSkeleton count={3} cardHeight={200} />
      ) : providers.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-sm p-xxl text-center">
            <p className="text-metadata text-muted-foreground">No LLM providers configured yet.</p>
            <p className="text-caption text-muted-foreground">
              Add one to enable the in-app agent runtime. Cloud providers (OpenAI, Anthropic)
              require API keys; Ollama runs locally and needs only a base URL.
            </p>
            <Button onClick={openNew}>
              <Plus className="size-4" aria-hidden /> Add Your First Provider
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-md sm:grid-cols-2 lg:grid-cols-3">
          {providers.map((p) => (
            <Card key={p.id}>
              <CardContent className="p-md">
                <div className="mb-xs flex items-start justify-between gap-xs">
                  <div className="min-w-0">
                    <p className="truncate text-subheading font-semibold">{p.name}</p>
                    <p className="text-caption text-muted-foreground">{p.provider_type}</p>
                  </div>
                  {p.is_default && (
                    <Badge variant="default">
                      <Star className="size-3" aria-hidden /> default
                    </Badge>
                  )}
                </div>
                {p.base_url && (
                  <p className="text-metadata break-words">
                    <strong className="text-foreground">URL:</strong> {p.base_url}
                  </p>
                )}
                {p.model_id && (
                  <p className="text-metadata break-words">
                    <strong className="text-foreground">Model:</strong> {p.model_id}
                  </p>
                )}
                <Badge variant={p.has_api_key ? 'success' : 'muted'} className="mt-xs">
                  {p.has_api_key ? 'API key set' : 'No API key'}
                </Badge>
                <Separator className="my-sm" />
                <div className="flex gap-xxs">
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => handleTest(p)}
                        disabled={testingId === p.id}
                        aria-label={`Test connection to ${p.name}`}
                      >
                        {testingId === p.id ? (
                          <Loader2 className="size-4 animate-spin" aria-hidden />
                        ) : (
                          <Wifi className="size-4" aria-hidden />
                        )}
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Test connection</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => openEdit(p)}
                        aria-label={`Edit provider ${p.name}`}
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
                        onClick={() => handleDelete(p)}
                        aria-label={`Delete provider ${p.name}`}
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
            <DialogTitle>{editing ? 'Edit LLM Provider' : 'Add LLM Provider'}</DialogTitle>
          </DialogHeader>
          <div className="flex flex-col gap-md">
            <div className="flex flex-col gap-xs">
              <Label htmlFor="llm-name">Name</Label>
              <Input
                id="llm-name"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                autoFocus
                required
              />
              <p className="text-caption text-muted-foreground">
                Human-readable label like "Work OpenAI" or "Home Ollama".
              </p>
            </div>
            <div className="flex flex-col gap-xs">
              <Label htmlFor="llm-type">Provider Type</Label>
              <Select
                value={form.provider_type}
                onValueChange={(v) => setForm((f) => ({ ...f, provider_type: v }))}
                disabled={!!editing}
              >
                <SelectTrigger id="llm-type">
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
              <Label htmlFor="llm-url">Base URL</Label>
              <Input
                id="llm-url"
                value={form.base_url || ''}
                onChange={(e) => setForm((f) => ({ ...f, base_url: e.target.value }))}
                placeholder={BASE_URL_HINTS[form.provider_type] || ''}
                aria-invalid={!!urlError}
                aria-describedby="llm-url-help"
              />
              <p
                id="llm-url-help"
                role={urlError ? 'alert' : undefined}
                className={`text-caption ${urlError ? 'text-destructive' : 'text-muted-foreground'}`}
              >
                {urlError || BASE_URL_HINTS[form.provider_type] || 'Optional'}
              </p>
            </div>
            <div className="flex flex-col gap-xs">
              <Label htmlFor="llm-model">Model ID</Label>
              <Input
                id="llm-model"
                value={form.model_id || ''}
                onChange={(e) => setForm((f) => ({ ...f, model_id: e.target.value }))}
                placeholder={MODEL_HINTS[form.provider_type] || ''}
              />
              <p className="text-caption text-muted-foreground">
                {MODEL_HINTS[form.provider_type] || ''}
              </p>
            </div>
            {needsKey && (
              <div className="flex flex-col gap-xs">
                <Label htmlFor="llm-key">
                  {editing ? 'API Key (leave blank to keep current)' : 'API Key'}
                </Label>
                <PasswordInput
                  id="llm-key"
                  value={form.api_key || ''}
                  onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value }))}
                  onClear={
                    editing && editing.has_api_key
                      ? async () => {
                          try {
                            await updateLLMProvider(editing.id, { clear_api_key: true });
                            toast.success('API key cleared.');
                            setForm((f) => ({ ...f, api_key: '' }));
                            await load();
                          } catch (err: unknown) {
                            toast.error(formatApiError(err, 'Failed to clear API key.'));
                          }
                        }
                      : undefined
                  }
                  clearTooltip="Remove the stored API key"
                />
                <p className="text-caption text-muted-foreground">
                  Stored encrypted; never returned in API responses.
                </p>
              </div>
            )}
            <div className="flex items-center gap-xs">
              <Switch
                id="llm-default"
                checked={!!form.is_default}
                onCheckedChange={(v) => setForm((f) => ({ ...f, is_default: Boolean(v) }))}
              />
              <Label htmlFor="llm-default">Use as default provider</Label>
            </div>
          </div>
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

export default LLMSettings;
