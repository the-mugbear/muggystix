import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  Check,
  Clock,
  HelpCircle,
  Link2,
  Loader2,
  RefreshCw,
  Search,
  Sparkles,
  Star,
  X,
} from 'lucide-react';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';
import { useQueryAssist } from './useQueryAssist';
import { quote } from './dslFromFilters';
import { cn } from '../../utils/cn';
import { useSearchFocus } from '../../hooks/useSearchFocus';
import type { HostQueryField } from '../../services/api';

export interface HostCommandBarProps {
  /** Committed query value (filters.query). */
  value: string;
  /** Push a (debounced or committed) query up to the page filters. */
  onChange: (q: string) => void;
  /** Save the current query as a named view. */
  onPin: (q: string) => void;
  /** Copy the current shareable URL.  Receives the live draft so the copied
   *  link includes a just-typed query without waiting for the commit debounce. */
  onCopyLink: (q?: string) => void;
  /** Optional facet values per value_source (ports/services/os/tags/…). */
  valueSuggestions?: Record<string, string[]>;
}

const COMMIT_DEBOUNCE_MS = 450;

/** The last whitespace-delimited token of `text` (what autocomplete acts on). */
function lastToken(text: string): { token: string; head: string } {
  const m = text.match(/(\S*)$/);
  const token = m ? m[1] : '';
  return { token, head: text.slice(0, text.length - token.length) };
}

// Short, human descriptions for the syntax-help popover — keyed by the
// server's field names (the field set itself comes live from the schema, so
// no field can silently go missing; this only annotates them).
const FIELD_DESCRIPTIONS: Record<string, string> = {
  state: 'Host up / down / unknown',
  ip: 'Host IP address',
  hostname: 'Host name',
  os: 'OS name (nmap detection)',
  port: 'An open port number',
  service: 'Service on an open port (nmap -sV)',
  portstate: 'Port open / closed / filtered',
  subnet: 'Host IP within a CIDR',
  tech: 'Web technology (httpx / whatweb)',
  tag: 'Project host tag',
  label: 'Project subnet label',
  site: 'Site of the host’s subnet',
  follow: 'Review state (in_review / reviewed …)',
  assigned: 'Assignment (“me” or a username)',
  scan: 'A scan that observed the host',
  has: 'Flags: web, notes, exploit, tested, critical …',
  cve: 'Finding CVE id (Nessus / OpenVAS / Nikto)',
  vuln: 'Finding title (Nessus / OpenVAS / Nikto)',
  header: 'HTTP Server header (httpx)',
  webtitle: 'Web page title (httpx / eyewitness)',
  note: 'Note / annotation body text',
};

function buildSuggestions(
  draft: string,
  fields: HostQueryField[],
  valueSuggestions: Record<string, string[]> | undefined,
): { display: string; insert: string }[] {
  const { token } = lastToken(draft);
  if (!token) return [];
  const colon = token.indexOf(':');
  if (colon === -1) {
    // Suggest field names + aliases that start with the partial token.
    const lower = token.toLowerCase();
    const out: { display: string; insert: string }[] = [];
    for (const f of fields) {
      for (const name of [f.name, ...f.aliases]) {
        if (name.startsWith(lower)) out.push({ display: `${name}:`, insert: `${name}:` });
      }
    }
    return out.slice(0, 8);
  }
  // After `field:` — suggest enum values / facet values for that field.
  const fieldName = token.slice(0, colon).toLowerCase();
  const partial = token.slice(colon + 1).toLowerCase();
  const spec = fields.find((f) => f.name === fieldName || f.aliases.includes(fieldName));
  if (!spec) return [];
  const pool =
    spec.enum_values.length > 0
      ? spec.enum_values
      : (valueSuggestions?.[spec.value_source] ?? []);
  return pool
    .filter((v) => v.toLowerCase().includes(partial))
    .slice(0, 8)
    // Quote values with spaces/commas/quotes so e.g. "Windows Server 2019"
    // inserts as os:"Windows Server 2019", not os:Windows AND Server AND 2019.
    .map((v) => ({ display: v, insert: `${spec.name}:${quote(v)}` }));
}

/**
 * Query-first hero for the Hosts page: a boolean-DSL power input with live
 * validation + match-count preview, field/value autocomplete, recent-queries
 * and template dropdowns, pin-to-view, copy-link, and a syntax-help popover.
 * Bare text still works (the backend maps it to the legacy free-text search).
 */
