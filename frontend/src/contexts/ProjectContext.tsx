import React, { createContext, useCallback, useContext, useMemo, useState, ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, Loader2, LogOut, RefreshCw } from 'lucide-react';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Button } from '../components/ui/button';
import { createProject, getProjects, setCurrentProjectId, getCurrentProjectId, Project } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { GLOBAL, ScopedQueryClient, getQueryScope, queryErrorText, setQueryScope } from '../lib/query';
import { useAuth } from './AuthContext';
import { CharacterCount } from '../components/ui/character-count';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Textarea } from '../components/ui/textarea';

// Routes whose URL contains a per-project resource id. After a project
// switch we redirect the operator off these to /operations because
// the previous project's resource won't exist (or, worse, the same
// numeric id will silently resolve to a different scope/host/finding in
// the new project). Static, child-free paths (lists, hubs, settings)
// are project-wide and stay put.
const PROJECT_SCOPED_RESOURCE_ROUTES: RegExp[] = [
  /^\/scans\/[^/]+/,
  /^\/hosts\/[^/]+/,
  /^\/scopes\/[^/]+/,
  /^\/findings\/[^/]+/,
  /^\/reports\/[^/]+/,
  /^\/assist-sessions\/[^/]+/,
  /^\/agent-sessions\/[^/]+/,
];

function isProjectScopedResourceRoute(pathname: string): boolean {
  return PROJECT_SCOPED_RESOURCE_ROUTES.some((re) => re.test(pathname));
}

// Pages whose query string isn't tied to one project — /portfolio and
// /tool-activity span every project (the analyst arrives at tool activity with
// a timestamp, not knowing which project owns it), so their view state
// survives a switch.  /remediation-deadlines too: opening a row's host there
// selects the row's project first, and Back must return to the filtered list.
const CROSS_PROJECT_ROUTES: RegExp[] = [
  /^\/portfolio(\/|$)/, /^\/oversight(\/|$)/, /^\/tool-activity(\/|$)/, /^\/remediation-deadlines(\/|$)/,
];

/** Where to send the operator after switching projects, or null to stay put.
 *  Resource pages go to /operations (their id belongs to the old project).
 *  Project-wide pages keep their path but drop the query string: Hosts,
 *  Findings, Scans, Names and others mirror their filters there, and those
 *  values — tag / scan / label ids, owners, CIDRs — describe the previous
 *  project. Left in place, the remounted page treated them as a shared link
 *  and applied them to the new project; without them it restores the new
 *  project's own saved filters (per-project session storage). */
export function locationAfterProjectSwitch(pathname: string, search: string): string | null {
  if (isProjectScopedResourceRoute(pathname)) return '/operations';
  if (search && !CROSS_PROJECT_ROUTES.some((re) => re.test(pathname))) return pathname;
  return null;
}

function announceProjectChange(name: string): void {
  if (typeof document === 'undefined') return;
  const node = document.getElementById('nm-project-announce');
  if (node) node.textContent = `Active project changed to ${name}`;
}

// MRU ring of recently-selected project ids — replaces the dropped
// "default project" concept as the auto-select source of truth. Top
// of the list is most recent. Capped at 8 so a power user switching
// between many projects doesn't accumulate stale entries forever.
const RECENT_PROJECTS_KEY = 'nm.recentProjectIds';
const RECENT_PROJECTS_CAP = 8;

