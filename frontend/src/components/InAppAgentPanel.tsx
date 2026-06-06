import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Bot, Check, Copy, ExternalLink, Loader2, RefreshCw, X as XIcon } from 'lucide-react';
import {
  listLLMProviders,
  llmComplete,
  LLMProviderEntry,
  LLMCompletionResponse,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { asAxiosError, formatApiError } from '../utils/apiErrors';
import { sanitizePromptForLlm } from '../utils/promptSanitizer';
import { Alert, AlertDescription } from './ui/alert';
import { Button } from './ui/button';
import { Label } from './ui/label';
import { InlineLoader } from './ui/inline-loader';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';

interface Props {
  /** The full prompt to hand to the LLM. Usually the "instructions"
   *  markdown emitted by the backend plan / execution / recon flows. */
  prompt: string;
  /** Optional system message prepended to the prompt. */
  system?: string;
  /** Human-readable label for surrounding context (used in toasts). */
  contextLabel?: string;
}

/**
 * Audit PRF·L5: extracted the elapsed-seconds counter into its own
 * component so the 1s setInterval only re-renders this small leaf
 * instead of the whole panel (which previously caused selects, alerts,
 * and the response viewer to re-render every tick).
 */
const ElapsedSeconds: React.FC<{ startedAt: number }> = ({ startedAt }) => {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000));
  return <>{seconds}s</>;
};

/**
 * Reusable widget that lets the user send a prompt to their default
 * LLM provider without leaving the dialog. Respects the architectural
 * rule that agents are *coordinators*: the LLM's response is surfaced
 * as read-only text for the user to review; no commands are executed
 * automatically.
 */
