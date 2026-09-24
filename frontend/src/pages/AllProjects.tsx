/**
 * /settings/projects (v5.265.0) — every project, for global administrators:
 * create one, or open one's settings.  Split out of Project settings, which
 * now covers only the project chosen at the top of the page (it used to mix
 * the two on one page).  Deleting a project lives at the foot of that
 * project's settings, behind a typed-name confirmation.
 */
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, Plus } from 'lucide-react';

import { useProject } from '../contexts/ProjectContext';
import { createProject, type Project } from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import PostureSection from '../components/posture/PostureSection';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Textarea } from '../components/ui/textarea';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '../components/ui/dialog';
import { CharacterCount } from '../components/ui/character-count';
import { PROJECT_NAME_MAX, PROJECT_STATUSES } from './ProjectSettings';

const day = (s?: string | null) => (s ? new Date(s).toLocaleDateString() : null);
const statusLabel = (s: string) => PROJECT_STATUSES.find((x) => x.value === s)?.label ?? s;

const AllProjects: React.FC = () => {
  const { projects, currentProject, selectProject, adoptProject, isLoading } = useProject();
  const navigate = useNavigate();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [creating, setCreating] = useState(false);

  const openSettings = (p: Project) => {
    if (currentProject?.id !== p.id) selectProject(p);
    navigate('/project-settings');
  };

  const create = async () => {
    if (!name.trim()) return;
    setCreating(true);
    try {
      const created = await createProject(name.trim(), description.trim() || undefined);
      setOpen(false);
      setName('');
      setDescription('');
      // v5.290.0 — the new project becomes the active one and opens on its
      // Scope page: declaring scope is the first thing a project needs, and
      // leaving the old project active meant the next upload or scope entry
      // silently went into the wrong engagement.  The toast says so.
      adoptProject(created);
      navigate('/scopes');
      toast.success(`Created ${created.name} and switched to it — you are its admin. Start by declaring its scope.`);
    } catch (err) {
      toast.error(formatApiError(err, 'Could not create the project.'));
    } finally {
      setCreating(false);
    }
  };

  return (
    // Full width like the other hub pages (see Reference.tsx); the table and
    // the dialog keep their own widths.
    <div className="space-y-lg p-md md:p-lg">
      <header className="flex flex-wrap items-start justify-between gap-md">
        <div className="min-w-0">
          <h1 className="text-page-title">All projects</h1>
          <p className="mt-xxs max-w-3xl text-metadata text-muted-foreground">
            Every project on this deployment. Open one to change its details, members, tags and webhooks.
          </p>
        </div>
        <Button size="sm" onClick={() => setOpen(true)}><Plus className="size-4" aria-hidden /> New project</Button>
      </header>

      <PostureSection title={`Projects (${projects.length})`}>
        {isLoading && projects.length === 0 ? (
          <p className="inline-flex items-center gap-xs text-caption text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden /> Loading projects…
          </p>
        ) : projects.length === 0 ? (
          <p className="text-metadata text-muted-foreground">No projects yet.</p>
        ) : (
          <table className="w-full border-collapse text-metadata" style={{ tableLayout: 'fixed' }} aria-label="All projects">
            <thead>
              <tr className="text-left text-caption text-muted-foreground">
                <th className="pb-xxs pr-md font-medium">Project</th>
                <th className="w-32 pb-xxs pr-md font-medium">Status</th>
                <th className="w-52 pb-xxs pr-md font-medium">Engagement window</th>
                <th className="w-24 pb-xxs pr-md text-right font-medium">Members</th>
                <th className="w-36 pb-xxs" aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {projects.map((p) => (
                <tr key={p.id} className="border-t border-border/60 align-top">
                  <td className="py-xs pr-md">
                    <span className="block truncate font-medium text-foreground" title={p.name}>
                      {p.name}{currentProject?.id === p.id && <span className="font-normal text-muted-foreground"> (current)</span>}
                    </span>
                    {p.description && (
                      <span className="block truncate text-caption text-muted-foreground" title={p.description}>{p.description}</span>
                    )}
                  </td>
                  <td className="py-xs pr-md">
                    <Badge variant={p.status === 'archived' ? 'muted' : p.status === 'completed' ? 'success' : 'info'}>
                      {statusLabel(p.status)}
                    </Badge>
                  </td>
                  <td className="py-xs pr-md text-caption">
                    {day(p.start_date) || day(p.end_date)
                      ? `${day(p.start_date) ?? '?'} – ${day(p.end_date) ?? 'open'}`
                      : <span className="text-muted-foreground">No dates</span>}
                  </td>
                  <td className="py-xs pr-md text-right tabular-nums">{p.member_count ?? '—'}</td>
                  <td className="py-xs text-right">
                    <Button size="sm" variant="ghost" onClick={() => openSettings(p)} aria-label={`Open the settings of ${p.name}`}>
                      Open settings
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </PostureSection>

      <Dialog open={open} onOpenChange={(v) => !v && !creating && setOpen(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New project</DialogTitle>
            <DialogDescription>You become its admin; add the team from its settings.</DialogDescription>
          </DialogHeader>
          <form className="space-y-md" onSubmit={(e) => { e.preventDefault(); void create(); }}>
            <div className="space-y-xxs">
              <Label htmlFor="np-name">Name</Label>
              <Input id="np-name" autoFocus maxLength={PROJECT_NAME_MAX} value={name}
                aria-describedby="np-name-count" onChange={(e) => setName(e.target.value)} />
              <CharacterCount id="np-name-count" value={name} max={PROJECT_NAME_MAX} />
            </div>
            <div className="space-y-xxs">
              <Label htmlFor="np-desc">Description</Label>
              <Textarea id="np-desc" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setOpen(false)} disabled={creating}>Cancel</Button>
              <Button type="submit" disabled={creating || !name.trim()}>
                {creating && <Loader2 className="size-4 animate-spin" aria-hidden />} Create project
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default AllProjects;