export default function HostCommandBar({
  value,
  onChange,
  onPin,
  onCopyLink,
  valueSuggestions,
}: HostCommandBarProps) {
  const [draft, setDraft] = useState(value);
  const [focused, setFocused] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  // Highlighted suggestion for keyboard navigation (-1 = none). Reset whenever
  // the draft changes so a fresh suggestion list starts unhighlighted.
  const [activeIndex, setActiveIndex] = useState(-1);
  const listboxId = 'hosts-query-suggestions';
  const inputRef = useRef<HTMLInputElement>(null);
  // The "/" keyboard shortcut focuses the command bar (it used to focus the
  // now-removed panel search field).
  useSearchFocus(inputRef);
  const { schema, validation, validatedQuery, validating, validationError, retryValidation, history, recordQuery, removeHistory, clearHistory } =
    useQueryAssist(draft);

  const trimmedDraft = draft.trim();
  // `validation` only describes the current draft when validatedQuery matches
  // and nothing is in flight. Acting on a stale/pending result could commit an
  // invalid query (and trigger list-fetch errors) or copy a broken link.
  const validationFresh = !validating && validatedQuery === trimmedDraft;
  // Committable when validated-and-valid, OR when validation is unavailable
  // (offline / endpoint down) — in that case we don't dead-end the user: the
  // query can still be submitted and the hosts endpoint will reject it if bad.
  const draftCommittable =
    trimmedDraft === '' || validationError || (validationFresh && !!validation?.valid);

  // Keep the draft in sync when the value changes externally (saved view,
  // convert button, history apply, URL restore).
  useEffect(() => { setDraft(value); }, [value]);

  // Debounced commit: update the page filters as the user types, but only
  // when the draft is empty or parses cleanly — never push a broken query
  // into the list fetch.
  useEffect(() => {
    const trimmed = draft.trim();
    if (trimmed === value.trim()) return;
    // Only auto-commit once validation has caught up to this exact draft and
    // says it's valid (or the draft is empty). Avoids pushing a query the
    // backend hasn't confirmed — validation lags the draft by its debounce.
    const ok = trimmed === '' || (!validating && validatedQuery === trimmed && (validation?.valid ?? false));
    if (!ok) return;
    const timer = setTimeout(() => onChange(trimmed), COMMIT_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [draft, validation, validatedQuery, validating, value, onChange]);

  const suggestions = useMemo(
    () => (schema ? buildSuggestions(draft, schema.fields, valueSuggestions) : []),
    [draft, schema, valueSuggestions],
  );
  useEffect(() => { setActiveIndex(-1); }, [draft]);

  const commit = () => {
    const trimmed = draft.trim();
    // Don't commit a non-empty draft until validation confirms it (fresh + valid),
    // so Enter on a half-typed `port:` can't push a broken query.
    if (trimmed && !draftCommittable) return;
    onChange(trimmed);
    if (trimmed) recordQuery(trimmed, validation?.match_count ?? null);
  };

  const applySuggestion = (insert: string) => {
    const { head } = lastToken(draft);
    setDraft(`${head}${insert}`);
    setActiveIndex(-1);
    inputRef.current?.focus();
  };

  // Click a field in the syntax popover to append `field:` to the query and
  // focus the input so the user can type the value.
  const insertField = (name: string) => {
    setDraft((prev) => {
      const trimmed = prev.replace(/\s+$/, '');
      return `${trimmed}${trimmed ? ' ' : ''}${name}:`;
    });
    inputRef.current?.focus();
  };

  const applyQuery = (q: string) => {
    setDraft(q);
    onChange(q);
    setHistoryOpen(false);
    inputRef.current?.focus();
  };

  const invalid = !!trimmedDraft && validationFresh && !!validation && !validation.valid;
  const showSuggest = focused && suggestions.length > 0;

  return (
    <div className="space-y-xs">
      <div className="flex flex-col gap-xs sm:flex-row sm:items-center">
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute left-sm top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onFocus={() => setFocused(true)}
            onBlur={() => setTimeout(() => setFocused(false), 120)}
            onKeyDown={(e) => {
              if (showSuggest && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
                e.preventDefault();
                const n = suggestions.length;
                setActiveIndex((prev) =>
                  e.key === 'ArrowDown'
                    ? (prev < n - 1 ? prev + 1 : 0)
                    : (prev > 0 ? prev - 1 : n - 1),
                );
                return;
              }
              if (e.key === 'Enter') {
                e.preventDefault();
                // Enter on a highlighted suggestion accepts it; otherwise commit.
                if (showSuggest && activeIndex >= 0 && activeIndex < suggestions.length) {
                  applySuggestion(suggestions[activeIndex].insert);
                } else {
                  commit();
                }
                return;
              }
              if (e.key === 'Escape') { setFocused(false); setActiveIndex(-1); }
            }}
            placeholder="Search hosts or query (port:80 port:443 AND NOT tag:test) — press / to focus"
            aria-label="Host query"
            aria-invalid={invalid ? true : undefined}
            role="combobox"
            aria-expanded={showSuggest}
            aria-controls={listboxId}
            aria-autocomplete="list"
            aria-activedescendant={
              showSuggest && activeIndex >= 0 ? `${listboxId}-opt-${activeIndex}` : undefined
            }
            className="pl-9 pr-24 font-mono text-body"
          />
          <div className="absolute right-sm top-1/2 flex -translate-y-1/2 items-center gap-xs">
            {validating && <Loader2 className="size-3.5 animate-spin text-muted-foreground" aria-hidden />}
            {!validating && trimmedDraft && validationFresh && validation?.valid && (
              <Badge variant="secondary" className="gap-xxs">
                <Check className="size-3" aria-hidden />
                {/* null = the backend skipped the count (statement timeout); show
                    a dash rather than a misleading "0 matches". */}
                {validation.match_count != null ? (
                  validation.match_count
                ) : (
                  <span title="Match count unavailable — query too expensive to count live">—</span>
                )}
              </Badge>
            )}
            {!validating && invalid && (
              <AlertCircle className="size-4 text-destructive" aria-hidden />
            )}
            {!validating && validationError && trimmedDraft && (
              <button
                type="button"
                aria-label="Query validation unavailable — retry"
                title="Query validation unavailable — click to retry (you can still press Enter to run)"
                className="flex items-center gap-xxs text-warning hover:text-foreground"
                onMouseDown={(e) => { e.preventDefault(); retryValidation(); }}
              >
                <AlertCircle className="size-3.5" aria-hidden />
                <RefreshCw className="size-3" aria-hidden />
              </button>
            )}
            {draft && (
              <button
                type="button"
                aria-label="Clear query"
                className="text-muted-foreground hover:text-foreground"
                onClick={() => { setDraft(''); onChange(''); }}
              >
                <X className="size-3.5" aria-hidden />
              </button>
            )}
          </div>

          {showSuggest && (
            <ul
              id={listboxId}
              role="listbox"
              aria-label="Query suggestions"
              className="absolute left-0 right-0 top-full z-20 mt-xxs max-h-64 overflow-auto rounded-md border border-border bg-popover py-xxs shadow-md"
            >
              {suggestions.map((s, i) => (
                <li
                  key={s.insert}
                  id={`${listboxId}-opt-${i}`}
                  role="option"
                  aria-selected={i === activeIndex}
                  // onMouseDown (not onClick) + preventDefault keeps input focus
                  // so the blur-close doesn't fire before the selection lands.
                  onMouseDown={(e) => { e.preventDefault(); applySuggestion(s.insert); }}
                  onMouseEnter={() => setActiveIndex(i)}
                  className={cn(
                    'flex w-full cursor-pointer items-center px-sm py-xxs text-left font-mono text-metadata',
                    i === activeIndex ? 'bg-accent' : 'hover:bg-accent',
                  )}
                >
                  {s.display}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="flex items-center gap-xs">
          {/* Recent queries */}
          <Popover open={historyOpen} onOpenChange={setHistoryOpen}>
            <PopoverTrigger asChild>
              <Button variant="outline" size="sm" disabled={history.length === 0} aria-label="Recent queries">
                <Clock className="size-4" aria-hidden />
                History
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-80 p-0" align="end">
              <div className="flex items-center justify-between border-b border-border px-sm py-xs">
                <span className="text-metadata font-semibold">Recent queries</span>
                <Button variant="ghost" size="sm" onClick={() => clearHistory()}>Clear all</Button>
              </div>
              <ul className="max-h-72 overflow-auto py-xxs">
                {history.map((h) => (
                  <li key={h.id} className="group flex items-center gap-xs px-sm py-xxs hover:bg-accent">
                    <button
                      type="button"
                      className="min-w-0 flex-1 truncate text-left font-mono text-metadata"
                      title={h.q}
                      onClick={() => applyQuery(h.q)}
                    >
                      {h.q}
                    </button>
                    {h.result_count != null && (
                      <span className="shrink-0 text-metadata text-muted-foreground">{h.result_count}</span>
                    )}
                    <button
                      type="button"
                      aria-label="Remove from history"
                      className="shrink-0 text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-foreground"
                      onClick={() => removeHistory(h.id)}
                    >
                      <X className="size-3" aria-hidden />
                    </button>
                  </li>
                ))}
              </ul>
            </PopoverContent>
          </Popover>

          {/* Templates */}
          <Popover>
            <PopoverTrigger asChild>
              <Button variant="outline" size="sm" aria-label="Query templates">
                <Sparkles className="size-4" aria-hidden />
                Templates
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-96 p-0" align="end">
              <div className="border-b border-border px-sm py-xs text-metadata font-semibold">Starter queries</div>
              <ul className="max-h-72 overflow-auto py-xxs">
                {(schema?.examples ?? []).map((ex) => (
                  <li key={ex.q}>
                    <button
                      type="button"
                      className="w-full px-sm py-xs text-left hover:bg-accent"
                      onClick={() => applyQuery(ex.q)}
                    >
                      <div className="text-metadata font-medium">{ex.label}</div>
                      <div className="truncate font-mono text-caption text-muted-foreground">{ex.q}</div>
                    </button>
                  </li>
                ))}
              </ul>
            </PopoverContent>
          </Popover>

          <Button variant="outline" size="sm" aria-label="Save query as view" disabled={!trimmedDraft || !draftCommittable} onClick={() => onPin(draft.trim())}>
            <Star className="size-4" aria-hidden />
            Save
          </Button>
          <Button
            variant="outline"
            size="sm"
            aria-label="Copy shareable link"
            // Disabled only when the visible draft DIFFERS from the committed
            // query and isn't committable (pending/invalid) — so Copy can't
            // silently substitute the committed query for different visible
            // text. When the draft matches what's applied, or is valid, copying
            // reflects exactly what's shown.
            disabled={
              !!trimmedDraft && trimmedDraft !== (value ?? '').trim() && !draftCommittable
            }
            onClick={() => onCopyLink(trimmedDraft)}
          >
            <Link2 className="size-4" aria-hidden />
          </Button>

          {/* Syntax help */}
          <Popover>
            <PopoverTrigger asChild>
              <Button variant="ghost" size="sm" aria-label="Query syntax help">
                <HelpCircle className="size-4" aria-hidden />
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-96 space-y-xs" align="end">
              <p className="text-metadata font-semibold">Query syntax</p>
              <p className="text-caption text-muted-foreground">
                Combine fields with <code>AND</code> / <code>OR</code> / <code>NOT</code> and parentheses.
                Comma = OR within a field (<code>port:80,443</code>); repeating a field = AND
                (<code>port:80 port:443</code> ⇒ both). Bare text is a free-text search.
                <strong> Click a field to add it.</strong>
              </p>
              <div className="max-h-64 space-y-px overflow-y-auto">
                {(schema?.fields ?? []).map((f) => (
                  <button
                    key={f.name}
                    type="button"
                    onClick={() => insertField(f.name)}
                    className="flex w-full items-baseline gap-xs rounded px-xs py-xxs text-left hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                    title={f.aliases.length ? `aliases: ${f.aliases.join(', ')}` : undefined}
                  >
                    <code className="shrink-0 font-mono text-caption text-foreground">{f.name}:</code>
                    <span className="truncate text-caption text-muted-foreground">
                      {FIELD_DESCRIPTIONS[f.name] ?? ''}
                      {f.aliases.length ? ` · ${f.aliases.map((a) => `${a}:`).join(' ')}` : ''}
                    </span>
                  </button>
                ))}
              </div>
            </PopoverContent>
          </Popover>
        </div>
      </div>

      {invalid && validation?.error && (
        <p className="flex items-center gap-xs text-caption text-destructive">
          <AlertCircle className="size-3.5 shrink-0" aria-hidden />
          <span className="truncate">{validation.error.message}</span>
        </p>
      )}
    </div>
  );
}