const InAppAgentPanel: React.FC<Props> = ({ prompt, system }) => {
  const navigate = useNavigate();
  const toast = useToast();
  const [providers, setProviders] = useState<LLMProviderEntry[]>([]);
  // Audit M24: providersLoaded ref distinguishes "fetch in flight,
  // we don't know yet whether the user has providers" from "fetch
  // resolved, list is genuinely empty".  Pre-audit the panel
  // flashed the "No providers" alert during the fetch.
  const [providersLoaded, setProvidersLoaded] = useState(false);
  // Audit FBK·H14: a failed providers fetch previously only emitted
  // console.warn, which produced a false-empty "No LLM providers
  // configured" UI even when the backend was down or the user lacked
  // permission. Track the error and surface it instead of the empty
  // state.
  const [providersError, setProvidersError] = useState<string | null>(null);
  const [providerId, setProviderId] = useState<number | ''>('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<LLMCompletionResponse | null>(null);
  const [copied, setCopied] = useState(false);
  // Wall-clock counter — completions on Opus / GPT-4 routinely take
  // 30-120s.  A static 4-px spinner with no elapsed indicator made
  // the user think the panel was hung (audit C9).
  const [loadingStartedAt, setLoadingStartedAt] = useState<number | null>(null);
  // AbortController so the user can cancel mid-completion.  axios
  // accepts a `signal`; aborting throws CanceledError which we
  // suppress in the catch block.
  const abortRef = useRef<AbortController | null>(null);

  const loadProviders = useCallback(async () => {
    setProvidersError(null);
    try {
      const list = await listLLMProviders();
      setProviders(list);
      const def = list.find((p) => p.is_default) || list[0];
      if (def) setProviderId(def.id);
    } catch (err: unknown) {
      console.warn('Failed to load LLM providers:', err);
      setProvidersError(formatApiError(err, 'Failed to load LLM providers.'));
    } finally {
      setProvidersLoaded(true);
    }
  }, []);

  useEffect(() => {
    loadProviders();
  }, [loadProviders]);

  useEffect(() => {
    if (loading) {
      setLoadingStartedAt(Date.now());
    } else {
      setLoadingStartedAt(null);
    }
  }, [loading]);

  const handleRun = async () => {
    if (providerId === '' || !prompt) return;
    setLoading(true);
    setError(null);
    setResult(null);
    abortRef.current = new AbortController();
    try {
      // Code review critical #1: sanitize before the prompt leaves the
      // browser.  The full unredacted instructions remain available
      // for the copy-paste-to-terminal flow above this panel; only the
      // in-app LLM path gets the redacted version so agent API keys
      // and inlined scanner credentials never land in a hosted
      // provider's request log.
      const sanitized = sanitizePromptForLlm(prompt);
      const res = await llmComplete(
        providerId as number,
        {
          system,
          prompt: sanitized,
          max_tokens: 4096,
          temperature: 0.3,
        },
        { signal: abortRef.current.signal },
      );
      setResult(res);
      toast.success(`Response received from ${res.provider_name}.`);
    } catch (err: unknown) {
      // Suppress error display when the user aborted; the cancel
      // button is its own feedback.
      const e = asAxiosError(err);
      if (e.name === 'CanceledError' || e.code === 'ERR_CANCELED') {
        toast.info('Cancelled.');
      } else {
        setError(formatApiError(err, 'Failed to call LLM provider.'));
      }
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  };

  const handleCancel = () => {
    abortRef.current?.abort();
  };

  const handleCopy = () => {
    if (!result?.content) return;
    navigator.clipboard.writeText(result.content).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      },
      () => {
        toast.warning('Could not copy.');
      },
    );
  };

  // Render nothing while the initial provider fetch is in flight so
  // the "No providers configured" alert doesn't flash before settling.
  if (!providersLoaded) {
    return <InlineLoader label="Loading LLM providers…" size="sm" />;
  }

  if (providers.length === 0) {
    // Audit FBK·H14: separate "fetch failed" from "fetch succeeded
    // with no providers". The fetch-failed case should NOT prompt the
    // user to configure — the configuration may already be there but
    // unreachable. Offer Retry instead.
    if (providersError) {
      return (
        <Alert variant="destructive">
          <AlertDescription className="flex flex-wrap items-center justify-between gap-sm">
            <span>{providersError}</span>
            <Button size="sm" variant="outline" onClick={loadProviders}>
              <RefreshCw className="size-3.5" aria-hidden />
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      );
    }
    return (
      <Alert variant="info">
        <AlertDescription className="flex flex-wrap items-center justify-between gap-sm">
          <span>
            No LLM providers configured. Add one in <strong>LLM Providers</strong> to run this
            prompt against an in-app agent instead of pasting it into an external terminal.
          </span>
          <Button size="sm" onClick={() => navigate('/llm-settings')}>
            <ExternalLink className="size-3.5" aria-hidden />
            Configure
          </Button>
        </AlertDescription>
      </Alert>
    );
  }

  const selectedProvider = providers.find((p) => p.id === providerId);

  return (
    <div className="space-y-xs">
      <div className="flex flex-col gap-xs sm:flex-row sm:items-end">
        <div className="space-y-xxs sm:min-w-[14rem]">
          <Label htmlFor="in-app-agent-provider">Provider</Label>
          <Select
            value={providerId === '' ? '' : String(providerId)}
            onValueChange={(v) => setProviderId(v ? Number(v) : '')}
            disabled={loading}
          >
            <SelectTrigger id="in-app-agent-provider">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {providers.map((p) => (
                <SelectItem key={p.id} value={String(p.id)}>
                  {p.name}
                  {p.is_default ? ' (default)' : ''}
                  {p.model_id ? ` · ${p.model_id}` : ''}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button onClick={handleRun} disabled={loading || providerId === ''}>
          {loading ? (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          ) : (
            <Bot className="size-4" aria-hidden />
          )}
          {loading && loadingStartedAt !== null ? (
            <>
              Running… (<ElapsedSeconds startedAt={loadingStartedAt} />)
            </>
          ) : (
            'Run in-app'
          )}
        </Button>
        {loading && (
          <Button variant="outline" onClick={handleCancel} aria-label="Cancel running completion">
            <XIcon className="size-4" aria-hidden />
            Cancel
          </Button>
        )}
        <span className="text-caption text-muted-foreground sm:self-center">
          The LLM proposes — <strong>you still approve every command</strong> before it runs.
        </span>
      </div>
      {selectedProvider && (
        <p className="text-caption text-muted-foreground">
          Provider type: <code className="font-mono">{selectedProvider.provider_type}</code>
          {selectedProvider.model_id && (
            <>
              {' · '}Model: <code className="font-mono">{selectedProvider.model_id}</code>
            </>
          )}
          {/* Rough prompt token estimate — 4 chars/token as the
              common heuristic — so the user has a sense of cost
              before clicking Run (audit H11). */}
          {' · '}Approx. prompt size: ~{Math.ceil(prompt.length / 4)} tokens
        </p>
      )}
      <p className="text-caption text-warning">
        Agent API keys and scanner credentials are <strong>redacted</strong> before the prompt is
        sent to the provider. The full instructions above still contain them for copy-paste use.
      </p>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {result && (
        <div className="space-y-xxs">
          <div className="flex items-center justify-between">
            <h4 className="text-subheading">
              Response from {result.provider_name}
              {result.model_id && (
                <>
                  {' · '}
                  <code className="font-mono">{result.model_id}</code>
                </>
              )}
            </h4>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={handleCopy}
                  aria-label="Copy response to clipboard"
                >
                  {copied ? (
                    <Check className="size-4 text-success" aria-hidden />
                  ) : (
                    <Copy className="size-4" aria-hidden />
                  )}
                </Button>
              </TooltipTrigger>
              <TooltipContent>{copied ? 'Copied!' : 'Copy response'}</TooltipContent>
            </Tooltip>
          </div>
          <div className="max-h-80 overflow-auto rounded-control border border-border bg-muted/30 p-sm font-mono text-caption text-foreground whitespace-pre-wrap break-words">
            {result.content}
          </div>
          {result.raw_metadata?.usage && (
            <p className="text-caption text-muted-foreground">
              Usage: {JSON.stringify(result.raw_metadata.usage)}
            </p>
          )}
        </div>
      )}
    </div>
  );
};

export default InAppAgentPanel;
