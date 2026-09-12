import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect, vi } from 'vitest';

// Only the pure helper is under test; keep the axios client (stubbed in
// setupTests) out of the import graph.
vi.mock('../../services/api', () => ({
  createProject: vi.fn(),
  getProjects: vi.fn(),
  setCurrentProjectId: vi.fn(),
  getCurrentProjectId: vi.fn(),
}));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({}) }));

import { locationAfterProjectSwitch } from '../../contexts/ProjectContext';

// Filters were carried across a project switch: project-wide pages mirror
// their filters into the query string, the switch left it in place, and the
// remounted page read it as a shared link — applying one project's tag ids,
// owners and CIDRs to another.
describe('locationAfterProjectSwitch', () => {
  it("drops a project-wide page's query string", () => {
    expect(locationAfterProjectSwitch('/hosts', '?subnets=10.0.0.0%2F24&tags=3')).toBe('/hosts');
    expect(locationAfterProjectSwitch('/findings', '?owner=7&status=open')).toBe('/findings');
    expect(locationAfterProjectSwitch('/names', '?name_id=42')).toBe('/names');
  });

  it('stays put when there is nothing to carry over', () => {
    expect(locationAfterProjectSwitch('/hosts', '')).toBeNull();
  });

  it('sends resource pages to /operations', () => {
    expect(locationAfterProjectSwitch('/hosts/12', '')).toBe('/operations');
    expect(locationAfterProjectSwitch('/test-plans/compare', '?a=1&b=2')).toBe('/operations');
  });

  it('keeps the view of pages that span projects', () => {
    expect(locationAfterProjectSwitch('/portfolio', '?view=table')).toBeNull();
    // /tool-activity is cross-project by design (App.tsx): the analyst
    // arrives with a timestamp, not knowing which project owns the activity.
    expect(locationAfterProjectSwitch('/tool-activity', '?start=2026-09-01')).toBeNull();
  });
});

// Drift guard: every route carrying a resource id must redirect on a switch.
// The two lists here are maintained by hand, and /findings/:findingId and
// /assist-sessions/:sessionId were both missing — the page stayed open and
// re-requested the old project's id. Rather than restate the routes, read
// them from App.tsx, which is where they are actually declared.
describe('every route with a resource id redirects after a project switch', () => {
  // vitest runs from the frontend root; import.meta.url isn't a file: URL here.
  const appSource = readFileSync(resolve(process.cwd(), 'src/App.tsx'), 'utf8');
  const routes = [...appSource.matchAll(/path="(\/[^"]*:[^"]*)"/g)].map((m) => m[1]);

  it('finds the routes to check', () => {
    // A parsing change that silently matched nothing would make this suite
    // pass while checking nothing.
    expect(routes.length).toBeGreaterThanOrEqual(8);
    expect(routes).toContain('/findings/:findingId');
  });

  it.each(routes)('%s redirects to /operations', (route) => {
    const sample = route.replace(/:[A-Za-z0-9_]+/g, '123');
    expect(locationAfterProjectSwitch(sample, '')).toBe('/operations');
  });
});
