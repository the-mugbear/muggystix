/**
 * Keeps the session open while the reader works, and the one notice that it is
 * about to end when they do not (style guide §45).
 *
 * A session ends a fixed time after the reader last pressed a key or clicked
 * (`hooks/useSessionRenewal`); when it ends the next request sends the reader
 * to sign in and anything typed and unsaved is lost.  Ten minutes before, and
 * again one minute before, a toast says when it ends — in the reader's local
 * time — and offers "Stay signed in".  After the end it says the session has
 * ended, instead of leaving that to be found on the next click, and offers
 * "Sign in again", which returns to the page they are on.
 *
 * A toast, not a dialog: it takes no focus and blocks no typing.  One id, so
 * each stage replaces the last; it stays until closed or the session is
 * renewed.
 */
import React from 'react';

import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { NoticeStage, useSessionExpiryNotice } from '../hooks/useSessionExpiryNotice';
import { useSessionRenewal } from '../hooks/useSessionRenewal';
import { formatClockTime } from '../utils/relativeTime';
import { signInAgain } from '../utils/sessionExpiry';

export const SESSION_NOTICE_ID = 'session-expiry';

export const sessionNoticeText = (stage: NoticeStage, expiresAt: number): string => {
  const at = formatClockTime(expiresAt);
  if (stage === 'ended') return `Your session ended at ${at}. Copy anything unsaved before you sign in again.`;
  if (stage === 'last') return `Your session ends in less than a minute, at ${at}, unless you carry on working.`;
  return `Your session ends at ${at} unless you carry on working. Anything unsaved is lost when it ends.`;
};

const SessionExpiryNotice: React.FC = () => {
  const { token, renewSession } = useAuth();
  const toast = useToast();
  useSessionRenewal(token, { renew: renewSession });
  useSessionExpiryNotice(token, {
    notify: (stage, expiresAt) => {
      const ended = stage === 'ended';
      const show = ended ? toast.error : toast.warning;
      show(sessionNoticeText(stage, expiresAt), {
        id: SESSION_NOTICE_ID,
        autoHideMs: null,
        action: ended
          ? { label: 'Sign in again', onClick: signInAgain }
          // A renewal that fails leaves the notice, which still says when.
          : { label: 'Stay signed in', onClick: () => { renewSession().catch(() => undefined); } },
      });
    },
    dismiss: () => toast.dismiss(SESSION_NOTICE_ID),
  });
  return null;
};

export default SessionExpiryNotice;
