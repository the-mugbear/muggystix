/**
 * /reference/release-notes (5.420.0) — what changed in each version, in a line
 * each, newest first.  The server's file is the one source
 * (`backend/app/data/release_notes.json`, whose check keeps every release
 * non-empty and its lines in kind order); why a change was made is the
 * maintainers' changelog's, not this page's.
 *
 * Analysts and above (`canWriteInSomeProject`, the server's
 * `can_write_in_some_project`): the Reference entry and the About link are not
 * offered below that, and the server answers 403 — said here, never drawn as
 * "no releases".  A release is one section; the one the SERVER runs is marked,
 * and the page opens on it (5.420.1: the About link used to name the versions
 * the frontend was built with, which a deploy of one side only leaves
 * different) — unless the address names another release's anchor.
 */
import React, { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useLocation } from 'react-router-dom';

import { getReleaseNotes } from '../services/api';
import type { Release, ReleaseNoteKind } from '../services/api';
import { queryErrorText } from '../lib/query';
import { formatDate } from '../utils/relativeTime';
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

/** The element id of a release's section (a link may name it after `#`). */
export const releaseAnchor = (backend: string, frontend: string): string => `release-${backend}-${frontend}`;

const ReleaseSection: React.FC<{ release: Release; running: boolean }> = ({ release, running }) => {
  // The server sends the lines in kind order: group the runs as they come.
  const groups = release.entries.reduce<Array<{ kind: ReleaseNoteKind; texts: string[] }>>((out, e) => {
    const last = out[out.length - 1];
    if (last?.kind === e.kind) last.texts.push(e.text);
    else out.push({ kind: e.kind, texts: [e.text] });
    return out;
  }, []);
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
        <dl className="space-y-sm">
          {groups.map((group) => (
            <div key={group.kind} className="grid gap-x-lg gap-y-xxs md:grid-cols-[10rem_minmax(0,1fr)]">
              <dt className="text-caption font-medium text-muted-foreground">{KIND_LABEL[group.kind]}</dt>
              <dd className="min-w-0">
                <ul className="list-disc space-y-xxs pl-md">
                  {group.texts.map((text, i) => (
                    // By position: two lines may read alike; the list never reorders.
                    <li key={i} className="break-words">{text}</li>
                  ))}
                </ul>
              </dd>
            </div>
          ))}
        </dl>
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
  const current = notes.data?.current;

  // Open on the release the address names, else on the one the server runs.
  const target = hash
    ? decodeURIComponent(hash.slice(1))
    : current && releaseAnchor(current.backend, current.frontend);
  const loaded = notes.data != null;
  useEffect(() => {
    if (!loaded || !target) return;
    document.getElementById(target)?.scrollIntoView?.({ block: 'start' });
  }, [loaded, target]);

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
