/**
 * Cross-project team roster (SOC-P4) — the member-centric counterpart to
 * the project table: who's on which projects (with roles) and their current
 * workload (assigned open tasks + hosts In Review).  Sorted busiest-first.
 */
import React from 'react';
import { Loader2, RefreshCw, Users } from 'lucide-react';

import { TeamMember, getPortfolioTeam } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent } from './ui/card';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from './ui/table';

type Tone = 'default' | 'destructive' | 'success' | 'info' | 'muted' | 'warning' | 'outline';
const roleTone = (role: string): Tone =>
  role === 'admin' ? 'destructive' : role === 'analyst' ? 'success' : role === 'auditor' ? 'info' : 'muted';

export const PortfolioTeam: React.FC = () => {
  const [members, setMembers] = React.useState<TeamMember[] | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [nonce, setNonce] = React.useState(0);

  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getPortfolioTeam()
      .then((r) => { if (!cancelled) setMembers(r.members); })
      .catch((err) => { if (!cancelled) setError(formatApiError(err, 'Failed to load the team roster.')); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [nonce]);

  if (loading) {
    return (
      <Card>
        <CardContent className="flex items-center gap-xs p-md text-metadata text-muted-foreground" role="status" aria-live="polite">
          <Loader2 className="size-4 animate-spin" aria-hidden /> Loading team roster…
        </CardContent>
      </Card>
    );
  }
  if (error) {
    return (
      <Alert variant="destructive">
        <AlertDescription className="flex flex-wrap items-center justify-between gap-sm">
          <span>{error}</span>
          <Button size="sm" variant="outline" onClick={() => setNonce((n) => n + 1)}>
            <RefreshCw className="size-4" aria-hidden /> Retry
          </Button>
        </AlertDescription>
      </Alert>
    );
  }
  if (!members || members.length === 0) {
    return (
      <Card>
        <CardContent className="p-xl text-center">
          <Users className="mx-auto mb-xs size-12 text-muted-foreground" aria-hidden />
          <p className="text-metadata text-muted-foreground">No team members across your projects yet.</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[22%]">Member</TableHead>
                <TableHead className="w-[40%]">Projects &amp; roles</TableHead>
                <TableHead className="w-[10%] text-right">Projects</TableHead>
                <TableHead className="w-[14%] text-right">Open tasks</TableHead>
                <TableHead className="w-[14%] text-right">In review</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {members.map((m) => (
                <TableRow key={m.user_id}>
                  <TableCell>
                    <p className="truncate font-medium">{m.full_name || m.username}</p>
                    {m.full_name && (
                      <p className="truncate text-caption text-muted-foreground">@{m.username}</p>
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-xxs">
                      {m.projects.map((pr) => (
                        <Badge key={pr.project_id} variant={roleTone(pr.role)} title={`${pr.role} on ${pr.project_name}`}>
                          <span className="max-w-[12rem] truncate">{pr.project_name}</span>
                          <span className="ml-xxs opacity-80">· {pr.role}</span>
                        </Badge>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell className="text-right">{m.project_count}</TableCell>
                  <TableCell className="text-right">
                    {m.open_tasks > 0 ? (
                      <Badge variant={m.open_tasks >= 10 ? 'warning' : 'outline'}>{m.open_tasks}</Badge>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    {m.hosts_in_review > 0 ? m.hosts_in_review : <span className="text-muted-foreground">—</span>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
};

export default PortfolioTeam;
