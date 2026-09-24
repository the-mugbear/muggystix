import { useMemo, useState } from 'react';
import { ArrowLeft, Check, ChevronDown, ChevronRight, Plus, Search } from 'lucide-react';
import type { HostFilterData } from '../../services/api';
import {
  HOST_PORT_GROUP_PRESETS,
  togglePreset,
  type HostFilterOptions,
} from '../HostFilters';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Input } from '../ui/input';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';
import { cn } from '../../utils/cn';
import { PORT_STATE_ANY } from '../../utils/endpointMatch';
import {
  FILTER_CATEGORIES,
  HOST_FILTER_FIELDS,
  fieldById,
  fieldIsApplied,
  portOptions,
  scanOptions,
  searchFields,
  serviceOptions,
  type ChoiceField,
  type FilterValueOption,
  type HostFilterField,
  type MultiField,
  type SingleField,
} from './hostFilterFields';

/**
 * "+ Add filter": a searchable catalog of filter fields, and — one at a time —
 * the editor for the field that was picked.  The table stays visible behind
 * it; nothing changes until a condition is applied, and dismissing the popover
 * discards the draft (the editor unmounts).  An applied condition's chip opens
 * the same editor through `fieldId`.
 *
 * Multi-value editors are a local draft with one Apply, so choosing three
 * subnets is one table reload, not three.  A field with a single answer
 * (a toggle, a choice, the OS) applies as it is picked.
 */

export interface HostFilterPopoverProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Field whose editor is showing; null shows the catalog. */
  fieldId: string | null;
  onFieldChange: (fieldId: string | null) => void;
  filters: HostFilterOptions;
  onApply: (next: HostFilterOptions) => void;
  data: HostFilterData | null;
  optionsLoading: boolean;
  /** The facet request failed — the option lists may be missing or stale. */
  optionsError: boolean;
}

const without = (filters: HostFilterOptions, keys: Array<keyof HostFilterOptions>): HostFilterOptions => {
  const next = { ...filters } as Record<string, unknown>;
  keys.forEach((k) => delete next[k]);
  return next as HostFilterOptions;
};

const withValue = (filters: HostFilterOptions, key: keyof HostFilterOptions, value: unknown): HostFilterOptions => {
  const next = { ...filters } as Record<string, unknown>;
  const empty = value === undefined || (Array.isArray(value) && value.length === 0);
  if (empty) delete next[key];
  else next[key] = value;
  return next as HostFilterOptions;
};

// ── A searchable list of values with counts ─────────────────────────────────

interface ValueListProps {
  label: string;
  options: FilterValueOption[];
  selected: string[];
  onToggle: (value: string) => void;
  loading: boolean;
  error: boolean;
  noData: string;
  cap?: number;
  queryHint?: string;
  /** Rows before the list scrolls. */
  tall?: boolean;
}

