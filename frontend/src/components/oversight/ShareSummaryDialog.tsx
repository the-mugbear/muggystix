/**
 * "Copy summary" on Oversight (v5.273.0): the notebook's metric set for the
 * current filters, as text to paste into an email or a chat.  Plain text for
 * email and most chats; Markdown for chats that render it (Teams, Mattermost,
 * GitHub).  What is shown is exactly what is copied.
 */
import React, { useMemo, useState } from 'react';
import { Check, Copy } from 'lucide-react';

import type { OversightResponse } from '../../services/api';
import { buildOversightSummary, type SummaryFormat } from '../../utils/oversightSummary';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import {
  Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '../ui/dialog';
import { Label } from '../ui/label';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  data: OversightResponse;
  periodLabel: string;
  filterLabels: string[];
}

/** Clipboard API where the page may use it; a selected textarea otherwise
 *  (an http:// deployment has no navigator.clipboard). */
async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall through to the selection copy.
  }
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  }
  document.body.removeChild(area);
  return ok;
}

const ShareSummaryDialog: React.FC<Props> = ({ open, onOpenChange, data, periodLabel, filterLabels }) => {
  const [format, setFormat] = useState<SummaryFormat>('text');
  const [includeProjects, setIncludeProjects] = useState(true);
  const [includeTesters, setIncludeTesters] = useState(true);
  const [copied, setCopied] = useState<'ok' | 'failed' | null>(null);

  const text = useMemo(
    () => buildOversightSummary(data, { format, includeProjects, includeTesters, periodLabel, filterLabels }),
    [data, format, includeProjects, includeTesters, periodLabel, filterLabels],
  );

  const copy = async () => {
    setCopied((await copyText(text)) ? 'ok' : 'failed');
    window.setTimeout(() => setCopied(null), 2500);
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { onOpenChange(v); if (!v) setCopied(null); }}>
      <DialogContent size="xl">
        <DialogHeader>
          <DialogTitle>Copy summary</DialogTitle>
          <DialogDescription>
            The figures on this page for the current filters — projects, hosts taken into review, reviews, findings by severity and
            the defect rate — ready to paste into an email or a chat.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-sm">
          <div className="flex flex-wrap items-center gap-md">
            <div role="radiogroup" aria-label="Format" className="inline-flex rounded-control border border-border p-[2px]">
              {([['text', 'Plain text'], ['markdown', 'Markdown']] as const).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  aria-checked={format === value}
                  onClick={() => setFormat(value)}
                  className={
                    'rounded-control px-sm py-xxs text-metadata focus:outline-none focus-visible:ring-2 focus-visible:ring-ring '
                    + (format === value ? 'bg-primary text-primary-foreground' : 'text-foreground hover:bg-accent')
                  }
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-xs">
              <Checkbox id="summary-projects" checked={includeProjects} onCheckedChange={(v) => setIncludeProjects(v === true)} />
              <Label htmlFor="summary-projects" className="text-metadata">Per project ({data.projects.length})</Label>
            </div>
            <div className="flex items-center gap-xs">
              <Checkbox id="summary-testers" checked={includeTesters} onCheckedChange={(v) => setIncludeTesters(v === true)} />
              <Label htmlFor="summary-testers" className="text-metadata">Per tester ({data.testers.length})</Label>
            </div>
          </div>
          <p className="text-caption text-muted-foreground">
            {format === 'text'
              ? 'Plain text pastes cleanly into email and most chats.'
              : 'Markdown renders as bold text and tables in chats that support it (Teams, Mattermost, GitHub).'}
          </p>
          <textarea
            readOnly
            aria-label="Summary to copy"
            value={text}
            onFocus={(e) => e.currentTarget.select()}
            className="h-80 w-full resize-y rounded-control border border-input bg-muted/30 p-sm font-mono text-caption text-foreground"
          />
        </DialogBody>
        <DialogFooter className="items-center">
          {copied === 'failed' && (
            <span role="alert" className="mr-auto text-caption text-destructive">
              The browser refused the copy. Click in the text, select all and copy it yourself.
            </span>
          )}
          <Button variant="outline" onClick={() => onOpenChange(false)}>Close</Button>
          <Button onClick={() => void copy()}>
            {copied === 'ok' ? <Check className="size-4" aria-hidden /> : <Copy className="size-4" aria-hidden />}
            {copied === 'ok' ? 'Copied' : 'Copy'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default ShareSummaryDialog;
