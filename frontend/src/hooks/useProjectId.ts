/**
 * The current project's id, for the first argument of a project-scoped API
 * function and for its query key (5.353.0; UI_STYLE_GUIDE §48).
 *
 *   const projectId = useProjectId();
 *   const things = useQuery({
 *     queryKey: ['listThings', projectId, filters],
 *     queryFn: ({ signal }) => listThings(projectId, filters, signal),
 *   });
 *
 * It is read while RENDERING, so a request — the first of an operation and
 * every later one — goes to the project the component was showing, whatever
 * the reader does meanwhile.
 *
 * With no project selected it is `NO_PROJECT` (0): the API function then
 * refuses with "No project selected", as it always has.  A page that can be
 * shown without a project guards its reads with `enabled: projectId !== NO_PROJECT`.
 */
import { useProject } from '../contexts/ProjectContext';

export const NO_PROJECT = 0;

export function useProjectId(): number {
  return useProject().currentProject?.id ?? NO_PROJECT;
}