function ValueList({
  label, options, selected, onToggle, loading, error, noData, cap, queryHint, tall,
}: ValueListProps) {
  const [needle, setNeedle] = useState('');
  // A selected value must stay visible and removable even when the other
  // conditions (or a failed facet load) leave it out of the option list.
  const rows = useMemo(() => {
    const known = new Set(options.map((o) => o.value));
    const orphans: FilterValueOption[] = selected
      .filter((v) => !known.has(v))
      .map((v) => ({ value: v, label: v, description: 'Selected — not among the values loaded now' }));
    return [...orphans, ...options];
  }, [options, selected]);
  const q = needle.trim().toLowerCase();
  const visible = q
    ? rows.filter((o) =>
        o.label.toLowerCase().includes(q) || o.keywords?.some((k) => k.toLowerCase().includes(q)))
    : rows;
  const capped = cap !== undefined && options.length >= cap;

  if (rows.length === 0) {
    return (
      <p className="py-xs text-caption text-muted-foreground break-words">
        {loading ? 'Loading options…' : error ? 'Options unavailable — the value list could not be loaded.' : noData}
      </p>
    );
  }
  return (
    <div className="space-y-xxs">
      {rows.length > 8 && (
        <div className="relative">
          <Search className="pointer-events-none absolute left-xs top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            value={needle}
            onChange={(e) => setNeedle(e.target.value)}
            spellCheck={false}
            autoCorrect="off"
            autoCapitalize="off"
            placeholder={`Find in ${label.toLowerCase()}…`}
            aria-label={`Find in ${label}`}
            className="h-8 pl-7 text-caption"
          />
        </div>
      )}
      <ul className={cn('overflow-y-auto', tall ? 'max-h-64' : 'max-h-40')} aria-label={label}>
        {visible.map((o) => {
          const id = `hf-${label}-${o.value}`.replace(/\s+/g, '-');
          return (
            <li key={o.value}>
              <label
                htmlFor={id}
                className="flex min-w-0 cursor-pointer items-start gap-xs rounded-control px-xs py-xxs hover:bg-accent"
              >
                <Checkbox
                  id={id}
                  className="mt-0.5 shrink-0"
                  checked={selected.includes(o.value)}
                  onCheckedChange={() => onToggle(o.value)}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-metadata" title={o.label}>{o.label}</span>
                  {o.description && (
                    <span className="block truncate text-caption text-muted-foreground">{o.description}</span>
                  )}
                </span>
                {o.count !== undefined && (
                  <span className="shrink-0 tabular-nums text-caption text-muted-foreground">
                    {o.count.toLocaleString()}
                  </span>
                )}
              </label>
            </li>
          );
        })}
        {visible.length === 0 && (
          <li className="px-xs py-xxs text-caption text-muted-foreground">
            No loaded value matches “{needle.trim()}”.
          </li>
        )}
      </ul>
      {capped && (
        <p className="text-caption text-muted-foreground break-words">
          Only the {cap} most common values are loaded — this search cannot reach the rest.
          {queryHint && <> Use the query bar instead, e.g. <code className="font-mono">{queryHint}</code>.</>}
        </p>
      )}
      {loading && <p className="text-caption text-muted-foreground">Updating counts…</p>}
      {error && !loading && (
        <p className="text-caption text-warning">Options could not be refreshed — counts may be out of date.</p>
      )}
    </div>
  );
}

// ── Editors ──────────────────────────────────────────────────────────────────

interface EditorProps<F extends HostFilterField = HostFilterField> {
  field: F;
  filters: HostFilterOptions;
  data: HostFilterData | null;
  loading: boolean;
  error: boolean;
  /** Apply the condition and close. */
  commit: (next: HostFilterOptions) => void;
  cancel: () => void;
}

const COUNTS_NOTE = 'Counts are hosts under the filters applied now; a host can carry several values, so they do not add up.';

function EditorFooter({
  onApply, onCancel, onRemove, applyDisabled,
}: { onApply: () => void; onCancel: () => void; onRemove?: () => void; applyDisabled?: boolean }) {
  // Pinned to the bottom of the popover's scroll area: on a short viewport the
  // editor scrolls, and the action must not be the part that scrolls away.
  // The negative offset + matching padding covers the popover's own `p-sm`,
  // so list rows never show through beneath the buttons.
  return (
    <div className="sticky -bottom-sm z-10 -mb-sm flex items-center gap-xs border-t border-border bg-popover pb-sm pt-xs">
      {onRemove && (
        <Button variant="ghost" size="sm" onClick={onRemove}>Remove condition</Button>
      )}
      <Button variant="ghost" size="sm" className="ml-auto" onClick={onCancel}>Cancel</Button>
      <Button size="sm" onClick={onApply} disabled={applyDisabled}>Apply condition</Button>
    </div>
  );
}

const toggleIn = (list: string[], value: string) =>
  list.includes(value) ? list.filter((v) => v !== value) : [...list, value];