function readRecentProjectIds(): number[] {
  try {
    const raw = localStorage.getItem(RECENT_PROJECTS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((x) => typeof x === 'number') : [];
  } catch {
    return [];
  }
}

function pushRecentProjectId(id: number): void {
  try {
    const current = readRecentProjectIds().filter((x) => x !== id);
    current.unshift(id);
    localStorage.setItem(
      RECENT_PROJECTS_KEY,
      JSON.stringify(current.slice(0, RECENT_PROJECTS_CAP)),
    );
  } catch {
    // localStorage disabled (private browsing); auto-select falls
    // through to the alphabetical-first project.
  }
}

interface ProjectContextType {
  projects: Project[];
  currentProject: Project | null;
  selectProject: (project: Project) => void;
  isLoading: boolean;
  refreshProjects: () => Promise<void>;
  /** v5.290.0 — add a project the caller just created to the list and make
   *  it the active one, without a refetch (a refresh shows the full-screen
   *  loader, unmounting the page that created it). */
  adoptProject: (project: Project) => void;
  /** Present when the last project fetch failed; null on success (even if empty). */
  loadError: string | null;
}

const ProjectContext = createContext<ProjectContextType>({
  projects: [],
  currentProject: null,
  selectProject: () => {},
  isLoading: true,
  refreshProjects: async () => {},
  adoptProject: () => {},
  loadError: null,
});

export const useProject = () => useContext(ProjectContext);

// The project list is the reader's, not one project's: `GLOBAL` (lib/query).
// Anything else that reads or invalidates the list uses this key and so
// keeps the selector current.
const PROJECTS_KEY = [GLOBAL, 'getProjects'];
const NO_PROJECTS: Project[] = [];

/** The project to work in when the reader has not chosen one in this tab:
 *   1. the one stored on this device (`preferredId` — the operator's last
 *      active project here),
 *   2. the most recently used that is still in the list (`nm.recentProjectIds`,
 *      promoted on every selectProject call),
 *   3. the first by name — only when neither resolves (a fresh install, a
 *      new user).
 *
 *  There is no "default project": engagements are independent, and the old
 *  default was whichever project happened to be created first.  `null` only
 *  for an empty list. */
export function pickProject(list: Project[], preferredId: number | null, recentIds: number[]): Project | null {
  const exact = list.find((p) => p.id === preferredId);
  const mruPick = recentIds.map((id) => list.find((p) => p.id === id)).find(Boolean);
  return exact ?? mruPick ?? (list.length > 0 ? [...list].sort((a, b) => a.name.localeCompare(b.name))[0] : null);
}

export const ProjectProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const queryClient = useQueryClient();
  const list = useQuery({
    queryKey: PROJECTS_KEY,
    queryFn: async () => {
      try {
        return await getProjects();
      } catch (err) {
        console.error('Failed to load projects:', err);
        throw err;
      }
    },
  });
  const projects = list.data ?? NO_PROJECTS;

  // What the reader chose in this tab, and the list it was chosen under.  The
  // current project is DERIVED from it and the list — never a copy that a
  // re-read of the list could leave behind.
  const [chosen, setChosen] = useState<{ project: Project; under: Project[] | undefined } | null>(null);
  const currentProject = useMemo(() => {
    if (!list.data) return chosen?.project ?? null;
    if (chosen) {
      // The list's row for it (a rename shows at once)…
      const listed = list.data.find((p) => p.id === chosen.project.id);
      if (listed) return listed;
      // …or the project as it was handed over, while the list is the one it
      // was chosen under: a list that has changed since, and does not have
      // it, says it is gone.
      if (chosen.under === list.data) return chosen.project;
    }
    return pickProject(list.data, getCurrentProjectId(), readRecentProjectIds());
  }, [list.data, chosen]);
  // The API client addresses the current project by this id (services/api
  // `p()`): it must be the project on screen before any child asks for data,
  // so it is set while rendering, like the cache scope below.
  if (currentProject && getCurrentProjectId() !== currentProject.id) setCurrentProjectId(currentProject.id);
  // The query cache is partitioned by project (lib/query): set while
  // rendering, so every query under this provider is keyed for this project
  // and one project's rows never answer another's question.
  setQueryScope({ ...getQueryScope(), projectId: currentProject?.id ?? null });

  // The full-screen loader: the first read, and a refresh that was ASKED for
  // (`refreshProjects`).  Not every read in flight — another reader of the
  // list (a dialog that lists projects) re-reads it in the background, and a
  // loader there would unmount the page that opened the dialog.
  const [refreshing, setRefreshing] = useState(0);
  const isLoading = list.isPending || refreshing > 0;
  // Fix for UX audit #2: distinguish "fetch failed" from "fetched
  // successfully but the user has no projects".  Previously any
  // failure was swallowed and users saw the misleading
  // "No Projects Available" dead end even when the backend was down.
  // The same rule as the loader: the full-screen error is for a list that
  // was never read, or a refresh that was asked for and failed — a failed
  // background re-read keeps the app, and the list it had.
  const [refreshFailed, setRefreshFailed] = useState(false);
  const loadError = list.isError && (!list.data || refreshFailed)
    ? formatApiError(list.error, 'Failed to load projects. Check backend connection.')
    : null;
  // Both the error and empty-project states below render *instead of*
  // the app Layout, which has no sign-out control of its own.  Without
  // a Sign Out button here a user with no project assignment (or a
  // stale/expired session) is stranded with no way to switch accounts.
  const { logout, hasPermission } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const { refetch } = list;
  const refreshProjects = useCallback(async () => {
    setRefreshing((n) => n + 1);
    try {
      const result = await refetch();
      setRefreshFailed(result.isError);
    } finally {
      setRefreshing((n) => n - 1);
    }
  }, [refetch]);

  const selectProject = useCallback(
    (project: Project) => {
      const previousId = currentProject?.id;
      setChosen({ project, under: queryClient.getQueryData<Project[]>(PROJECTS_KEY) });
      setCurrentProjectId(project.id);
      pushRecentProjectId(project.id);
      announceProjectChange(project.name);

      // CRIT-1: never carry the previous project's URL state across a
      // switch — see locationAfterProjectSwitch. No-op when the user
      // picked the same project again.
      if (previousId !== project.id) {
        const next = locationAfterProjectSwitch(location.pathname, location.search);
        if (next) navigate(next, { replace: true });
      }
    },
    [currentProject?.id, location.pathname, location.search, navigate, queryClient],
  );

  const adoptProject = useCallback(
    (project: Project) => {
      queryClient.setQueryData<Project[]>(PROJECTS_KEY, (prev) => (
        !prev || prev.some((p) => p.id === project.id)
          ? prev
          // The API lists projects by name; keep that order.
          : [...prev, project].sort((a, b) => a.name.localeCompare(b.name))
      ));
      selectProject(project);
    },
    [queryClient, selectProject],
  );

  // Memoize so consumers don't re-render on every Provider render.
  // Same rationale as AuthContext — the topbar + every page subscribe.
  // Declared BEFORE early returns so the hook order is stable across
  // renders (rules of hooks).
  const contextValue = useMemo(
    () => ({ projects, currentProject, selectProject, isLoading, refreshProjects, adoptProject, loadError }),
    [projects, currentProject, selectProject, isLoading, refreshProjects, adoptProject, loadError],
  );

  // Show loading state until projects are loaded and one is selected.
  // This prevents data pages from calling p() before a project is available.
  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center gap-sm text-muted-foreground">
        <Loader2 className="size-8 animate-spin" aria-hidden />
        <span className="text-metadata">Loading projects…</span>
      </div>
    );
  }

  // Error state (distinct from empty) — the previous implementation
  // collapsed all failures into "no projects" which is trust-breaking
  // when the backend is actually down.
  if (loadError) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center p-lg">
        <div className="flex max-w-[520px] flex-col items-center gap-sm text-center">
          <AlertCircle className="size-12 text-destructive" aria-hidden />
          <h2 className="text-subheading">Could not load projects</h2>
          <Alert variant="destructive" className="w-full text-left">
            <AlertDescription>{loadError}</AlertDescription>
          </Alert>
          <p className="text-metadata text-muted-foreground">
            This usually means the backend is unreachable or your session expired. Try again in a
            moment, or sign out and back in if the problem persists.
          </p>
          <div className="flex gap-xs">
            <Button onClick={() => refreshProjects()}>
              <RefreshCw className="size-4" aria-hidden />
              Retry
            </Button>
            <Button variant="outline" onClick={() => logout()}>
              <LogOut className="size-4" aria-hidden />
              Sign Out
            </Button>
          </div>
        </div>
      </div>
    );
  }

  // Confirmed empty — only rendered after a successful fetch that
  // returned an empty project list.  v2.44.2 (regression bug): pre-fix
  // this state advertised "create one if you have the required
  // permissions" but the only button was Sign Out — fresh-install
  // admins were locked out of the app on first login.  Now the
  // admin sees an inline create-project form right here; non-admins
  // still see the original "contact your administrator" copy.
  if (!currentProject && projects.length === 0) {
    return (
      <EmptyProjectStartScreen
        canCreate={hasPermission('admin')}
        onCreated={refreshProjects}
        onSignOut={logout}
      />
    );
  }

  return (
    <ProjectContext.Provider value={contextValue}>
      {/* The client as THIS user and project see it: a save that answers
          after the reader has switched project cannot write its row into the
          other project's cache (lib/query `scopedClient`). */}
      <ScopedQueryClient>{children}</ScopedQueryClient>
    </ProjectContext.Provider>
  );
};


