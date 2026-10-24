/**
 * One tab's view of a running presentation (5.424.0): the poll that keeps it
 * following (and reports presence — and, for a Display, what it RENDERED),
 * and the state machine that decides what the poll's and the moves' answers
 * do (`utils/presentationSync`).
 *
 * The poll is a heartbeat every second that keeps running in a hidden tab
 * (`pollEvery(…, { inBackground: true })`), and runs again at once when the
 * tab is shown or the network returns.  A Display passes `rendered`: the
 * revision and position it last finished drawing (fonts loaded, images
 * decoded) — sent on every heartbeat, stored by the server only if newer.
 */
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import axios from 'axios';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { heartbeatPresentation, movePresentation, type PresentationState, type RenderedAck } from '../services/api';
import { pollEvery } from '../lib/query';
import { MoveConflict, PresentationSync, type SyncView, type Target } from '../utils/presentationSync';

export type TabKind = 'display' | 'controller' | 'browse';

/** A move that has not answered in this long has an unknown outcome. */
export const MOVE_TIMEOUT_MS = 8000;

export interface PresentationSyncHandle extends SyncView {
  go: (target: Target) => boolean;
  /** Accept a state another write answered with (Take control, End). */
  read: (answer: PresentationState) => void;
  dismissLostControl: () => void;
  /** The poll's own failure, if the last read failed. */
  readFailed: boolean;
  /** Record what this Display has finished rendering (sent with the next heartbeat). */
  setRendered: (ack: RenderedAck | null) => void;
  refresh: () => void;
}

export function usePresentationSync(
  projectId: number, presentationId: number | null, clientId: string, kind: TabKind,
): PresentationSyncHandle {
  const queryClient = useQueryClient();
  const moveMutation = useMutation({
    mutationFn: (args: { revision: number; target: Target }) => movePresentation(
      projectId, presentationId ?? 0,
      { client_id: clientId, revision: args.revision, slide_key: args.target.slide_key, host_filter: args.target.host_filter },
      MOVE_TIMEOUT_MS,
    ),
  });
  const mutateRef = useRef(moveMutation.mutateAsync);
  mutateRef.current = moveMutation.mutateAsync;
  const heartbeatKey = useMemo(
    () => ['heartbeatPresentation', projectId, presentationId, clientId, kind] as const,
    [projectId, presentationId, clientId, kind],
  );

  const heartbeatKeyRef = useRef<readonly unknown[]>(heartbeatKey);
  heartbeatKeyRef.current = heartbeatKey;

  const [sync] = useState(() => new PresentationSync({
    move: async (revision, target) => {
      try {
        return await mutateRef.current({ revision, target });
      } catch (error) {
        if (axios.isAxiosError(error) && error.response?.status === 409) {
          const detail = (error.response.data as { detail?: { presentation?: unknown } } | undefined)?.detail;
          if (detail && typeof detail === 'object' && detail.presentation) {
            throw new MoveConflict(detail.presentation as never);
          }
        }
        throw error;
      }
    },
    requestRead: () => { void queryClient.refetchQueries({ queryKey: heartbeatKeyRef.current }); },
  }));

  const poll = useQuery({
    queryKey: heartbeatKey,
    queryFn: async ({ signal }) => {
      const answer = await heartbeatPresentation(
        projectId, presentationId ?? 0,
        { client_id: clientId, kind, rendered: kind === 'display' ? sync.renderedAck() : null },
        signal,
      );
      sync.read(answer);
      return answer;
    },
    enabled: presentationId != null,
    ...pollEvery(1000, { inBackground: true }),
  });

  // Shown again, or back online: read now.
  useEffect(() => {
    const now = () => { if (presentationId != null) void poll.refetch(); };
    const onVisible = () => { if (document.visibilityState === 'visible') now(); };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('online', now);
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('online', now);
    };
  }, [poll, presentationId]);

  const view = useSyncExternalStore(sync.subscribe, sync.getView);
  const setRendered = useCallback((ack: RenderedAck | null) => {
    // A newly finished render is reported at once, not a second later.
    if (sync.setRendered(ack)) {
      void queryClient.refetchQueries({ queryKey: heartbeatKeyRef.current });
    }
  }, [queryClient, sync]);

  return {
    ...view,
    go: sync.go.bind(sync),
    read: sync.read.bind(sync),
    dismissLostControl: sync.dismissLostControl.bind(sync),
    readFailed: poll.isError,
    setRendered,
    refresh: () => { void poll.refetch(); },
  };
}
