/**
 * Scanner observations — what scanners reported on this host, grouped by
 * issue, not yet judged (v5.225.0 vocabulary). One line per issue, with the
 * promote / dismiss write and its confirm dialog (`VulnerabilityTriageDialog`).
 *
 * The host is the inspector's read (`getHost`); a triage puts the server's
 * answer onto it through `putHost` and invalidates what the promotion put out
 * of date.  Whether informational rows are part of that read is the
 * inspector's (`includeInfo` is in the host's key), so this section is handed
 * the state of that read and the way to ask for it.
 */
import React, { useCallback, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';
import { ShieldAlert } from 'lucide-react';
import { promoteVulnerability } from '../../services/api';
import type { Host, HostVulnerability } from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { useProjectId } from '../../hooks/useProjectId';
import { queryErrorText } from '../../lib/query';
import { formatApiError } from '../../utils/apiErrors';
import { buildHostsUrl } from '../../utils/drilldownLinks';
import { issueFromWeaknessHash } from '../../utils/observationsPath';
import { SEVERITY_BADGE_VARIANT, SEVERITY_RANK, type Severity } from '../../utils/severity';
import { buildExploitOnPortsQuery, buildSameVulnQuery } from '../../utils/vulnQuery';
import { groupByProduct, groupVulnerabilities } from '../../utils/vulnGrouping';
import { Button } from '../ui/button';
import { InfoTip } from '../ui/info-tip';
import { hostEvidenceKey } from './HostEvidenceSection';
import { InspectorSection } from './InspectorSection';
import ProductObservationGroup from './ProductObservationGroup';
import VulnerabilityGroup from './VulnerabilityGroup';
import VulnerabilityTriageDialog, {
  type TriageIntent, type TriageScope, type TriageTarget,
} from './VulnerabilityTriageDialog';

// One line per issue since v5.240.0, so the preview can afford most hosts'
// whole list (the largest host here carries 26).
const VULNERABILITY_PREVIEW_LIMIT = 25;

const VULNERABILITY_SEVERITY_ORDER = SEVERITY_RANK;

// Thin null-tolerant wrapper over the canonical severity→badge-variant map.
const severityBadgeVariant = (severity: string | null | undefined): string =>
  SEVERITY_BADGE_VARIANT[(severity ?? '').toLowerCase() as Severity] ?? 'outline';

const toTimestamp = (value: string | null | undefined) =>
  value ? new Date(value).getTime() : 0;

/** The inspector's read of the informational rows (asked for on demand). */
export interface InformationalRows {
  /** The wider read is in flight. */
  loading: boolean;
  /** The wider read failed. */
  failed: boolean;
  /** Its error, for the sentence that says so. */
  error: unknown;
  /** Ask for them ("N informational hidden · show"). */
  onShow: () => void;
  /** Ask again after a failure. */
  onRetry: () => void;
}

export interface ScannerObservationsSectionProps {
  host: Host;
  /** Put the server's answer onto the inspector's cached host. */
  putHost: (update: (previous: Host) => Host) => void;
  /** See `HostInspectorProps.onQueryHosts`. */
  onQueryHosts?: (query: string) => void;
  informational: InformationalRows;
}

type Triage = { vulnId: number; intent: TriageIntent; scope: TriageScope; reason: string };

export const ScannerObservationsSection: React.FC<ScannerObservationsSectionProps> = ({
  host,
  putHost,
  onQueryHosts,
  informational,
}) => {
  const hostId = host.id;
  const navigate = useNavigate();
  const locationHash = useLocation().hash;
  const toast = useToast();
  const projectId = useProjectId();
  const queryClient = useQueryClient();

  // Pivot a vuln to the host inventory filtered to every host with the same
  // vulnerability. When the parent supplies onQueryHosts (the Hosts SideSheet)
  // it closes the sheet + applies the query; otherwise (standalone HostDetail)
  // we navigate to the filtered /hosts ourselves.
  const handleQueryHosts = useCallback(
    (vuln: HostVulnerability) => {
      const q = buildSameVulnQuery(vuln);
      if (!q) return;
      if (onQueryHosts) onQueryHosts(q);
      else navigate(buildHostsUrl({ q }));
    },
    [onQueryHosts, navigate],
  );

  // Pivot a vuln GROUP to the host inventory filtered to every host with an
  // exploitable finding on ANY of the group's exploitable ports (same-row
  // correlation, not "port open anywhere AND exploit anywhere"). Takes all the
  // group's exploit ports, not one representative row's — a plugin exploitable
  // on 80/443/8080 pivots on all three. Same close-sheet-or-navigate contract
  // as handleQueryHosts.
  const handleQueryExploitPort = useCallback(
    (ports: number[]) => {
      const q = buildExploitOnPortsQuery(ports);
      if (!q) return;
      if (onQueryHosts) onQueryHosts(q);
      else navigate(buildHostsUrl({ q }));
    },
    [onQueryHosts, navigate],
  );

  // Promote / dismiss a scanner vulnerability as a finding (status 'confirmed'
  // promotes; a terminal status dismisses). Idempotent server-side.
  const [triageVuln, setTriageVuln] = useState<TriageTarget | null>(null);
  const [triageReason, setTriageReason] = useState('');
  // v5.238.0 — how far a false-positive dismissal reaches.  It is made in ONE
  // host's inspector about that host's observation, so it defaults to this
  // host; marking the issue a false positive everywhere is an explicit choice.
  const [triageScope, setTriageScope] = useState<TriageScope>('host');

  const triage = useMutation({
    mutationFn: ({ vulnId, intent, scope, reason }: Triage) => promoteVulnerability(projectId, vulnId, {
      status: intent,
      summary: reason || undefined,
      // ALWAYS sent (v5.245.0): what the dialog showed is what the server
      // does, whatever its default — the API's default for a promotion is
      // still the whole issue, the dialog's is this host.
      scope,
    }),
    // The toast names the finding and is true wherever the reader is by then.
    onSuccess: (finding, { vulnId, intent, scope }) => {
      const hostOnly = intent === 'false_positive' && scope === 'host';
      // Scanner findings span every host with the same plugin — report it.
      const span = finding.host_count > 1 ? ` across ${finding.host_count} hosts` : '';
      toast.success(
        intent === 'confirmed'
          ? (scope === 'host'
            ? `Promoted to finding for this host: ${finding.title}`
            : `Promoted to finding${span}: ${finding.title}`)
          : hostOnly
            ? `Dismissed as false positive on this host only: ${finding.title}`
            : `Dismissed as false positive${span}: ${finding.title}`,
        {
          autoHideMs: 8000,
          // A promotion leads to the write-up; a dismissal has none to write.
          action: intent === 'confirmed'
            ? { label: 'Write it up', onClick: () => navigate(`/findings/${finding.id}?edit=report-text`) }
            : { label: 'Open finding', onClick: () => navigate(`/findings/${finding.id}`) },
        },
      );
      // The row says it at once, in the server's own words: the finding it
      // answered with, and this host's row on it.  (5.364.0 — it was two maps
      // of "done in this session" that every row was checked against, which
      // knew nothing of the finding's status: an issue-wide false positive
      // read "Promoted → finding" until the page was reloaded.)
      const here = (finding.hosts ?? []).find((h) => h.host_id === hostId);
      putHost((previous) => ({
        ...previous,
        vulnerabilities: previous.vulnerabilities?.map((row) => (row.id !== vulnId ? row : {
          ...row,
          finding_id: finding.id,
          finding_status: finding.status,
          finding_on_this_host: true,
          finding_endpoint_status: here?.host_status ?? (hostOnly ? 'false_positive' : row.finding_endpoint_status),
        })),
      }));
      // (`putHost` reads the host again too: the issue's OTHER rows on this
      // host are covered by the same finding now.)
      // This host has a finding it did not (or one changed); the promotion
      // took the issue's test results onto the finding, so the tests and the
      // other evidence are out of date too.
      void queryClient.invalidateQueries({ queryKey: ['listFindings'] });
      void queryClient.invalidateQueries({ queryKey: ['listHostTests'] });
      void queryClient.invalidateQueries({ queryKey: hostEvidenceKey(projectId, hostId) });
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to update vulnerability.')),
  });
  const vulnActionId = triage.isPending ? triage.variables.vulnId : null;
  const handlePromoteVuln = () => {
    if (!triageVuln) return;
    const { id: vulnId, intent } = triageVuln;
    const scope = triageScope;
    // The rest is this panel's own state: it is set only while the reader is
    // still on this host.
    triage.mutate({ vulnId, intent, scope, reason: triageReason.trim() }, {
      onSuccess: () => {
        setTriageVuln(null);
        setTriageReason('');
      },
    });
  };
  const openTriage = (vulnId: number, title: string, intent: TriageIntent) => {
    setTriageReason('');
    setTriageScope('host');
    setTriageVuln({ id: vulnId, title, intent });
  };
  const closeTriage = () => {
    setTriageVuln(null);
    setTriageReason('');
  };

  const [showAllVulnerabilities, setShowAllVulnerabilities] = useState(false);
  // Per-vuln expand state for the (often long) description writeup.
  const [expandedVulnIds, setExpandedVulnIds] = useState<Set<number>>(new Set());
  const toggleVulnDescription = (id: number) =>
    setExpandedVulnIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const sortedVulnerabilities = (host.vulnerabilities ?? []).slice().sort((a, b) => {
    const severityA = (a.severity ?? 'unknown').toLowerCase();
    const severityB = (b.severity ?? 'unknown').toLowerCase();
    const rankA =
      VULNERABILITY_SEVERITY_ORDER[severityA] ?? VULNERABILITY_SEVERITY_ORDER['unknown'];
    const rankB =
      VULNERABILITY_SEVERITY_ORDER[severityB] ?? VULNERABILITY_SEVERITY_ORDER['unknown'];
    if (rankA !== rankB) return rankA - rankB;
    const timeA = toTimestamp(a.last_seen ?? a.first_seen);
    const timeB = toTimestamp(b.last_seen ?? b.first_seen);
    if (timeA !== timeB) return timeB - timeA;
    return b.id - a.id;
  });
  // Group by the ISSUE, not the scanner — Nessus and GreenBone report the same
  // problem in their own words, and a flat list made the operator correlate by
  // eye on every host. See utils/vulnGrouping.ts for the keying rules and why
  // there is deliberately no fuzzy matching. An O(n) pass over one host's
  // findings, so memoising buys nothing.
  const vulnGroups = groupVulnerabilities(sortedVulnerabilities);
  // The header count stays the raw finding total (what the scanners reported);
  // the preview limit now counts ISSUES, since that's what a row is.
  const totalVulnerabilities =
    host.vulnerability_summary?.total_vulnerabilities ?? sortedVulnerabilities.length;
  // v5.292.0 — issues about one product on the same ports (one CPE) fold into
  // a product line, so an outdated Tomcat's dozen advisory checks no longer
  // bury the host. The preview limit counts LINES.
  // v5.298.0 — misconfigurations (catalog checks, issue key `check:…`) are
  // listed apart from vulnerabilities, misconfigurations first; each list
  // keeps its severity order.
  const isMisconfig = (item: (typeof groupedItems)[number]) =>
    item.kind !== 'product' && item.group.key.startsWith('check:');
  const groupedItems = groupByProduct(vulnGroups);
  const observationItems = [
    ...groupedItems.filter(isMisconfig),
    ...groupedItems.filter((i) => !isMisconfig(i)),
  ];
  const displayedVulnerabilities = showAllVulnerabilities
    ? observationItems
    : observationItems.slice(0, VULNERABILITY_PREVIEW_LIMIT);
  const misconfigCount = groupedItems.filter(isMisconfig).length;
  const splitWeaknesses = misconfigCount > 0 && misconfigCount < groupedItems.length;
  // v5.215.0 — a host whose only findings are informational still gets the
  // card, so the "N informational hidden · show" affordance has somewhere to
  // live; otherwise the hidden rows would be invisible exactly when they are
  // all there is.
  const hiddenInformational =
    host.informational_included === false ? (host.informational_count ?? 0) : 0;
  const hasVulnerabilities = vulnGroups.length > 0 || hiddenInformational > 0;

  return (
    <>
      {hasVulnerabilities && (
        <InspectorSection
          id="host-detail-vulnerabilities"
          // A `#weakness-<issue>` link (the Scanner observations inspector) opens
          // the section, so its row can open itself (review 2026-10-16, O4 / O7).
          forceOpen={issueFromWeaknessHash(locationHash) !== null}
          title="Scanner observations"
          titleHint="What the scanners reported on this host — one row per observation whichever tool reported it (nmap, NetExec, Nessus…). Not yet judged: promote one to make it a finding under investigation."
          icon={<ShieldAlert className="size-4 shrink-0 text-destructive" aria-hidden />}
          // Rows are issues, so the count is issues. When scanners overlapped,
          // say so rather than showing a number that doesn't match the rows.
          count={vulnGroups.length}
          actions={(
            <>
              {/* v5.289.0 — "5 … from 6 scanner observations" read as a
                  contradiction; name both units and say why they differ. */}
              {totalVulnerabilities > vulnGroups.length && (
                <span className="inline-flex min-w-0 items-center gap-xxs text-caption text-muted-foreground" data-testid="observation-grouping">
                  <span className="min-w-0 truncate">
                    {vulnGroups.length} {vulnGroups.length === 1 ? 'observation' : 'observations'} · from {totalVulnerabilities} scanner rows
                  </span>
                  <InfoTip
                    label="About observations and scanner rows"
                    text="Scanners can report the same thing more than once on a host (on several ports, or from more than one scanner). Rows of the same observation are grouped into one line, so there are fewer observations than scanner rows."
                  />
                </span>
              )}
              {/* v5.215.0 — informational rows are hidden until asked; say how
                  many there are so nothing looks lost. */}
              {(host.informational_count ?? 0) > 0 && host.informational_included === false && !informational.failed && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-caption text-muted-foreground"
                  onClick={informational.onShow}
                  disabled={informational.loading}
                  aria-label={`Show ${host.informational_count} informational findings`}
                >
                  {informational.loading
                    ? 'Loading…'
                    : `${host.informational_count} informational hidden · show`}
                </Button>
              )}
              {/* Said where the rows would have been, with Retry (it was a toast
                  over a link that had stopped spinning). */}
              {informational.failed && (
                <p role="alert" className="break-words text-caption text-destructive">
                  {queryErrorText(informational.error, 'The informational observations could not be loaded.')}{' '}
                  <button type="button" className="text-info hover:underline" onClick={informational.onRetry}>Retry</button>
                </p>
              )}
            </>
          )}
        >
          <div className="space-y-xs">
            {displayedVulnerabilities.map((item, index) => {
              const prev = displayedVulnerabilities[index - 1];
              // A heading where the list changes kind — only when it has both.
              const heading = splitWeaknesses && (index === 0 || isMisconfig(prev) !== isMisconfig(item))
                ? (isMisconfig(item)
                  ? `Misconfigurations (${misconfigCount})`
                  : `Vulnerabilities (${groupedItems.length - misconfigCount})`)
                : null;
              const rowProps = {
                severityBadgeVariant,
                expandedVulnIds,
                onToggleDescription: toggleVulnDescription,
                vulnActionId,
                onTriage: openTriage,
                onQueryHosts: handleQueryHosts,
                onQueryExploitPort: handleQueryExploitPort,
              };
              const row = item.kind === 'product' ? (
                <ProductObservationGroup
                  key={`${host.id}:${item.product.key}`}
                  product={item.product}
                  hostId={host.id}
                  {...rowProps}
                />
              ) : (
                <VulnerabilityGroup
                  // Host-qualified, on top of the inspector's body being keyed by
                  // host: an issue shared by two hosts must never carry its open
                  // state over.
                  key={`${host.id}:${item.group.key}`}
                  group={item.group}
                  {...rowProps}
                />
              );
              return heading ? (
                <React.Fragment key={`h-${heading}`}>
                  <h3 className="pt-xxs text-caption font-semibold uppercase tracking-wide text-muted-foreground">{heading}</h3>
                  {row}
                </React.Fragment>
              ) : row;
            })}
            {observationItems.length > VULNERABILITY_PREVIEW_LIMIT && (
              <div className="flex justify-end">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setShowAllVulnerabilities((prev) => !prev)}
                >
                  {showAllVulnerabilities
                    ? 'Show fewer issues'
                    : `Show all issues (${vulnGroups.length})`}
                </Button>
              </div>
            )}
          </div>
        </InspectorSection>
      )}

      <VulnerabilityTriageDialog
        target={triageVuln}
        scope={triageScope}
        onScopeChange={setTriageScope}
        reason={triageReason}
        onReasonChange={setTriageReason}
        busy={vulnActionId === triageVuln?.id}
        onCancel={closeTriage}
        onConfirm={handlePromoteVuln}
      />
    </>
  );
};

export default ScannerObservationsSection;