// ---------------------------------------------------------------------------
// EmptyProjectStartScreen
// ---------------------------------------------------------------------------
// v2.44.2: standalone empty-state component rendered when a user lands
// on the app and has zero projects.  Admins + analysts get an inline
// "Create your first project" form (the form-based flow is the same
// one ProjectSettings uses); other roles see the original
// "contact your administrator" copy and a Sign Out button.
//
// Pre-fix the empty-state was a flat "you have no projects, contact an
// admin or create one if you have permission" panel with NO create
// affordance, even for admins.  Fresh installs (with no auto-seed
// project, post v2.40.1) trapped the first admin in a dead-end and the
// only escape was to call the API by hand.

interface EmptyProjectStartScreenProps {
  canCreate: boolean;
  onCreated: () => Promise<void>;
  onSignOut: () => void;
}

const EmptyProjectStartScreen: React.FC<EmptyProjectStartScreenProps> = ({
  canCreate,
  onCreated,
  onSignOut,
}) => {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const create = useMutation({
    mutationFn: (body: { name: string; description?: string }) => createProject(body.name, body.description),
    // Re-read the project list (the provider's loader takes the screen);
    // its pick is the new project, and this screen is not shown again.
    // Pending until then, so the form stays locked.
    onSuccess: () => onCreated(),
  });
  const creating = create.isPending;
  const error = queryErrorText(create.error, 'Failed to create project.');

  const handleCreate = (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim() || creating) return;
    create.mutate({ name: name.trim(), description: description.trim() || undefined });
  };

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-md p-lg">
      <div className="w-full max-w-md text-center">
        <h2 className="mb-xs text-subheading">No Projects Yet</h2>
        <p className="text-metadata text-muted-foreground">
          {canCreate
            ? 'Get started by creating your first project below. Projects isolate scans, hosts, scopes, and findings — most operators start with one named after the engagement.'
            : 'You are not assigned to any projects yet. Contact an administrator to be added to a project.'}
        </p>
      </div>

      {canCreate ? (
        <form
          onSubmit={handleCreate}
          className="flex w-full max-w-md flex-col gap-sm rounded-panel border border-border bg-card p-md"
          aria-label="Create your first project"
        >
          <div className="flex flex-col gap-xxs">
            <Label htmlFor="empty-state-project-name">Project name</Label>
            <Input
              id="empty-state-project-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Acme Q3 engagement"
              // v5.290.0 — was 120, past the API's 100: a 101–120 character
              // name was accepted here and then refused by the server.
              maxLength={100}
              aria-describedby="empty-state-project-name-count"
              autoFocus
              required
              disabled={creating}
            />
            <CharacterCount id="empty-state-project-name-count" value={name} max={100} />
          </div>
          <div className="flex flex-col gap-xxs">
            <Label htmlFor="empty-state-project-description">
              Description <span className="text-caption text-muted-foreground">(optional)</span>
            </Label>
            <Textarea
              id="empty-state-project-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              maxLength={1000}
              disabled={creating}
            />
          </div>
          {error && (
            <Alert variant="destructive">
              <AlertCircle className="size-4" aria-hidden />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <div className="flex items-center justify-between gap-sm">
            <Button type="button" variant="outline" onClick={onSignOut}>
              <LogOut className="size-4" aria-hidden />
              Sign Out
            </Button>
            <Button type="submit" disabled={creating || !name.trim()}>
              {creating ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
              Create Project
            </Button>
          </div>
        </form>
      ) : (
        <Button variant="outline" onClick={onSignOut}>
          <LogOut className="size-4" aria-hidden />
          Sign Out
        </Button>
      )}
    </div>
  );
};
