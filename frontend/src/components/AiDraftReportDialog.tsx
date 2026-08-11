import React, { useCallback, useEffect, useRef, useState } from 'react';
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
  type DraftReportResponse,
  type LLMProviderEntry,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
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
}

/**
 * Elapsed-seconds counter isolated so its 1s tick doesn't re-render the
 * surrounding form / draft viewer (mirrors InAppAgentPanel's ElapsedSeconds).
 */
const ElapsedSeconds: React.FC<{ startedAt: number }> = ({ startedAt }) => {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  return <>{Math.max(0, Math.floor((now - startedAt) / 1000))}s</>;
};

/**
 * "Draft with AI (beta)" — asks a configured LLM provider to draft a markdown
 * report from the project's promoted findings, then hands the raw markdown to
 * the operator in an editable textarea. The AI drafts; the human owns the final
 * text (copy / download .md).
 */
const AiDraftReportDialog: React.FC<AiDraftReportDialogProps> = ({ open, onClose }) => {
  const navigate = useNavigate();
  const toast = useToast();

  const [providers, setProviders] = useState<LLMProviderEntry[]>([]);
  const [providersLoaded, setProvidersLoaded] = useState(false);
  const [providersError, setProvidersError] = useState<string | null>(null);
  const [providerId, setProviderId] = useState<number | ''>('');

  const [audience, setAudience] = useState('');
  const [instructions, setInstructions] = useState('');

  const [loading, setLoading] = useState(false);
  const [loadingStartedAt, setLoadingStartedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<DraftReportResponse | null>(null);
  // The operator-owned, editable copy of the draft. Seeded from the model's
  // output, then diverges as the human edits.
  const [draft, setDraft] = useState('');

  const abortRef = useRef<AbortController | null>(null);

  const loadProviders = useCallback(async () => {
    setProvidersError(null);
    try {
      const list = await listLLMProviders();
      setProviders(list);
      const def = list.find((prov) => prov.is_default) || list[0];
      if (def) setProviderId(def.id);
    } catch (err: unknown) {
      setProvidersError(formatApiError(err, 'Failed to load LLM providers.'));
    } finally {
      setProvidersLoaded(true);
    }
  }, []);

  // Load providers when the dialog opens; reset transient state so a reopen
  // doesn't show a stale draft or error.
  useEffect(() => {
    if (!open) return;
    setError(null);
    setResult(null);
    setDraft('');
    setProvidersLoaded(false);
    loadProviders();
  }, [open, loadProviders]);

  useEffect(() => {
    setLoadingStartedAt(loading ? Date.now() : null);
  }, [loading]);

  const handleGenerate = async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    abortRef.current = new AbortController();
    try {
      const res = await draftReportWithAI(
        {
          provider_id: providerId === '' ? undefined : (providerId as number),
          audience: audience.trim() || undefined,
          instructions: instructions.trim() || undefined,
        },
        { signal: abortRef.current.signal },
      );
      setResult(res);
      setDraft(res.content);
      toast.success(`Draft ready — built from ${res.finding_total} finding${res.finding_total === 1 ? '' : 's'}.`);
    } catch (err: unknown) {
      const e = asAxiosError(err);
      if (e.name === 'CanceledError' || e.code === 'ERR_CANCELED') {
        toast.info('Draft cancelled.');
      } else {
        // Backend `detail` (400 user-fixable / 502 provider failure) is surfaced
        // verbatim by formatApiError.
        setError(formatApiError(err, 'Failed to draft the report.'));
      }
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  };

  const handleCancel = () => abortRef.current?.abort();

  const handleCopy = () => {
    if (!draft) return;
    navigator.clipboard.writeText(draft).then(
      () => toast.success('Draft copied to clipboard.'),
      () => toast.warning('Could not copy to clipboard.'),
    );
  };

  const handleDownload = () => {
    if (!draft) return;
    const blob = new Blob([draft], { type: 'text/markdown' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `ai-draft-report_${new Date().toISOString().split('T')[0]}.md`;
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
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
                    onValueChange={(v) => setProviderId(v ? Number(v) : '')}
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
