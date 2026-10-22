/**
 * What identifies a scanner observation beside its title and severity — its
 * kind, a reported exploit, the CVE, the scanners' check ids, the highest
 * CVSS, the ports, the scanners and when it was last seen — built ONCE for
 * the list's row (one line) and the inspector's header (badges and a line),
 * so the two cannot drift (review 2026-10-16, I11; with O1 / O2).
 */
import React from 'react';

import type { ObservationIssue } from '../../services/api';
import { formatDate } from '../../utils/relativeTime';
import { Badge } from '../ui/badge';

type IssueFacts = Pick<ObservationIssue, 'cve_id' | 'sources' | 'kind' | 'exploitable' | 'plugin_ids'
  | 'cvss_score' | 'ports' | 'port_count' | 'last_seen'>;

export interface IssueMetaPart {
  key: string;
  text: string;
  /** Drawn as a badge in the inspector; plain text everywhere else. */
  badge?: 'outline' | 'destructive-outline';
}

const KIND_LABEL: Record<string, string> = {
  misconfiguration: 'Misconfiguration',
  vulnerability: 'Vulnerability',
  informational: 'Informational',
};

/** The parts, in reading order.  `withKind`: the inspector names every kind;
 *  the row only the one that differs from the list's usual (misconfiguration). */
export const issueMetaParts = (issue: IssueFacts, { withKind = false } = {}): IssueMetaPart[] => {
  const parts: IssueMetaPart[] = [];
  if (issue.kind && (withKind || issue.kind === 'misconfiguration')) {
    parts.push({ key: 'kind', text: withKind ? (KIND_LABEL[issue.kind] ?? issue.kind) : 'misconfiguration', badge: 'outline' });
  }
  if (issue.exploitable) parts.push({ key: 'exploit', text: withKind ? 'Exploit reported' : 'exploit reported', badge: 'destructive-outline' });
  if (issue.cve_id) parts.push({ key: 'cve', text: issue.cve_id });
  const plugins = issue.plugin_ids ?? [];
  if (plugins.length > 0) parts.push({ key: 'plugin', text: `${plugins.length > 1 ? 'checks' : 'check'} ${plugins.join(', ')}` });
  if (issue.cvss_score != null) parts.push({ key: 'cvss', text: `CVSS ${issue.cvss_score}` });
  const ports = issue.ports ?? [];
  const portCount = issue.port_count ?? ports.length;
  if (ports.length > 0) {
    const more = portCount - ports.length;
    parts.push({ key: 'ports', text: `${portCount > 1 ? 'ports' : 'port'} ${ports.join(', ')}${more > 0 ? ` +${more}` : ''}` });
  }
  if (issue.sources.length > 0) parts.push({ key: 'sources', text: `from ${issue.sources.join(', ')}` });
  if (issue.last_seen) parts.push({ key: 'seen', text: `last seen ${formatDate(issue.last_seen)}` });
  return parts;
};

export const IssueMeta: React.FC<{ issue: IssueFacts; variant: 'line' | 'header' }> = ({ issue, variant }) => {
  if (variant === 'line') {
    const text = issueMetaParts(issue).map((p) => p.text).join(' · ');
    return text ? (
      <p className="truncate text-caption text-muted-foreground" title={text} data-testid="issue-meta">{text}</p>
    ) : null;
  }
  const parts = issueMetaParts(issue, { withKind: true });
  const badges = parts.filter((p) => p.badge);
  const line = parts.filter((p) => !p.badge).map((p) => p.text).join(' · ');
  return (
    <>
      {badges.map((p) => <Badge key={p.key} variant={p.badge}>{p.text}</Badge>)}
      {line && (
        <span className="min-w-0 truncate text-caption text-muted-foreground" title={line} data-testid="issue-meta">
          {line}
        </span>
      )}
    </>
  );
};

export default IssueMeta;
