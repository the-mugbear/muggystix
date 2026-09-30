import { useProject } from '../contexts/ProjectContext';
import { canStartAgentSession } from '../utils/projectRole';

/** Whether the caller may start an agent session in the current project —
 *  every "Start Agent Session" / "… with your agent" entry point asks this, so
 *  a viewer is never offered a dialog whose start the server refuses. */
export const useCanStartAgentSession = (): boolean =>
  canStartAgentSession(useProject().currentProject);
