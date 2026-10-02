/**
 * Operations' "Tests" tab (5.331.0): ONE table of the tests to do that are
 * the reader's, or free to claim, a page at a time.
 *
 * Each test is listed once, and "Why it is here" says under which kind — its
 * strongest: assigned to the reader, on a host the reader is reviewing, or
 * free to claim (unassigned, critical or high priority).  The kinds are the
 * filter chips, with the server's counts.  Which number is which is said on
 * the page: the first two kinds are the reader's (the tab's count, part of
 * "your queue"); the claimable ones are shared work and are NOT counted as
 * theirs.
 *
 * A test's PRIORITY is not a severity: a neutral outline badge.  The row
 * opens the test on its host's page (`/hosts/:id#host-test-:id`); Claim — on
 * claimable rows, for a role that may write — assigns it to the reader, with
 * an Undo.
 *
 * No "Open in Hosts" link: no page lists tests across hosts.
 */
import React from 'react';
import { Link, useNavigate } from 'react-router-dom';

import type { MyTaskItem, MyTaskReason } from '../../services/api';
import { updateHostTest } from '../../services/api';
import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../../contexts/ToastContext';
import { useListCursor } from '../../hooks/useListCursor';
import { formatApiError } from '../../utils/apiErrors';
import { TEST_KINDS, TEST_KIND_LABEL } from '../../utils/operationsTabs';
import { formatRelativeTime } from '../../utils/relativeTime';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';
import {
  FilterChips, ListBody, PagedFooter, WaitingCell, type ListState, type Pager,
} from './QueueParts';

export const TESTS_TAB_TITLE = 'Tests to do';

/** The kind a test is listed under: its strongest reason. */
export const testKind = (t: MyTaskItem): MyTaskReason =>
  (t.reasons ?? []).includes('assigned')
    ? 'assigned'
    : (t.reasons ?? []).includes('in_review') ? 'in_review' : 'triage';

const WHY: Record<MyTaskReason, string> = {
  assigned: 'assigned to me',
  in_review: 'on a host I review',
  triage: 'free to claim',
};

/** "tool · description" — the label, when the test has one, leads. */
export const testLine = (t: MyTaskItem): string =>
  [t.label, t.tool, t.description].filter(Boolean).join(' · ');

const KIND_TITLE: Record<MyTaskReason, string> = {
  assigned: 'Tests assigned to you.',
  in_review: 'Tests on a host you have In Review that are not assigned to you.',
  triage: 'Unassigned critical and high priority tests anyone may claim — not counted as yours.',
};

const NO_ROWS: MyTaskItem[] = [];
/** A test row opens a host page with the way back, and no queue of hosts. */
const FROM_TESTS_TAB = { fromOperations: true, operationsTab: 'tests' } as const;

export interface MyTestsTableProps {
  /** The page of the list on screen; null while it loads or when it failed. */
  rows: MyTaskItem[] | null;
  state: ListState;
  pager: Pager;
  /** The server's count per kind; null when not known. */
  kindCounts: Record<MyTaskReason, number | null>;
  /** The kind the rows are narrowed to (null = every kind). */
  kind: MyTaskReason | null;
  onKind: (kind: MyTaskReason | null) => void;
  /** The reader's project role allows writes — Claim is hidden otherwise. */
  canWrite: boolean;
  /** After a claim (or its undo): refresh the list and the counts. */
  onChanged: () => void;
  keysActive?: boolean;
}