function MultiEditor({ field, filters, data, loading, error, commit, cancel }: EditorProps<MultiField>) {
  const applied = (filters[field.key] as string[] | undefined) ?? [];
  const [draft, setDraft] = useState<string[]>(applied);
  return (
    <div className="space-y-xs">
      <p className="text-caption text-muted-foreground">Match ANY selected value.</p>
      <ValueList
        label={field.label}
        options={field.options(data)}
        selected={draft}
        onToggle={(v) => setDraft((d) => toggleIn(d, v))}
        loading={loading}
        error={error}
        noData={field.noData}
        cap={field.cap}
        queryHint={field.queryHint}
        tall
      />
      <p className="text-caption text-muted-foreground">{COUNTS_NOTE}</p>
      <EditorFooter
        onApply={() => commit(withValue(filters, field.key, draft))}
        onCancel={cancel}
        onRemove={applied.length > 0 ? () => commit(without(filters, field.keys)) : undefined}
      />
    </div>
  );
}

function SingleEditor({ field, filters, data, loading, error, commit }: EditorProps<SingleField>) {
  const applied = filters[field.key] as string | undefined;
  return (
    <div className="space-y-xs">
      <p className="text-caption text-muted-foreground">Pick one — it applies straight away. Pick it again to clear.</p>
      <ValueList
        label={field.label}
        options={field.options(data)}
        selected={applied ? [applied] : []}
        onToggle={(v) => commit(withValue(filters, field.key, v === applied ? undefined : v))}
        loading={loading}
        error={error}
        noData={field.noData}
        cap={field.cap}
        queryHint={field.queryHint}
        tall
      />
    </div>
  );
}

