import React, { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { copyToClipboard } from '../utils/clipboard';
import { downloadTextFile } from '../utils/download';
import { useNavigate } from 'react-router-dom';
import {
  Bot,
  Copy,
  Download,
  ExternalLink,
  Loader2,
  RefreshCw,
  Sparkles,
  X as XIcon,
} from 'lucide-react';
import {
  draftReportWithAI,
  listLLMProviders,
  type DraftReportRequest,
  type DraftReportResponse,
  type LLMProviderEntry,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { useProjectId } from '../hooks/useProjectId';
import { queryErrorText } from '../lib/query';
import { asAxiosError, formatApiError } from '../utils/apiErrors';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { InlineLoader } from './ui/inline-loader';
import { Input } from './ui/input';
import { Label } from './ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';
import { Textarea } from './ui/textarea';

interface AiDraftReportDialogProps {
  open: boolean;
  onClose: () => void;
  /** v5.261.0 — hand the (edited) draft to the caller, e.g. a report's
   *  executive summary, which the person then edits and saves. */
  onUse?: (markdown: string) => void;
  useLabel?: string;
}

/**
 * Elapsed-seconds counter isolated so its 1s tick doesn't re-render the
 * surrounding form / draft viewer.
 */
const ElapsedSeconds: React.FC<{ startedAt: number }> = ({ startedAt }) => {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  return <>{Math.max(0, Math.floor((now - startedAt) / 1000))}s</>;
};

/** The operator pressed Cancel: said as such, never as a failure. */
const wasCancelled = (err: unknown): boolean => {
  const e = asAxiosError(err);
  return e.name === 'CanceledError' || e.code === 'ERR_CANCELED';
};

/**
 * "Draft with AI (beta)" — asks a configured LLM provider to draft a markdown
 * report from the project's promoted findings, then hands the raw markdown to
 * the operator in an editable textarea. The AI drafts; the human owns the final
 * text (copy / download .md).
 */
const AiDraftReportDialog: React.FC<AiDraftReportDialogProps> = ({ open, onClose, onUse, useLabel }) => {
  const navigate = useNavigate();
  const toast = useToast();
  const projectId = useProjectId();

  // The providers are the installation's, read each time the dialog opens.
  const providersQuery = useQuery({
    queryKey: ['listLLMProviders'],
    queryFn: ({ signal }) => listLLMProviders(signal),
    enabled: open,
  });
  const providers: LLMProviderEntry[] = providersQuery.data ?? [];
  const providersLoaded = !providersQuery.isFetching;
  const providersError = queryErrorText(providersQuery.error, 'Failed to load LLM providers.');
  const loadProviders = () => { void providersQuery.refetch(); };
  // The default provider until the operator picks another.
  const [chosenProvider, setChosenProvider] = useState<number | ''>('');
  const defaultProviderId: number | '' = providers.length > 0
    ? (providers.find((prov) => prov.is_default) ?? providers[0]).id
    : '';
  const providerId = chosenProvider !== '' ? chosenProvider : defaultProviderId;

  const [audience, setAudience] = useState('');
  const [instructions, setInstructions] = useState('');

  // Cancel is the operator's: the request in flight is theirs to stop.
  const abortRef = useRef<AbortController | null>(null);
  const generate = useMutation({
    mutationFn: (request: DraftReportRequest) => {
      const controller = new AbortController();
      abortRef.current = controller;
      return draftReportWithAI(projectId, request, { signal: controller.signal });
    },
    onMutate: () => setEdited(null),
    onSuccess: (res) => {
      toast.success(`Draft ready — built from ${res.finding_total} finding${res.finding_total === 1 ? '' : 's'}.`);
    },
    onError: (err) => {
      if (wasCancelled(err)) toast.info('Draft cancelled.');
    },
    onSettled: () => { abortRef.current = null; },
  });
  const loading = generate.isPending;
  const loadingStartedAt = loading ? generate.submittedAt : null;
  const result: DraftReportResponse | null = generate.data ?? null;
  // Backend `detail` (400 user-fixable / 502 provider failure) is surfaced
  // verbatim by formatApiError.
  const error = generate.error && !wasCancelled(generate.error)
    ? formatApiError(generate.error, 'Failed to draft the report.')
    : null;
  // The operator-owned, editable copy of the draft: the model's output until
  // the human edits it.
  const [edited, setEdited] = useState<string | null>(null);
  const draft = edited ?? result?.content ?? '';
  const setDraft = setEdited;

  // A reopen doesn't show a stale draft, error or choice of provider.
  const resetGenerate = generate.reset;
  useEffect(() => {
    if (!open) return;
    resetGenerate();
    setEdited(null);
    setChosenProvider('');
  }, [open, resetGenerate]);

  const handleGenerate = () => {
    generate.mutate({
      provider_id: providerId === '' ? undefined : providerId,
      audience: audience.trim() || undefined,
      instructions: instructions.trim() || undefined,
    });
  };

  const handleCancel = () => abortRef.current?.abort();

  const handleCopy = () => {
    if (!draft) return;
    copyToClipboard(draft).then((ok) =>
      ok
        ? toast.success('Draft copied to clipboard.')
        : toast.warning('Could not copy — try over HTTPS or select the text manually.'),
    );
  };

  const handleDownload = () => {
    if (!draft) return;
    downloadTextFile(`ai-draft-report_${new Date().toISOString().split('T')[0]}.md`, draft, 'text/markdown');
  };

  const noProviders = providersLoaded && !providersError && providers.length === 0;

  return (
    <Dialog open={open} onOpenChange={(v) => !v && !loading && onClose()}>
      <DialogContent size="xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-xs">
            <Sparkles className="size-5" aria-hidden />
            Draft report with AI
            <Badge variant="outline">beta</Badge>
          </DialogTitle>
        </DialogHeader>

        {/* Body scrolls inside the frame per style guide §11 — the header stays
            pinned and the page never overflows horizontally. */}
        <div className="min-w-0 space-y-sm overflow-y-auto">
          <p className="text-caption text-muted-foreground">
            A configured LLM drafts a narrative report from this project&apos;s promoted findings.
            The draft is <strong>yours to edit</strong> — nothing is saved or sent anywhere until
            you copy or download it.
          </p>

          {!providersLoaded && <InlineLoader label="Loading LLM providers…" size="sm" />}

          {providersLoaded && providersError && (
            <Alert variant="destructive">
              <AlertDescription className="flex flex-wrap items-center justify-between gap-sm">
                <span className="min-w-0 break-words">{providersError}</span>
                <Button size="sm" variant="outline" onClick={loadProviders}>
                  <RefreshCw className="size-3.5" aria-hidden />
                  Retry
                </Button>
              </AlertDescription>
            </Alert>
          )}

          {noProviders && (
            <Alert variant="info">
              <AlertDescription className="flex flex-wrap items-center justify-between gap-sm">
                <span className="min-w-0">
                  No LLM providers configured. Add one in <strong>LLM Providers</strong> to draft a
                  report in-app.
                </span>
                <Button
                  size="sm"
                  onClick={() => {
                    onClose();
                    navigate('/llm-settings');
                  }}
                >
                  <ExternalLink className="size-3.5" aria-hidden />
                  Configure
                </Button>
              </AlertDescription>
            </Alert>
          )}

          {providersLoaded && !providersError && providers.length > 0 && (
            <>
              <div className="grid grid-cols-1 gap-sm sm:grid-cols-2">
                <div className="space-y-xxs">
                  <Label htmlFor="ai-draft-provider">Provider</Label>
                  <Select
                    value={providerId === '' ? '' : String(providerId)}
                    onValueChange={(v) => setChosenProvider(v ? Number(v) : '')}
                    disabled={loading}
                  >
                    <SelectTrigger id="ai-draft-provider">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {providers.map((prov) => (
                        <SelectItem key={prov.id} value={String(prov.id)}>
                          {prov.name}
                          {prov.is_default ? ' (default)' : ''}
                          {prov.model_id ? ` · ${prov.model_id}` : ''}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-xxs">
                  <Label htmlFor="ai-draft-audience">Audience (optional)</Label>
                  <Input
                    id="ai-draft-audience"
                    value={audience}
                    onChange={(e) => setAudience(e.target.value)}
                    disabled={loading}
                    placeholder="e.g. executive summary, technical remediation team"
                  />
                </div>
              </div>

              <div className="space-y-xxs">
                <Label htmlFor="ai-draft-instructions">Instructions (optional)</Label>
                <Textarea
                  id="ai-draft-instructions"
                  value={instructions}
                  onChange={(e) => setInstructions(e.target.value)}
                  disabled={loading}
                  rows={3}
                  placeholder="Steer tone, structure, or emphasis — e.g. 'lead with business risk, keep it under two pages'."
                />
              </div>

              <div className="flex flex-wrap items-center gap-xs">
                <Button onClick={handleGenerate} disabled={loading || providerId === ''}>
                  {loading ? (
                    <Loader2 className="size-4 animate-spin" aria-hidden />
                  ) : (
                    <Bot className="size-4" aria-hidden />
                  )}
                  {loading && loadingStartedAt !== null ? (
                    <>
                      Drafting… (<ElapsedSeconds startedAt={loadingStartedAt} />)
                    </>
                  ) : result ? (
                    'Redraft'
                  ) : (
                    'Generate draft'
                  )}
                </Button>
                {loading && (
                  <Button
                    variant="outline"
                    onClick={handleCancel}
                    aria-label="Cancel the running draft"
                  >
                    <XIcon className="size-4" aria-hidden />
                    Cancel
                  </Button>
                )}
                <span className="text-caption text-muted-foreground">
                  Drafting can take 30–60s.
                </span>
              </div>

              {error && (
                <Alert variant="destructive">
                  <AlertDescription className="break-words">{error}</AlertDescription>
                </Alert>
              )}

              {result && (
                <div className="space-y-xxs">
                  <div className="flex flex-wrap items-center justify-between gap-xs">
                    <p className="min-w-0 break-words text-caption text-muted-foreground">
                      Drafted from <strong>{result.finding_total}</strong> finding
                      {result.finding_total === 1 ? '' : 's'} ·{' '}
                      <code className="font-mono">{result.provider_type}</code>
                      {result.model_id && (
                        <>
                          {'/'}
                          <code className="font-mono">{result.model_id}</code>
                        </>
                      )}
                    </p>
                    <div className="flex shrink-0 items-center gap-xs">
                      <Button size="sm" variant="outline" onClick={handleCopy} disabled={!draft}>
                        <Copy className="size-3.5" aria-hidden />
                        Copy
                      </Button>
                      <Button size="sm" variant="outline" onClick={handleDownload} disabled={!draft}>
                        <Download className="size-3.5" aria-hidden />
                        Download .md
                      </Button>
                      {onUse && (
                        <Button size="sm" onClick={() => { onUse(draft); onClose(); }} disabled={!draft.trim()}>
                          {useLabel ?? 'Use this text'}
                        </Button>
                      )}
                    </div>
                  </div>
                  <Label htmlFor="ai-draft-content" className="sr-only">
                    Drafted report (editable)
                  </Label>
                  <Textarea
                    id="ai-draft-content"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    className="max-h-[45vh] min-h-64 overflow-auto font-mono text-caption"
                    spellCheck={false}
                  />
                  <p className="text-caption text-muted-foreground">
                    This is a draft — review and edit before sharing.
                  </p>
                </div>
              )}
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default AiDraftReportDialog;
