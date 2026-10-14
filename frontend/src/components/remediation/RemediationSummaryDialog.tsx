/**
 * "Copy summary" on the remediation pages opens this dialog (owner,
 * 2026-10-14: the pattern used across BlueStick) instead of copying
 * straight away: the summary is shown as it will be copied, in a box the
 * reader may adjust for their mail, with Copy beside it — as the follow-up
 * dialog shows its message.
 *
 * The text is made by the caller from the list on screen
 * (`utils/remediation.remediationSummary`) when the button is pressed; the
 * dialog is mounted for one opening, so every opening starts from it.
 */
import React, { useState } from 'react';
import { Check, Copy } from 'lucide-react';

import { copyToClipboard } from '../../utils/clipboard';
import { Button } from '../ui/button';
import {
  Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '../ui/dialog';
import { Label } from '../ui/label';
import { Textarea } from '../ui/textarea';

export const RemediationSummaryDialog: React.FC<{
  /** The summary as made from the list on screen. */
  summary: string;
  /** Where the counts are from, in the page's words ("across the projects you administer (filtered)"). */
  where: string;
  onClose: () => void;
}> = ({ summary, where, onClose }) => {
  const [text, setText] = useState(summary);
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  const copy = async () => {
    const ok = await copyToClipboard(text);
    setFailed(!ok);
    if (ok) { setCopied(true); window.setTimeout(() => setCopied(false), 2000); }
  };
  return (
    <Dialog open onOpenChange={(v) => { if (!v) onClose(); }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Summary</DialogTitle>
          <DialogDescription className="break-words">
            The counts of the list on screen, {where}, as text for a status mail. Change it here before copying if
            you like; the list is not changed.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="flex items-center justify-between gap-sm">
            <Label htmlFor="rem-summary-text">Summary</Label>
            <Button size="sm" variant="outline" className="h-7" onClick={() => void copy()}>
              {copied ? <Check className="size-4" aria-hidden /> : <Copy className="size-4" aria-hidden />}
              {copied ? 'Copied' : 'Copy summary'}
            </Button>
          </div>
          <Textarea id="rem-summary-text" rows={12} value={text} className="mt-xxs font-mono text-caption"
            onChange={(e) => setText(e.target.value)} />
          {failed && (
            <p role="alert" className="mt-xxs text-caption text-destructive">
              The summary could not be copied. Select the text and copy it by hand.
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default RemediationSummaryDialog;
