/**
 * Where a notification opens (client-free; `pages/Activity.tsx` navigates
 * there).  One function, so a new notification kind gets its link — and its
 * test — in one place instead of falling through to "the host, or nothing".
 */
import type { NotificationItem } from '../services/api';

type Linkable = Pick<NotificationItem, 'type' | 'source_type' | 'source_id' | 'host_id' | 'finding_id'>;

/** The list filter each remediation alert opens. */
const REMEDIATION_ALERT_FILTER: Record<string, string> = {
  remediation_overdue: 'state=overdue',
  remediation_due_soon: 'state=due_soon',
  remediation_deferral_review: 'flag=deferral_review_due',
};

export const notificationHref = (n: Linkable): string | null => {
  if (n.type === 'proposal') {
    // v5.316.0 — one finding opens it; a review run over several opens its
    // proposals.  5.318.0 — only those on YOUR findings (scope=mine): the
    // run's whole list put every finding in front of every author.
    if (n.finding_id) return `/findings/${n.finding_id}#proposals`;
    // A proposal that is on no finding yet (a new finding, an observation):
    // told to project admins, and nobody's "mine" — scope=mine hid exactly
    // these, so the session's whole list is what opens.
    if (n.source_type === 'agent_session_new' && n.source_id) {
      return `/proposals?agent_session_id=${n.source_id}&scope=all`;
    }
    if (n.source_type === 'agent_session' && n.source_id) {
      return `/proposals?agent_session_id=${n.source_id}&scope=mine`;
    }
    return '/proposals?scope=mine';
  }
  // A host test assigned to you: the test on its host (the Tests section
  // scrolls to `#host-test-<id>` and shows it whatever its status), or My
  // work on Operations when the tests are on several hosts.
  if (n.source_type === 'host_test') {
    if (n.host_id && n.source_id) return `/hosts/${n.host_id}#host-test-${n.source_id}`;
    if (n.host_id) return `/hosts/${n.host_id}#host-detail-proposed-tests`;
    return '/operations';
  }
  // A remediation alert: the list its rows are in — overdue, due soon, or
  // deferrals whose review date has come — in the alert's project
  // (`source_id`).  It opens on the cross-project page narrowed to that
  // project, because the alert also goes to global administrators who are
  // not members of it, and to admins of a project since archived: the
  // project's own page would show whichever project the reader has selected.
  const remediation = REMEDIATION_ALERT_FILTER[n.source_type ?? ''];
  if (remediation) {
    return `/remediation-deadlines?${remediation}${n.source_id ? `&project=${n.source_id}` : ''}`;
  }
  if (n.source_type === 'scan' && n.source_id) return `/hosts?scan_ids=${n.source_id}`;
  if (n.source_type === 'report_job' && n.source_id) return `/hosts?reports=1&job=${n.source_id}`;
  if (n.source_type === 'note' && n.finding_id && n.source_id) return `/findings/${n.finding_id}#note-${n.source_id}`;
  if (n.source_type === 'note' && n.host_id && n.source_id) return `/hosts/${n.host_id}#note-${n.source_id}`;
  if (n.host_id) return `/hosts/${n.host_id}`;
  return null;
};
