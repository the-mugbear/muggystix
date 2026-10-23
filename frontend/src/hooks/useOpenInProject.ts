/**
 * Open a page of a GIVEN project: switch to it first when it is not the one
 * selected, then navigate — the selection and the navigation are one update,
 * so the destination renders, and asks, with that project (a project page
 * takes its project from `useProjectId()`, never from the link).
 *
 * The one mechanism for a row or a notification that belongs to a project
 * other than the selected one (Tool activity, Remediation deadlines, the
 * notifications).  Only a project in the reader's own list can be opened —
 * an archived one, or one they are no longer a member of, is not in it —
 * so a page of one project is never shown under another's name: the
 * function then does nothing and answers `false`, and the caller says why.
 */
import { useCallback } from 'react';
import { useNavigate } from 'react-router-dom';

import { useProject } from '../contexts/ProjectContext';

/** What a caller says when `openInProject` answered `false` for a named project. */
export const cannotOpenProject = (name: string): string =>
  `${name} is not in your project list — it is archived, or you are not one of its members.`;

export const useOpenInProject = () => {
  const { projects, currentProject, selectProject } = useProject();
  const navigate = useNavigate();
  const canOpen = useCallback(
    (projectId: number) => projects.some((p) => p.id === projectId),
    [projects],
  );
  const openInProject = useCallback(
    (projectId: number, to: string): boolean => {
      if (projectId !== currentProject?.id) {
        const target = projects.find((p) => p.id === projectId);
        if (!target) return false;
        selectProject(target);
      }
      navigate(to);
      return true;
    },
    [currentProject?.id, navigate, projects, selectProject],
  );
  return { openInProject, canOpen };
};