function ChoiceEditor({ field, filters, commit }: EditorProps<ChoiceField>) {
  const applied = filters[field.key];
  return (
    <ul className="space-y-xxs" role="radiogroup" aria-label={field.label}>
      {field.choices.map((choice) => {
        const active = applied === choice.value;
        return (
          <li key={String(choice.value)}>
            <button
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => commit(withValue(filters, field.key, choice.value))}
              className={cn(
                'flex w-full min-w-0 items-start gap-xs rounded-control px-xs py-xxs text-left hover:bg-accent',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              )}
            >
              <Check className={cn('mt-0.5 size-4 shrink-0', !active && 'opacity-0')} aria-hidden />
              <span className="min-w-0">
                <span className="block text-metadata">{choice.label}</span>
                {choice.help && <span className="block text-caption text-muted-foreground break-words">{choice.help}</span>}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

const SEVERITIES: Array<{ key: keyof HostFilterOptions; label: string }> = [
  { key: 'hasCriticalVulns', label: 'Critical' },
  { key: 'hasHighVulns', label: 'High' },
  { key: 'hasMediumVulns', label: 'Medium' },
  { key: 'hasLowVulns', label: 'Low' },
];

function SeverityEditor({ field, filters, commit, cancel }: EditorProps) {
  const [draft, setDraft] = useState<string[]>(
    SEVERITIES.filter((s) => filters[s.key] === true).map((s) => String(s.key)),
  );
  const apply = () => {
    let next = without(filters, field.keys);
    draft.forEach((k) => { next = withValue(next, k as keyof HostFilterOptions, true); });
    commit(next);
  };
  return (
    <div className="space-y-xs">
      <ul aria-label="Scanner severity">
        {SEVERITIES.map((s) => (
          <li key={s.key}>
            <label htmlFor={`hf-sev-${s.key}`} className="flex cursor-pointer items-center gap-xs rounded-control px-xs py-xxs hover:bg-accent">
              <Checkbox
                id={`hf-sev-${s.key}`}
                checked={draft.includes(String(s.key))}
                onCheckedChange={() => setDraft((d) => toggleIn(d, String(s.key)))}
              />
              <span className="text-metadata">{s.label}</span>
            </label>
          </li>
        ))}
      </ul>
      <EditorFooter
        onApply={apply}
        onCancel={cancel}
        onRemove={fieldIsApplied(field, filters) ? () => commit(without(filters, field.keys)) : undefined}
      />
    </div>
  );
}

const ENDPOINT_LIST_KEYS: Array<keyof HostFilterOptions> = ['ports', 'services', 'portStates'];
const ENDPOINT_STATE_CHOICES = [
  { value: 'open', label: 'Open' },
  { value: 'closed', label: 'Closed' },
  { value: 'filtered', label: 'Filtered' },
];

function EndpointEditor({ field, filters, data, loading, error, commit, cancel }: EditorProps) {
  // `hasOpenPorts: false` is a different condition ("no recorded open ports")
  // that the backend applies on its own; this editor leaves it alone.
  const [draft, setDraft] = useState<HostFilterOptions>(() => {
    let initial: HostFilterOptions = {};
    ENDPOINT_LIST_KEYS.forEach((k) => { initial = withValue(initial, k, filters[k]); });
    return withValue(initial, 'hasOpenPorts', filters.hasOpenPorts === true ? true : undefined);
  });
  const set = (key: keyof HostFilterOptions, value: unknown) => setDraft((d) => withValue(d, key, value));
  const draftIsEmpty = Object.keys(draft).length === 0;
  const excludesOpen = filters.hasOpenPorts === false;
  const apply = () => {
    // The backend ignores every port filter while "no recorded open ports" is
    // set, so the two cannot both hold: the condition being applied wins, and
    // the editor says so below rather than letting one silently disable the other.
    let next = without(filters, draftIsEmpty && excludesOpen ? ENDPOINT_LIST_KEYS : field.keys);
    (Object.keys(draft) as Array<keyof HostFilterOptions>).forEach((k) => { next = withValue(next, k, draft[k]); });
    commit(next);
  };
  // v5.289.0 — a port / service condition means an OPEN port unless a state is
  // chosen (the backend's `resolve_endpoint_states`), so "Open" shows ticked by
  // default and the editor writes a state only when the operator changes it.
  const namedStates = draft.portStates ?? [];
  const anyState = namedStates.includes(PORT_STATE_ANY);
  const shownStates = anyState ? [] : (namedStates.length ? namedStates : ['open']);
  const otherStates = namedStates.filter((s) => s !== PORT_STATE_ANY && !ENDPOINT_STATE_CHOICES.some((c) => c.value === s));
  const toggleState = (state: string, checked: boolean) => setDraft((d) => {
    const next = checked ? [...shownStates.filter((s) => s !== state), state] : shownStates.filter((s) => s !== state);
    // `hasOpenPorts` folds into the state list once the operator picks a state.
    return withValue(withValue(d, 'portStates', next), 'hasOpenPorts', undefined);
  });
  return (
    <div className="space-y-xs">
      <p className="text-caption text-muted-foreground break-words">
        Everything chosen here must match the <strong className="text-foreground">same recorded port</strong>.
        Within a list, any value matches. To require two ports on one host, use the query bar:{' '}
        <code className="font-mono">port:22 AND port:443</code>.
      </p>
      <div className="flex flex-wrap gap-xxs" role="group" aria-label="Port groups">
        {HOST_PORT_GROUP_PRESETS.map((preset) => (
          <button
            key={preset.id}
            type="button"
            title={preset.description}
            onClick={() => setDraft((d) => togglePreset(preset.filters, d, HOST_PORT_GROUP_PRESETS.map((p) => p.filters)))}
            className="rounded-chip border border-border bg-card px-xs py-px text-caption hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {preset.name}
          </button>
        ))}
      </div>
      <div className="grid gap-sm sm:grid-cols-2">
        <div className="min-w-0 space-y-xxs">
          <h4 className="text-caption font-semibold uppercase tracking-wider text-muted-foreground">Port</h4>
          <ValueList
            label="Ports" options={portOptions(data)} selected={draft.ports ?? []}
            onToggle={(v) => set('ports', toggleIn(draft.ports ?? [], v))}
            loading={loading} error={error} cap={500} queryHint="port:8443"
            noData="No ports yet — run a port scan (Nmap/Masscan) and upload it."
          />
        </div>
        <div className="min-w-0 space-y-xxs">
          <h4 className="text-caption font-semibold uppercase tracking-wider text-muted-foreground">Service</h4>
          <ValueList
            label="Services" options={serviceOptions(data)} selected={draft.services ?? []}
            onToggle={(v) => set('services', toggleIn(draft.services ?? [], v))}
            loading={loading} error={error} cap={200} queryHint="service:ms-wbt-server"
            noData="No services yet — run a version scan (nmap -sV) and upload it."
          />
        </div>
      </div>
      <fieldset className="min-w-0 space-y-xxs">
        <legend className="text-caption font-semibold uppercase tracking-wider text-muted-foreground">Port state</legend>
        <div className="flex flex-wrap items-center gap-x-sm gap-y-xxs px-xs">
          {ENDPOINT_STATE_CHOICES.map((choice) => {
            const checked = shownStates.includes(choice.value);
            const id = `hf-endpoint-state-${choice.value}`;
            return (
              <label key={choice.value} htmlFor={id} className="flex cursor-pointer items-center gap-xxs">
                <Checkbox
                  id={id}
                  checked={checked}
                  // The last ticked state stays: an empty list would mean the default again.
                  disabled={anyState || (checked && shownStates.length === 1)}
                  onCheckedChange={(c) => toggleState(choice.value, c === true)}
                />
                <span className="text-metadata">{choice.label}</span>
              </label>
            );
          })}
          <label htmlFor="hf-endpoint-state-any" className="flex cursor-pointer items-center gap-xxs">
            <Checkbox
              id="hf-endpoint-state-any"
              checked={anyState}
              onCheckedChange={(c) => setDraft((d) => withValue(
                withValue(d, 'portStates', c === true ? [PORT_STATE_ANY] : []), 'hasOpenPorts', undefined,
              ))}
            />
            <span className="text-metadata">Any state</span>
          </label>
        </div>
        <p className="px-xs text-caption text-muted-foreground break-words">
          Open unless you choose otherwise. For a closed or filtered port, nmap names the service from
          the port number alone — “ssh” there is no evidence SSH runs.
        </p>
      </fieldset>
      {otherStates.length > 0 && (
        // Arrives from a link; never silently narrowed to "open".
        <p className="px-xs text-caption text-muted-foreground break-words">
          Also matching port state: {otherStates.join(' or ')}.{' '}
          <button
            type="button"
            className="underline underline-offset-2"
            onClick={() => set('portStates', namedStates.filter((s) => !otherStates.includes(s)))}
          >
            Remove
          </button>
        </p>
      )}
      {excludesOpen && !draftIsEmpty && (
        <p className="px-xs text-caption text-warning break-words">
          “No recorded open ports” is applied and cannot hold together with a port condition — applying this replaces it.
        </p>
      )}
      <p className="text-caption text-muted-foreground">{COUNTS_NOTE}</p>
      <EditorFooter
        onApply={apply}
        onCancel={cancel}
        onRemove={
          fieldIsApplied(field, filters)
            ? () => commit(without(filters, excludesOpen ? ENDPOINT_LIST_KEYS : field.keys))
            : undefined
        }
      />
    </div>
  );
}

function ScansEditor({ field, filters, data, loading, error, commit, cancel }: EditorProps) {
  const [draft, setDraft] = useState<string[]>(filters.scanIds ?? []);
  const [firstSeen, setFirstSeen] = useState(filters.firstSeenInSelectedScans === true);
  const apply = () => {
    let next = withValue(without(filters, field.keys), 'scanIds', draft);
    // The modifier means nothing without a scan, so it never outlives them.
    if (firstSeen && draft.length > 0) next = withValue(next, 'firstSeenInSelectedScans', true);
    commit(next);
  };
  return (
    <div className="space-y-xs">
      <div className="flex gap-xxs" role="radiogroup" aria-label="How the scans are used">
        {[
          { value: false, label: 'Observed in' },
          { value: true, label: 'First discovered in' },
        ].map((mode) => (
          <button
            key={mode.label}
            type="button"
            role="radio"
            aria-checked={firstSeen === mode.value}
            onClick={() => setFirstSeen(mode.value)}
            className={cn(
              'rounded-chip border px-sm py-xxs text-caption font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              firstSeen === mode.value
                ? 'border-transparent bg-primary text-primary-foreground'
                : 'border-border bg-card hover:bg-accent',
            )}
          >
            {mode.label}
          </button>
        ))}
      </div>
      <ValueList
        label="Scans" options={scanOptions(data)} selected={draft}
        onToggle={(v) => setDraft((d) => toggleIn(d, v))}
        loading={loading} error={error} cap={100} queryHint="scan:42" tall
        noData="No scans yet — upload scan output from the Scans page."
      />
      <EditorFooter
        onApply={apply}
        onCancel={cancel}
        applyDisabled={firstSeen && draft.length === 0}
        onRemove={filters.scanIds?.length ? () => commit(without(filters, field.keys)) : undefined}
      />
    </div>
  );
}

function FieldEditor(props: EditorProps) {
  const { field } = props;
  switch (field.kind) {
    case 'multi': return <MultiEditor {...props} field={field} />;
    case 'single': return <SingleEditor {...props} field={field} />;
    case 'choice': return <ChoiceEditor {...props} field={field} />;
    case 'severity': return <SeverityEditor {...props} />;
    case 'endpoint': return <EndpointEditor {...props} />;
    case 'scans': return <ScansEditor {...props} />;
    default: return null; // a toggle has no editor — the catalog row applies it
  }
}

// ── Catalog ──────────────────────────────────────────────────────────────────

function Catalog({
  filters, onPick,
}: { filters: HostFilterOptions; onPick: (field: HostFilterField) => void }) {
  const [needle, setNeedle] = useState('');
  const [openCategory, setOpenCategory] = useState<string | null>(null);
  const searching = needle.trim().length > 0;
  const results = searching ? searchFields(needle) : [];

  const row = (field: HostFilterField, withHelp: boolean) => {
    const applied = fieldIsApplied(field, filters);
    const isToggle = field.kind === 'toggle';
    return (
      <li key={field.id}>
        <button
          type="button"
          onClick={() => onPick(field)}
          title={withHelp ? undefined : field.help}
          aria-pressed={isToggle ? applied : undefined}
          className="flex w-full min-w-0 items-start gap-xs rounded-control px-xs py-xxs text-left hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Check className={cn('mt-0.5 size-4 shrink-0 text-primary', !applied && 'opacity-0')} aria-hidden />
          <span className="min-w-0 flex-1">
            <span className="block text-metadata">{field.label}</span>
            {withHelp && <span className="block text-caption text-muted-foreground break-words">{field.help}</span>}
          </span>
          {isToggle
            ? <span className="shrink-0 text-caption text-muted-foreground">{applied ? 'on — click to remove' : 'add'}</span>
            : <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />}
        </button>
      </li>
    );
  };

  return (
    <div className="space-y-xs">
      <div className="relative">
        <Search className="pointer-events-none absolute left-xs top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <Input
          autoFocus
          value={needle}
          onChange={(e) => setNeedle(e.target.value)}
          spellCheck={false}
          autoCorrect="off"
          autoCapitalize="off"
          placeholder="Find a filter… (port, severity, unreviewed, ASN)"
          aria-label="Find a filter"
          className="h-9 pl-7"
        />
      </div>
      {searching ? (
        results.length > 0 ? (
          <ul aria-label="Matching filters">{results.map((f) => row(f, true))}</ul>
        ) : (
          <p className="px-xs text-caption text-muted-foreground break-words">
            No structured filter matches. The query bar reaches more — CVE (<code className="font-mono">cve:</code>),
            vulnerability text (<code className="font-mono">vuln:</code>), certificate organisation
            (<code className="font-mono">certorg:</code>), note text (<code className="font-mono">note:</code>).
          </p>
        )
      ) : (
        <>
          <div>
            <h4 className="px-xs text-caption font-semibold uppercase tracking-wider text-muted-foreground">Common</h4>
            <ul>{HOST_FILTER_FIELDS.filter((f) => f.common).map((f) => row(f, false))}</ul>
          </div>
          <div className="border-t border-border pt-xs">
            {FILTER_CATEGORIES.map((category) => {
              const expanded = openCategory === category.id;
              const fields = HOST_FILTER_FIELDS.filter((f) => f.category === category.id);
              const appliedCount = fields.filter((f) => fieldIsApplied(f, filters)).length;
              return (
                <div key={category.id}>
                  <button
                    type="button"
                    aria-expanded={expanded}
                    onClick={() => setOpenCategory(expanded ? null : category.id)}
                    className="flex w-full items-center gap-xs rounded-control px-xs py-xxs text-left text-metadata font-medium hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {expanded
                      ? <ChevronDown className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                      : <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />}
                    <span className="flex-1">{category.label}</span>
                    {appliedCount > 0 && (
                      <span className="text-caption text-muted-foreground">{appliedCount} applied</span>
                    )}
                  </button>
                  {expanded && <ul className="pl-sm">{fields.map((f) => row(f, false))}</ul>}
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

// ── The popover ──────────────────────────────────────────────────────────────

export default function HostFilterPopover({
  open, onOpenChange, fieldId, onFieldChange, filters, onApply, data, optionsLoading, optionsError,
}: HostFilterPopoverProps) {
  const field = fieldId ? fieldById(fieldId) : undefined;
  const close = () => onOpenChange(false);
  const commit = (next: HostFilterOptions) => {
    // Choosing "no recorded open ports" drops a port condition it would
    // otherwise silently override (see EndpointEditor for the other direction).
    const settled = fieldId === 'noOpenPorts' && next.hasOpenPorts === false
      ? without(next, ENDPOINT_LIST_KEYS)
      : next;
    onApply(settled);
    close();
  };
  const pick = (picked: HostFilterField) => {
    if (picked.kind === 'toggle') {
      commit(withValue(filters, picked.key, fieldIsApplied(picked, filters) ? undefined : true));
      return;
    }
    onFieldChange(picked.id);
  };

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" aria-haspopup="dialog">
          <Plus className="size-4" aria-hidden />
          Add filter
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        collisionPadding={8}
        // Capped by the room Radix measures below the trigger, not by the
        // viewport: the trigger sits ~250px down the page, so `100vh - 8rem`
        // still ran off the bottom of a short window.
        className="flex max-h-[min(36rem,var(--radix-popover-content-available-height))] w-[30rem] max-w-[calc(100vw-2rem)] flex-col overflow-y-auto p-sm"
        aria-label={field ? `Filter: ${field.label}` : 'Add a filter'}
      >
        {field ? (
          <div className="space-y-xs">
            <div className="flex min-w-0 items-center gap-xs">
              <Button variant="ghost" size="sm" className="h-7 shrink-0 px-xs" onClick={() => onFieldChange(null)}>
                <ArrowLeft className="size-4" aria-hidden />
                Filters
              </Button>
              <span className="text-muted-foreground" aria-hidden>/</span>
              <h3 className="min-w-0 truncate text-metadata font-semibold">{field.label}</h3>
            </div>
            <p className="text-caption text-muted-foreground break-words">{field.help}</p>
            {/* Keyed so a draft never survives switching fields. */}
            <FieldEditor
              key={field.id}
              field={field}
              filters={filters}
              data={data}
              loading={optionsLoading}
              error={optionsError}
              commit={commit}
              cancel={close}
            />
          </div>
        ) : (
          <Catalog filters={filters} onPick={pick} />
        )}
      </PopoverContent>
    </Popover>
  );
}
