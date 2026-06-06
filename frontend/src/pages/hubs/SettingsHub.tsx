import React from 'react';
import {
  FileWarning,
  KeyRound,
  Network as NetworkIcon,
  Settings as SettingsIcon,
  Sparkles,
} from 'lucide-react';
import HubLanding from './HubLanding';
import { CogSixIcon, UserCardIcon } from '../../components/AppIcons';

/**
 * Settings hub — grouped layout so the seven sub-pages don't all
 * compete for attention as a flat 7-card grid.  Groups follow the
 * mental model the user actually navigates:
 *
 *   - Project setup: configuration scoped to the currently-selected
 *     project (members, scanner creds, LLM endpoints, ingest health).
 *   - Your account: personal settings (the only "just-you" surface).
 *   - System administration: cross-project + cross-user admin.  Only
 *     visible to admins; the grouping makes the role boundary explicit.
 *   - Documentation: reference material; not a setting per se, but
 *     it lives here historically because it's where the sidebar puts it.
 */
const SettingsHub: React.FC = () => (
  <HubLanding
    title="Settings"
    subtitle="Project members + roles, scanner / LLM integrations, system admin, your account, and built-in docs."
    sections={[
      {
        label: 'Project setup',
        description: 'Per-project configuration. Scoped to the project you have selected in the topbar.',
        links: [
          {
            label: 'Project',
            path: '/project-settings',
            description: 'Project members, roles, and the agent API key bound to this project.',
            Icon: SettingsIcon,
          },
          {
            label: 'Scanner Integrations',
            path: '/integrations',
            description:
              'API keys for external scanners (Nessus, Shodan, etc.) that feed into the ingestion pipeline.',
            Icon: KeyRound,
          },
          {
            label: 'LLM Providers',
            path: '/llm-settings',
            description:
              'Configure the LLM endpoints (OpenAI, Anthropic, local) that the in-app agent panel uses.',
            Icon: Sparkles,
          },
          {
            label: 'Ingestion Results',
            path: '/parse-errors',
            description:
              'Per-file parse-error log from the scan ingestion pipeline. Use to debug ingestion failures.',
            Icon: FileWarning,
          },
        ],
      },
      {
        label: 'Your account',
        description: 'Personal preferences and active sessions for the signed-in user.',
        links: [
          {
            label: 'Profile',
            path: '/profile',
            description: 'Your account: full name, password, and active sessions.',
            Icon: UserCardIcon,
          },
        ],
      },
      {
        label: 'System administration',
        description: 'Cross-project, cross-user admin. Hidden for non-admin roles.',
        links: [
          {
            label: 'System',
            path: '/system-settings',
            description:
              'User management, system-wide security settings, and audit configuration.',
            Icon: CogSixIcon,
          },
        ],
      },
      {
        label: 'Documentation',
        description: 'Built-in reference material and developer docs.',
        links: [
          {
            label: 'Reference',
            path: '/reference',
            description:
              'Built-in docs: User Guide, Tool Reference, SBOM, and other developer references.',
            Icon: NetworkIcon,
          },
        ],
      },
    ]}
  />
);

export default SettingsHub;
