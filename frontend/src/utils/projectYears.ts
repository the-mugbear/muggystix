/**
 * The year a project belongs to in the project selector's filter (5.347.0):
 * the year it STARTS — its start date as entered in Project settings — and,
 * for a project nobody gave a start date, the year it was created.
 *
 * Read from the stored date's own digits, as Project settings shows it
 * (`start_date.split('T')[0]`): a start of 1 January must not become the year
 * before in a browser west of UTC.
 */
export interface ProjectDates {
  start_date?: string | null;
  created_at?: string | null;
}

export const projectYear = (project: ProjectDates): number | null => {
  const stamp = project.start_date || project.created_at;
  const year = stamp ? Number(stamp.slice(0, 4)) : NaN;
  return Number.isInteger(year) && year > 0 ? year : null;
};

/** The years the projects fall in, newest first, with how many each holds. */
export const projectYears = (projects: ProjectDates[]): Array<{ year: number; count: number }> => {
  const counts = new Map<number, number>();
  projects.forEach((p) => {
    const year = projectYear(p);
    if (year != null) counts.set(year, (counts.get(year) ?? 0) + 1);
  });
  return [...counts.entries()].sort((a, b) => b[0] - a[0]).map(([year, count]) => ({ year, count }));
};
