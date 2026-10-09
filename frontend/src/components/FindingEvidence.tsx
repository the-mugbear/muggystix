/**
 * The evidence records linked to a finding (5.321.0): the test results it was
 * created from, or that an accepted proposal cited. A finding made from a
 * test's result said only "Source execution" — the command and output that
 * showed the issue were on the host page, a click away and unnamed.
 * Renders nothing when the finding has none.
 */
import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import { listEvidenceRecords } from '../services/api';
import { queryErrorText } from '../lib/query';
import PostureSection, { SectionCount } from './posture/PostureSection';
import { EvidenceItem } from './host-inspector/HostEvidenceSection';

const FindingEvidence: React.FC<{ findingId: number }> = ({ findingId }) => {
  const query = useQuery({
    queryKey: ['listEvidenceRecords', { finding_id: findingId, limit: 50 }],
    queryFn: () => listEvidenceRecords({ finding_id: findingId, limit: 50 }),
  });
  const items = query.data?.items ?? null;
  const total = query.data?.total ?? 0;
  const error = queryErrorText(query.error, 'The evidence for this finding could not be loaded.');

  if (!error && (!items || items.length === 0)) return null;
  return (
    <PostureSection
      className="mb-md"
      title={<>Test evidence {total > 0 && <SectionCount>{total}</SectionCount>}</>}
      description="What was run and what came back — recorded as it happened, never changed."
    >
      {error ? (
        <p role="alert" className="text-caption text-destructive">{error}</p>
      ) : (
        <ul className="space-y-sm">
          {(items ?? []).map((rec) => (
            <li key={rec.id} className="min-w-0 space-y-xxs border-b border-border pb-sm last:border-b-0">
              <EvidenceItem rec={rec} hideFindingLink />
              {rec.host_ip && (
                <p className="text-caption">
                  <Link
                    to={`/hosts/${rec.host_id}${rec.host_test_id != null ? `#host-test-${rec.host_test_id}` : ''}`}
                    className="text-info hover:underline"
                  >
                    {rec.host_test_id != null ? `The test on ${rec.host_ip}` : rec.host_ip}
                  </Link>
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
      {items && total > items.length && (
        <p className="mt-xs text-caption text-muted-foreground">Showing the newest {items.length} of {total}.</p>
      )}
    </PostureSection>
  );
};

export default FindingEvidence;
