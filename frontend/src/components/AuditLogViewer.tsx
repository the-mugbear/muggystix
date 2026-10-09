/**
 * Audit log viewer (v2.243.0) — admin only, deployment-wide.
 *
 * CLAUDE.md documents audit logging as a feature and the backend has recorded
 * events all along; there was simply no way to read them without hitting the
 * API by hand. That undercuts the point of an audit trail, and especially the
 * auditor role.
 *
 * NOT project-scoped: the underlying endpoint spans the whole deployment,
 * which is why this is an Administration page (`/audit-log`, its own page
 * since 5.350.0; a section of System Settings before) and not a project one.
 * Login attempts and user administration aren't project events.
 */
import React, { useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { AlertCircle, CheckCircle2, Loader2, RefreshCw } from 'lucide-react';
import {
  AuditLogPage,
  AuditLogRow,
  getAuditStats,
  listAuditLogs,
} from '../services/api';
import { GLOBAL, queryErrorText } from '../lib/query';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { formatAuditDetails } from '../utils/auditDetails';
import { personName } from '../utils/people';
import { formatTimestamp } from '../utils/relativeTime';
import { safeFallback } from '../utils/uiStyles';
import PostureSection from './posture/PostureSection';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from './ui/select';

/** Rows per page. 50 made System Settings several screens long; the pager
 *  and the "1–20 of N" total carry the rest. */
export const AUDIT_PAGE_SIZE = 20;
const PAGE_SIZE = AUDIT_PAGE_SIZE;

const AuditLogViewer: React.FC = () => {
  const [actionFilter, setActionFilter] = useState('all');
  // The box shows what is typed; the server is asked once the typing stops
  // (it was asked once per keystroke).
  const [resourceFilter, setResourceFilter] = useState('');
  const resourceType = useDebouncedValue(resourceFilter, 300).trim();
  // Which page is asked for, and of which filters: a new filter starts from
  // the first page in the same request, with nothing to reset.
  const filters = `${actionFilter}\n${resourceType}`;
  const [position, setPosition] = useState({ skip: 0, filters });
  const asked = position.filters === filters ? position.skip : 0;
  const setAsked = (skip: number) => setPosition({ skip, filters });

  const params = {
    skip: asked,
    limit: PAGE_SIZE,
    ...(actionFilter !== 'all' ? { action: actionFilter } : {}),
    ...(resourceType ? { resource_type: resourceType } : {}),
  };
  const query = useQuery({
    queryKey: [GLOBAL, 'listAuditLogs', params],
    queryFn: () => listAuditLogs(params),
    // The page on screen stays until the next one answers.
    placeholderData: keepPreviousData,
  });
  const loading = query.isFetching;
  // A failed fetch must not render as an empty (i.e. "nothing happened")
  // audit trail — that is the most misleading possible state here: it is said,
  // and the rows that were shown go with it.
  const error = queryErrorText(query.error, 'Failed to load audit log.');
  const page: AuditLogPage | null = query.isError ? null : query.data ?? null;
  const rows: AuditLogRow[] = page?.logs ?? [];
  const total = page?.total ?? 0;
  // The rows on screen are the ones the server answered with, which is the
  // previous page while the next is on its way.
  const skip = query.isPlaceholderData ? page?.skip ?? asked : asked;
  const reload = (nextSkip: number) => {
    if (nextSkip === asked) void query.refetch();
    else setAsked(nextSkip);
  };

  // Stats are a nicety; the table is the feature — a failure shows none.
  const { data: stats = null } = useQuery({
    queryKey: [GLOBAL, 'getAuditStats'],
    queryFn: () => getAuditStats(),
  });

  const actionOptions = stats?.top_actions?.map((a) => a.action) ?? [];
  const pageEnd = Math.min(skip + PAGE_SIZE, total);

  const recent = typeof stats?.recent_logs_24h === 'number' ? stats.recent_logs_24h : null;

  // v5.288.0 — login and account events carry no resource, so a page of them
  // was a column of dashes. Show Resource only when a row on THIS page has one;
  // its width goes to Detail otherwise.
  const showResource = rows.some((r) => !!r.resource_type || !!r.resource_id);

  return (
    <PostureSection
      title={
        <>
          Events
          {stats && stats.failed_logs > 0 && (
            <Badge variant="outline" className="border-destructive/40 text-destructive">
              {stats.failed_logs} failed
            </Badge>
          )}
        </>
      }
      description={
        <>
          Authentication and administration events across the whole deployment — not scoped
          to the selected project.
          {recent !== null ? ` ${recent} in the last 24 hours.` : ''}
        </>
      }
      actions={
        <Button size="sm" variant="outline" onClick={() => reload(asked)} disabled={loading}>
          <RefreshCw className={`size-4 ${loading ? 'animate-spin' : ''}`} aria-hidden />
          Refresh
        </Button>
      }
    >
        <div className="mb-sm flex flex-wrap items-end gap-xs">
          <div className="space-y-xxs">
            <label className="text-caption text-muted-foreground" htmlFor="audit-action">Action</label>
            <Select value={actionFilter} onValueChange={setActionFilter}>
              <SelectTrigger id="audit-action" className="h-8 w-[220px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All actions</SelectItem>
                {actionOptions.map((a) => (
                  <SelectItem key={a} value={a}>{a}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-xxs">
            <label className="text-caption text-muted-foreground" htmlFor="audit-resource">
              Resource type
            </label>
            <Input
              id="audit-resource"
              value={resourceFilter}
              onChange={(e) => setResourceFilter(e.target.value)}
              placeholder="e.g. user"
              className="h-8 w-[180px]"
              maxLength={60}
            />
          </div>
        </div>

        {error && <p className="mb-sm text-metadata text-destructive" role="alert">{error}</p>}

        {loading && rows.length === 0 && (
          <p className="py-md text-center text-metadata text-muted-foreground">
            <Loader2 className="mr-xs inline size-4 animate-spin" aria-hidden />
            Loading audit log…
          </p>
        )}

        {!loading && !error && rows.length === 0 && (
          <p className="py-md text-center text-metadata text-muted-foreground">
            No audit events match these filters.
          </p>
        )}

        {rows.length > 0 && (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-metadata" style={{ tableLayout: 'fixed' }}>
                <colgroup>
                  <col style={{ width: '15%' }} />
                  <col style={{ width: '18%' }} />
                  {showResource && <col style={{ width: '13%' }} />}
                  <col style={{ width: '14%' }} />
                  <col style={{ width: '11%' }} />
                  <col style={{ width: showResource ? '29%' : '42%' }} />
                </colgroup>
                <thead>
                  <tr className="border-b border-border text-left text-caption text-muted-foreground">
                    <th className="py-xs pr-xs font-medium">When</th>
                    <th className="py-xs pr-xs font-medium">Action</th>
                    {showResource && <th className="py-xs pr-xs font-medium">Resource</th>}
                    <th className="py-xs pr-xs font-medium">User</th>
                    <th className="py-xs pr-xs font-medium">Source IP</th>
                    <th className="py-xs pr-xs font-medium">Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    // `details` is a JSON column — object for structured events
                    // (login: {"method":"totp"}), string/null otherwise — shown
                    // as readable text ("method: TOTP"), never raw JSON.
                    const detailText = formatAuditDetails(r.details, { actorUsername: r.user_username });
                    const actor = personName(r.user_full_name, r.user_username);
                    const actorTitle = r.user_username ? `@${r.user_username}` : undefined;
                    return (
                    <tr key={r.id} className="border-b border-border/50 align-top">
                      <td className="py-xs pr-xs whitespace-nowrap text-muted-foreground">
                        {formatTimestamp(r.created_at)}
                      </td>
                      <td className="py-xs pr-xs">
                        <span className="flex items-start gap-xxs">
                          {r.success
                            ? <CheckCircle2 className="mt-0.5 size-3 shrink-0 text-success" aria-hidden />
                            : <AlertCircle className="mt-0.5 size-3 shrink-0 text-destructive" aria-hidden />}
                          <span className="min-w-0 truncate font-mono text-caption" title={r.action}>
                            {safeFallback(r.action)}
                          </span>
                        </span>
                      </td>
                      {showResource && (
                        <td className="py-xs pr-xs">
                          <span className="block truncate" title={r.resource_type ?? undefined}>
                            {safeFallback(r.resource_type)}
                            {r.resource_id ? (
                              <span className="text-muted-foreground"> #{r.resource_id}</span>
                            ) : null}
                          </span>
                        </td>
                      )}
                      <td className="py-xs pr-xs">
                        <span className="block truncate" title={actorTitle}>
                          {actor === '—' && r.user_id != null
                            ? <span className="text-muted-foreground">User #{r.user_id}</span>
                            : actor}
                        </span>
                      </td>
                      <td className="py-xs pr-xs">
                        <span className="block truncate font-mono text-caption" title={r.ip_address ?? undefined}>
                          {safeFallback(r.ip_address)}
                        </span>
                      </td>
                      <td className="py-xs pr-xs">
                        <span
                          className="line-clamp-2 break-words"
                          title={[r.error_message, detailText].filter(Boolean).join(' — ') || undefined}
                        >
                          {r.error_message ? (
                            <>
                              <span className="text-destructive">{r.error_message}</span>
                              {detailText && <span className="text-muted-foreground"> · {detailText}</span>}
                            </>
                          ) : safeFallback(detailText)}
                        </span>
                      </td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="mt-sm flex items-center justify-between text-caption text-muted-foreground">
              <span>
                {total === 0 ? 'No events' : `${skip + 1}–${pageEnd} of ${total}`}
              </span>
              <span className="flex gap-xs">
                <Button
                  size="sm" variant="outline"
                  disabled={skip === 0 || loading}
                  onClick={() => reload(Math.max(0, skip - PAGE_SIZE))}
                >
                  Previous
                </Button>
                <Button
                  size="sm" variant="outline"
                  disabled={pageEnd >= total || loading}
                  onClick={() => reload(skip + PAGE_SIZE)}
                >
                  Next
                </Button>
              </span>
            </div>
          </>
        )}
    </PostureSection>
  );
};

export default AuditLogViewer;
