import React, { createContext, useCallback, useContext, useEffect, useMemo, useState, ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { AlertCircle, Loader2, LogOut, RefreshCw } from 'lucide-react';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Button } from '../components/ui/button';
import { createProject, getProjects, setCurrentProjectId, getCurrentProjectId, Project } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { useAuth } from './AuthContext';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Textarea } from '../components/ui/textarea';

// Routes whose URL contains a per-project resource id. After a project
// switch we redirect the operator off these to /operations because
// the previous project's resource won't exist (or, worse, the same
// numeric id will silently resolve to a different scope/host/plan in
// the new project). Static, child-free paths (lists, hubs, settings)
// are project-wide and stay put.
const PROJECT_SCOPED_RESOURCE_ROUTES: RegExp[] = [
  /^\/test-plans\/[^/]+/,
  /^\/scans\/[^/]+/,
  /^\/hosts\/[^/]+/,
  /^\/scopes\/[^/]+/,
  /^\/recon\/runs\/[^/]+/,
  /^\/executions\/[^/]+/,
];

function isProjectScopedResourceRoute(pathname: string): boolean {
  // Treat `/test-plans/compare`, `/recon/compare`, `/test-plans/:id/compare`
  // as project-scoped too — comparison reads concrete resource ids from
  // the query string.
  if (
    pathname === '/test-plans/compare' ||
    pathname === '/recon/compare'
  ) {
    return true;
  }
  return PROJECT_SCOPED_RESOURCE_ROUTES.some((re) => re.test(pathname));
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
  /** Present when the last project fetch failed; null on success (even if empty). */
  loadError: string | null;
}

const ProjectContext = createContext<ProjectContextType>({
  projects: [],
  currentProject: null,
  selectProject: () => {},
  isLoading: true,
  refreshProjects: async () => {},
  loadError: null,
});

export const useProject = () => useContext(ProjectContext);

export const ProjectProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [projects, setProjects] = useState<Project[]>([]);
  const [currentProject, setCurrentProject] = useState<Project | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  // Fix for UX audit #2: distinguish "fetch failed" from "fetched
  // successfully but the user has no projects".  Previously any
  // failure was swallowed and users saw the misleading
  // "No Projects Available" dead end even when the backend was down.
  const [loadError, setLoadError] = useState<string | null>(null);
  // Both the error and empty-project states below render *instead of*
  // the app Layout, which has no sign-out control of its own.  Without
  // a Sign Out button here a user with no project assignment (or a
  // stale/expired session) is stranded with no way to switch accounts.
  const { logout, hasPermission } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const refreshProjects = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const data = await getProjects();
      setProjects(data);

      // Auto-select order of preference:
      //   1. The project explicitly stored in localStorage (the
      //      operator's last active project on this device).
      //   2. The most-recently-used project from the MRU ring
      //      (`nm.recentProjectIds` — promoted on every selectProject
      //      call, see below).
      //   3. Alphabetical-first project — only when neither of the
      //      above resolves (genuinely fresh install or new user).
      //
      // The previous logic fell back to `find(p => p.is_default)`. We
      // dropped the "default project" concept entirely: pentest
      // engagements are conceptually independent, not hierarchical,
      // and the auto-set default was whichever project happened to
      // be created first — never a meaningful preference. MRU is what
      // the operator actually intends.
      const storedId = getCurrentProjectId();
      const exact = data.find((p) => p.id === storedId);
      const mruIds = readRecentProjectIds();
      const mruPick = mruIds.map((id) => data.find((p) => p.id === id)).find(Boolean);
      const pick = exact ?? mruPick ?? (data.length > 0 ? [...data].sort((a, b) => a.name.localeCompare(b.name))[0] : null);
      if (pick) {
        setCurrentProject(pick);
        setCurrentProjectId(pick.id);
      } else {
        // Explicit empty-success state so the empty-state UI renders
        // only after a confirmed empty list, never after a failure.
        setCurrentProject(null);
      }
    } catch (err) {
      console.error('Failed to load projects:', err);
      setLoadError(formatApiError(err, 'Failed to load projects. Check backend connection.'));
    } finally {
      setIsLoading(false);
    }
  }, []);

  const selectProject = useCallback(
    (project: Project) => {
      const previousId = currentProject?.id;
      setCurrentProject(project);
      setCurrentProjectId(project.id);
      pushRecentProjectId(project.id);
      announceProjectChange(project.name);

      // CRIT-1: when switching projects from a URL that targets a
      // resource id belonging to the *previous* project, redirect to
      // /operations rather than re-fetch a foreign-project id.
      // No-ops when the user picked the same project again, or when
      // we're already on a project-wide page.
      if (
        previousId !== project.id &&
        isProjectScopedResourceRoute(location.pathname)
      ) {
        navigate('/operations', { replace: true });
      }
    },
    [currentProject?.id, location.pathname, navigate],
  );

  useEffect(() => {
    refreshProjects();
  }, [refreshProjects]);

  // Memoize so consumers don't re-render on every Provider render.
  // Same rationale as AuthContext — the topbar + every page subscribe.
  // Declared BEFORE early returns so the hook order is stable across
  // renders (rules of hooks).
  const contextValue = useMemo(
    () => ({ projects, currentProject, selectProject, isLoading, refreshProjects, loadError }),
    [projects, currentProject, selectProject, isLoading, refreshProjects, loadError],
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
        canCreate={hasPermission('admin') || hasPermission('analyst')}
        onCreated={refreshProjects}
        onSignOut={logout}
      />
    );
  }

  return (
    <ProjectContext.Provider value={contextValue}>
      {children}
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
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleCreate = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim() || creating) return;
    setCreating(true);
    setError(null);
    try {
      await createProject(name.trim(), description.trim() || undefined);
      // Re-fetch the project list.  ProjectContext's auto-select picks
      // up the new project on the next render and the screen unmounts.
      await onCreated();
    } catch (err: unknown) {
      setError(formatApiError(err, 'Failed to create project.'));
      setCreating(false);
    }
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
              maxLength={120}
              autoFocus
              required
              disabled={creating}
            />
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
