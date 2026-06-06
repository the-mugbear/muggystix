import React from 'react';
import { MessageSquareHeart } from 'lucide-react';
import HubLanding from './HubLanding';
import { ActivityPulseIcon } from '../../components/AppIcons';

const CollaborationHub: React.FC = () => (
  <HubLanding
    title="Collaboration"
    subtitle="The team-coordination surfaces — host-attached notes and mentions, and the agent-feedback signal that drives product improvements."
    links={[
      {
        label: 'Activity',
        path: '/activity',
        description: 'Project-wide host-notes feed with threaded replies.  Mention teammates with @username to ping them.',
        Icon: ActivityPulseIcon,
      },
      {
        label: 'Agent Feedback',
        path: '/feedback',
        description: 'Structured post-workflow feedback from agents — tool suggestions, API critiques, friction notes.  Admin-only.',
        Icon: MessageSquareHeart,
      },
    ]}
  />
);

export default CollaborationHub;
