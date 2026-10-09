import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, it, expect, vi } from 'vitest';

interface P { id: number; name: string; start_date: string | null; created_at: string }
const ctx = vi.hoisted(() => ({ projects: [] as P[], current: 0, selectProject: vi.fn() }));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({
    projects: ctx.projects, currentProject: ctx.projects[ctx.current] ?? null,
    selectProject: ctx.selectProject,
  }),
}));

import ProjectSelector from '../../components/ProjectSelector';
import { projectYear, projectYears } from '../../utils/projectYears';

const project = (id: number, name: string, start: string | null, created = '2026-03-01T10:00:00Z'): P => (
  { id, name, start_date: start, created_at: created }
);
const names = () => within(screen.getByTestId('project-list')).getAllByRole('menuitem').map((el) => el.textContent);
const open = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(screen.getByRole('button', { name: /project/i }));
  return screen.findByRole('menu');
};

beforeEach(() => {
  vi.clearAllMocks();
  ctx.current = 0;
});

describe('projectYear', () => {
  it('is the year the project starts, read from the stored date and not the browser’s time zone', () => {
    expect(projectYear({ start_date: '2027-01-01T00:00:00+00:00', created_at: '2026-12-20T09:00:00Z' })).toBe(2027);
    // No start date: the year it was created.
    expect(projectYear({ start_date: null, created_at: '2026-12-20T09:00:00Z' })).toBe(2026);
    expect(projectYear({ start_date: null, created_at: null })).toBeNull();
    expect(projectYear({ start_date: 'not a date' })).toBeNull();
  });

  it('lists the years newest first with their counts', () => {
    expect(projectYears([
      { start_date: '2026-02-01T00:00:00Z' }, { start_date: '2027-02-01T00:00:00Z' },
      { start_date: null, created_at: '2026-05-01T00:00:00Z' }, { start_date: null, created_at: null },
    ])).toEqual([{ year: 2027, count: 1 }, { year: 2026, count: 2 }]);
  });
});

describe('ProjectSelector', () => {
  // 5.346.0 — with many projects the menu ran past the window and could not
  // be scrolled: it is bounded by the room Radix measures and scrolls inside.
  it('lists any number of projects in a menu that scrolls inside itself', async () => {
    ctx.projects = Array.from({ length: 60 }, (_, i) => project(i + 1, i === 3 ? 'p'.repeat(200) : `Project ${i + 1}`, null));
    const user = userEvent.setup();
    render(<ProjectSelector />);
    const menu = await open(user);
    expect(names()).toHaveLength(60);
    expect(menu.className).toContain('var(--radix-dropdown-menu-content-available-height)');
    expect(screen.getByTestId('project-list').className).toContain('overflow-y-auto');
    // One year: no filter to choose from.
    expect(screen.queryByRole('menuitemradio')).toBeNull();
    // A long name is cut inside its row and readable on hover.
    expect(screen.getByTitle('p'.repeat(200)).className).toContain('truncate');

    await user.click(screen.getByRole('menuitem', { name: 'Project 60' }));
    expect(ctx.selectProject).toHaveBeenCalledWith(ctx.projects[59]);
  });

  // The sidebar cut "Walkthrough 2026-09-30 […" with no way to read the rest.
  it('a long name is readable: on the trigger’s title, and in a menu wider than the trigger up to a cap', async () => {
    const long = 'Walkthrough 2026-09-30 '.repeat(9).trim();
    ctx.projects = [project(1, long, null), project(2, 'Short', null)];
    const user = userEvent.setup();
    render(<ProjectSelector />);
    const trigger = screen.getByRole('button', { name: /project/i });
    expect(trigger).toHaveAttribute('title', long);
    const menu = await open(user);
    // At least the trigger's width, as wide as its content, never past the cap.
    expect(menu.className).toContain('w-max');
    expect(menu.className).toContain('min-w-[max(14rem,var(--radix-dropdown-menu-trigger-width))]');
    expect(menu.className).toContain('max-w-[min(28rem,calc(100vw-1rem))]');
    const item = within(screen.getByTestId('project-list')).getByTitle(long);
    expect(item.className).toContain('truncate');
  });

  // 5.347.0 — "all projects that start in 2026 are shown and then can select 2027".
  it('opens on the current project’s year, and another year or All is one click that keeps the menu open', async () => {
    ctx.projects = [
      project(1, 'Acme 2026', '2026-02-01T00:00:00Z'),
      project(2, 'Bolt 2026', null, '2026-06-01T00:00:00Z'),          // no start date: created in 2026
      project(3, 'Acme 2027', '2027-01-01T00:00:00+00:00', '2026-12-15T00:00:00Z'),
      project(4, 'Cedar 2025', '2025-09-01T00:00:00Z'),
    ];
    const user = userEvent.setup();
    render(<ProjectSelector />);
    await open(user);

    const years = screen.getAllByRole('menuitemradio');
    expect(years.map((el) => el.textContent)).toEqual(['2027', '2026', '2025', 'All']);
    expect(screen.getByRole('menuitemradio', { name: '2026' })).toBeChecked();
    expect(names()).toEqual(['Acme 2026', 'Bolt 2026']);

    await user.click(screen.getByRole('menuitemradio', { name: '2027' }));
    expect(screen.getByRole('menu')).toBeInTheDocument();             // still open
    expect(names()).toEqual(['Acme 2027']);

    await user.click(screen.getByRole('menuitemradio', { name: 'All' }));
    expect(names()).toEqual(['Acme 2026', 'Bolt 2026', 'Acme 2027', 'Cedar 2025']);

    await user.click(screen.getByRole('menuitemradio', { name: '2027' }));
    await user.click(screen.getByRole('menuitem', { name: 'Acme 2027' }));
    expect(ctx.selectProject).toHaveBeenCalledWith(ctx.projects[2]);
  });

  it('follows the project the reader is in: after switching to a 2027 project it opens on 2027', async () => {
    ctx.projects = [project(1, 'Acme 2026', '2026-02-01T00:00:00Z'), project(3, 'Acme 2027', '2027-01-01T00:00:00Z')];
    ctx.current = 1;
    const user = userEvent.setup();
    render(<ProjectSelector />);
    await open(user);
    expect(screen.getByRole('menuitemradio', { name: '2027' })).toBeChecked();
    expect(names()).toEqual(['Acme 2027']);

    // A pick lasts while the menu is open; reopened, it follows the project again.
    await user.click(screen.getByRole('menuitemradio', { name: '2026' }));
    expect(names()).toEqual(['Acme 2026']);
    await user.keyboard('{Escape}');
    await open(user);
    expect(names()).toEqual(['Acme 2027']);
  });
});
