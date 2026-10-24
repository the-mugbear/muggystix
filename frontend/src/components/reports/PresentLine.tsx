/**
 * An issued report's way into presentation mode (5.424.0): "Present" when
 * nobody is presenting it, else who is driving, "Join the presentation" and
 * "Open the display".  Presenting opens the Controller, which starts the
 * presentation from ITS tab (`?start=1`) so that tab is the driving one.
 */
import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ExternalLink, MonitorUp } from 'lucide-react';

import { getLivePresentation } from '../../services/api';
import { useProjectId } from '../../hooks/useProjectId';
import PersonAvatar from '../PersonAvatar';

const PresentLine: React.FC<{ reportId: number }> = ({ reportId }) => {
  const projectId = useProjectId();
  const live = useQuery({
    queryKey: ['getLivePresentation', projectId, reportId, null],
    queryFn: ({ signal }) => getLivePresentation(projectId, reportId, null, signal),
  });
  const p = live.data?.live ?? null;
  return (
    <p className="flex flex-wrap items-center gap-x-sm gap-y-xxs text-metadata" data-testid="present-line">
      {p ? (
        <>
          <span className="inline-flex items-center gap-xxs">
            Being presented — driving: <PersonAvatar person={p.driver} size={20} /> {p.driver?.name ?? 'nobody'}
          </span>
          <Link to={`/reports/${reportId}/present`} className="text-info hover:underline">Join the presentation</Link>
          <a href={`/reports/${reportId}/present/display`} target="_blank" rel="noopener noreferrer"
            className="inline-flex items-center gap-xxs text-info hover:underline">
            Open the display <ExternalLink className="size-3.5" aria-hidden />
          </a>
        </>
      ) : (
        <Link to={`/reports/${reportId}/present?start=1`} className="inline-flex items-center gap-xxs text-info hover:underline">
          <MonitorUp className="size-4" aria-hidden /> Present
        </Link>
      )}
    </p>
  );
};

export default PresentLine;