export const MyTestsTable: React.FC<MyTestsTableProps> = ({
  rows: loaded, state, pager, kindCounts, kind, onKind, canWrite, onChanged, keysActive = true,
}) => {
  const { user } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const rows = loaded ?? NO_ROWS;
  const [claimingId, setClaimingId] = React.useState<number | null>(null);
  const testPath = (t: MyTaskItem) => `/hosts/${t.host_id}#host-test-${t.test_id}`;
  // "Back to my work" on the host page returns to this tab.
  const navState = FROM_TESTS_TAB;
  const { cursorRowProps } = useListCursor(
    rows.length,
    (i) => navigate(testPath(rows[i]), { state: navState }),
    { enabled: keysActive, resetKey: `${kind}:${pager.page}`, getId: (i) => rows[i]?.test_id },
  );

  const claim = async (t: MyTaskItem) => {
    if (user?.id == null) return;
    setClaimingId(t.test_id);
    try {
      const claimed = await updateHostTest(t.test_id, {
        assigned_to_id: user.id,
        expected_revision: t.revision,
      });
      // Undoable: the test was unassigned before the claim.
      toast.success("Claimed — it's now in your assigned tests", {
        autoHideMs: 6000,
        action: {
          label: 'Undo',
          onClick: () => {
            updateHostTest(t.test_id, {
              assigned_to_id: null,
              expected_revision: claimed.revision,
            })
              .then(onChanged)
              .catch((err) => toast.error(formatApiError(err, 'Could not undo the claim.')));
          },
        },
      });
      onChanged(); // it moves from "free to claim" to "assigned to me"
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to claim.'));
    } finally {
      setClaimingId(null);
    }
  };

  const known = (v: number | null) => v ?? 0;
  const mine = kindCounts.assigned == null && kindCounts.in_review == null
    ? null : known(kindCounts.assigned) + known(kindCounts.in_review);
  const all = mine == null && kindCounts.triage == null ? null : known(mine) + known(kindCounts.triage);
  const n = (v: number | null) => (v == null ? '—' : v.toLocaleString());
  const showAction = canWrite && rows.some((t) => testKind(t) === 'triage');

  return (
    <div className="min-w-0">
      {/* Which number is which: the tab counts the reader's own. */}
      <p className="mb-sm text-caption text-muted-foreground">
        <span className="font-medium text-foreground">{n(mine)} yours</span> — assigned to you, or on a host you
        are reviewing.{' '}
        <span className="font-medium text-foreground">{n(kindCounts.triage)} free to claim</span> — unassigned
        critical and high priority tests; not counted as yours.
      </p>
      <FilterChips
        label="Filter by why the test is here"
        allLabel="All"
        allCount={all}
        chips={TEST_KINDS.map((k) => ({
          key: k, label: TEST_KIND_LABEL[k], count: kindCounts[k], title: KIND_TITLE[k],
        }))}
        selected={kind}
        onSelect={onKind}
      />
      <ListBody
        rows={loaded}
        state={state}
        what="the tests to do"
        empty={kind != null
          ? `Nothing here — no test is ${WHY[kind]}.`
          : 'Nothing here — a test shows when it is assigned to you, is on a host you are reviewing, or is unassigned with critical or high priority.'}
      >
        {() => (
          <div>
            <div className="min-w-0 overflow-x-auto">
              <Table aria-label={TESTS_TAB_TITLE}>
                <TableHeader>
                  <TableRow className="hover:bg-transparent hover:shadow-none">
                    <TableHead className="w-[6.5rem]">Priority</TableHead>
                    <TableHead className="w-[8.5rem]">Host</TableHead>
                    <TableHead>Test</TableHead>
                    <TableHead className="w-[10.5rem]">Why it is here</TableHead>
                    <TableHead className="w-[5.5rem] text-right"
                      title="How long since the test was last updated.">
                      Waiting
                    </TableHead>
                    {showAction && <TableHead className="w-[5.5rem] text-right">Action</TableHead>}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((t, i) => {
                    const why = testKind(t);
                    const line = testLine(t);
                    return (
                      <TableRow key={t.test_id} data-kind={why} {...cursorRowProps(i)}>
                        <TableCell className="truncate align-middle">
                          {/* A priority, not a severity: an outline badge. */}
                          <Badge variant="outline" className="max-w-full whitespace-nowrap"
                            title={`${t.priority} priority`}>
                            <span className="truncate">{t.priority}</span>
                          </Badge>
                        </TableCell>
                        <TableCell className="truncate align-middle font-mono"
                          title={t.host_hostname ? `${t.host_ip} · ${t.host_hostname}` : t.host_ip}>
                          {t.host_ip}
                        </TableCell>
                        <TableCell className="truncate align-middle" title={line}>
                          <Link
                            to={testPath(t)}
                            state={navState}
                            className="rounded text-foreground hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          >
                            {line || `Test ${t.test_id}`}
                          </Link>
                        </TableCell>
                        <TableCell className="truncate align-middle text-muted-foreground" title={KIND_TITLE[why]}>
                          {WHY[why]}
                        </TableCell>
                        <TableCell className="truncate text-right align-middle">
                          <WaitingCell
                            waiting={t.updated_at ? formatRelativeTime(t.updated_at, { style: 'compact' }) : ''}
                          />
                        </TableCell>
                        {showAction && (
                          <TableCell className="whitespace-nowrap py-xxs text-right align-middle">
                            {why === 'triage' && (
                              <Button
                                size="sm" variant="ghost" className="h-7 text-info"
                                disabled={claimingId === t.test_id}
                                onClick={() => void claim(t)}
                                title="Assign this test to yourself. It joins your assigned tests."
                              >
                                {claimingId === t.test_id ? 'Claiming…' : 'Claim'}
                              </Button>
                            )}
                          </TableCell>
                        )}
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
            <PagedFooter pager={pager} shown={rows.length} noun="tests" />
          </div>
        )}
      </ListBody>
    </div>
  );
};

export default MyTestsTable;
