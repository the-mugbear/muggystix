/**
 * /reference/release-notes (5.420.0) — what changed in each version, in a line
 * each, newest first.  The server's file is the one source
 * (`backend/app/data/release_notes.json`); why a change was made is the
 * maintainers' changelog's, not this page's.
 *
 * Analysts and above (`canWriteInSomeProject`, the server's
 * `can_write_in_some_project`): the Reference entry and the About link are not
 * offered below that, and the server answers 403 — said here, never drawn as
 * "no releases".  A release is one section; the one this server runs is marked
 * and is the About dialog's anchor (`releaseAnchor`).
 */
import React, { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useLocation } from 'react-router-dom';

import { getReleaseNotes } from '../services/api';
import type { Release, ReleaseNoteKind } from '../services/api';
import { queryErrorText } from '../lib/query';
import { formatDate } from '../utils/relativeTime';
import { releaseAnchor } from '../utils/releaseNotes';
import PostureSection from '../components/posture/PostureSection';
import { CardListSkeleton } from '../components/PageSkeleton';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';

const KIND_LABEL: Record<ReleaseNoteKind, string> = {
  new: 'New',
  changed: 'Changed',
  fixed: 'Fixed',
  before_upgrading: 'Before upgrading',
};

const ReleaseSection: React.FC<{ release: Release; running: boolean }> = ({ release, running }) => {
  const kinds = release.entries.reduce<ReleaseNoteKind[]>(
    (seen, e) => (seen.includes(e.kind) ? seen : [...seen, e.kind]), [],
  );
  return (
    <div id={releaseAnchor(release.backend, release.frontend)} className="scroll-mt-lg">
      <PostureSection
        title={(
          <>
            <span className="font-mono">{release.backend}</span>
            <span className="text-muted-foreground">·</span>
            <span className="font-mono">{release.frontend}</span>
            {running && <Badge variant="secondary">Running here</Badge>}
          </>
        )}
        description={formatDate(release.date)}
      >
        {release.entries.length === 0 ? (
          <p className="text-metadata text-muted-foreground">No notes written for this release.</p>
        ) : (
          <dl className="space-y-sm">
            {kinds.map((kind) => (
              <div key={kind} className="grid gap-x-lg gap-y-xxs md:grid-cols-[10rem_minmax(0,1fr)]">
                <dt className="text-caption font-medium text-muted-foreground">{KIND_LABEL[kind]}</dt>
                <dd className="min-w-0">
                  <ul className="list-disc space-y-xxs pl-md">
                    {release.entries.filter((e) => e.kind === kind).map((e) => (
                      <li key={e.text} className="break-words">{e.text}</li>
                    ))}
                  </ul>
                </dd>
              </div>
            ))}
          </dl>
        )}
      </PostureSection>
    </div>
  );
};

const ReleaseNotes: React.FC = () => {
  const notes = useQuery({
    queryKey: ['getReleaseNotes'],
    queryFn: ({ signal }) => getReleaseNotes(signal),
  });
  const error = queryErrorText(notes.error, 'Could not load the release notes.');
  const { hash } = useLocation();

  // The About dialog opens the page at the running release.
  const loaded = notes.data != null;
  useEffect(() => {
    if (!loaded || !hash) return;
    document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView?.({ block: 'start' });
  }, [loaded, hash]);

  const current = notes.data?.current;
  return (
    <div className="space-y-lg p-md md:p-lg">
      <header>
        <h1 className="text-page-title">Release notes</h1>
        <p className="mt-xxs max-w-3xl text-metadata text-muted-foreground">
          What changed in each version of BlueStick, newest first.
        </p>
      </header>
      {notes.isPending && <CardListSkeleton />}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {notes.data && notes.data.releases.length === 0 && (
        <p className="text-metadata text-muted-foreground">No release has notes yet.</p>
      )}
      {notes.data?.releases.map((release) => (
        <ReleaseSection
          key={releaseAnchor(release.backend, release.frontend)}
          release={release}
          running={current?.backend === release.backend && current?.frontend === release.frontend}
        />
      ))}
    </div>
  );
};

export default ReleaseNotes;
