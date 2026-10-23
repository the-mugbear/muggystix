/**
 * The "why?" prompt for closing a finding (a terminal status: false positive,
 * accepted risk, remediated) — the ONE dialog for it, used by the Findings
 * list (single and bulk), the finding page and the host inspector's Findings
 * section (review 2026-10-22, B3-1: the inspector dropped the chosen status
 * and navigated to the finding page instead; the list and the page each had
 * their own copy of this dialog).
 *
 * The reason is asked for, never required (owner decision): an empty answer
 * saves as "Save without a reason".  The summary lands on the finding's
 * history as the rationale and is carried into the report.
 *
 * Open it a tick AFTER a Radix Select's `onValueChange` (`setTimeout(…, 0)`):
 * a modal opened inside it races the Select's dismiss layer, which can leave
 * the body `pointer-events: none` so the dialog never appears.
 */
import React, { useState } from 'react';
import type { FindingStatus } from '../../services/api';
import { STATUS_LABEL } from '../../utils/findingStatus';
import { useOpeningKey } from '../../hooks/useOpeningKey';
import { Button } from '../ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '../ui/dialog';
import { Textarea } from '../ui/textarea';

export interface StatusReasonRequest {
  status: FindingStatus;
  /** A bulk change: how many findings it sets, and how many of them are not
   *  on the page in front of the reader. */
  bulk?: { count: number; offscreen: number };
}

interface Props {
  /** What is being asked; null = closed. */
  request: StatusReasonRequest | null;
  onCancel: () => void;
  /** The reader's reason, trimmed; undefined when they gave none. */
  onSave: (summary: string | undefined) => void;
}

const plural = (n: number) => (n === 1 ? '' : 's');

export const StatusReasonDialog: React.FC<Props> = ({ request, onCancel, onSave }) => {
  // Every opening starts with an empty reason.
  const opening = useOpeningKey(request);
  return (
    <Dialog open={request !== null} onOpenChange={(v) => { if (!v) onCancel(); }}>
      <DialogContent>
        {request && <Body key={opening} request={request} onCancel={onCancel} onSave={onSave} />}
      </DialogContent>
    </Dialog>
  );
};

const Body: React.FC<{ request: StatusReasonRequest } & Omit<Props, 'request'>> = ({ request, onCancel, onSave }) => {
  const [text, setText] = useState('');
  const { status, bulk } = request;
  return (
    <>
      <DialogHeader>
        <DialogTitle>
          {bulk
            ? `Mark ${bulk.count} finding${plural(bulk.count)} ${STATUS_LABEL[status]}`
            : `Mark ${STATUS_LABEL[status]}`}
        </DialogTitle>
        <DialogDescription>
          {bulk
            ? 'Why? One reason is recorded on every selected finding’s history and carried into reports. You can save without one.'
              + (bulk.offscreen > 0
                ? ` ${bulk.offscreen} of the ${bulk.count} selected ${bulk.offscreen === 1 ? 'is' : 'are'} not on this page.`
                : '')
            : 'Why? The reason is kept on the finding’s history and carried into the report. You can save without one.'}
        </DialogDescription>
      </DialogHeader>
      <Textarea
        rows={3}
        autoFocus
        placeholder="e.g. confirmed false positive — scanner flagged the backport, not the CVE"
        value={text}
        onChange={(e) => setText(e.target.value)}
        aria-label="Disposition reason"
      />
      <DialogFooter>
        <Button variant="outline" onClick={onCancel}>Cancel</Button>
        <Button onClick={() => onSave(text.trim() || undefined)}>
          {text.trim() ? 'Save' : 'Save without a reason'}
        </Button>
      </DialogFooter>
    </>
  );
};

export default StatusReasonDialog;
