/**
 * The one notice that a session is about to end (style guide §45).
 *
 * A session lasts a fixed time and is not renewed; when it ends the next
 * request sends the reader to sign in and anything typed and unsaved is lost.
 * Ten minutes before, and again one minute before, a toast says when it ends —
 * in the reader's local time — and offers "Sign in again", which returns to
 * the page they are on.  After the end it says the session has ended, instead
 * of leaving that to be found on the next click.
 *
 * A toast, not a dialog: it takes no focus and blocks no typing.  One id, so
 * each stage replaces the last; it stays until closed.
 */
import React from 'react';

import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { NoticeStage, useSessionExpiryNotice } from '../hooks/useSessionExpiryNotice';
import { formatClockTime } from '../utils/relativeTime';
import { signInAgain } from '../utils/sessionExpiry';

export const SESSION_NOTICE_ID = 'session-expiry';

export const sessionNoticeText = (stage: NoticeStage, expiresAt: number): string => {
  const at = formatClockTime(expiresAt);
  if (stage === 'ended') return `Your session ended at ${at}. Copy anything unsaved before you sign in again.`;
  if (stage === 'last') return `Your session ends in less than a minute, at ${at}. Save your work now.`;
  return `Your session ends at ${at}. Save your work: anything unsaved is lost when it ends.`;
};

const SessionExpiryNotice: React.FC = () => {
  const { token } = useAuth();
  const toast = useToast();
  useSessionExpiryNotice(token, {
    notify: (stage, expiresAt) => {
      const show = stage === 'ended' ? toast.error : toast.warning;
      show(sessionNoticeText(stage, expiresAt), {
        id: SESSION_NOTICE_ID,
        autoHideMs: null,
        action: { label: 'Sign in again', onClick: signInAgain },
      });
    },
    dismiss: () => toast.dismiss(SESSION_NOTICE_ID),
  });
  return null;
};

export default SessionExpiryNotice;
