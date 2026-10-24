/**
 * The report's agendas, edited (5.425.0, plan E3; auditor and above, like the
 * rest of presentation mode).  An agenda is an ordered list of ANCHORS — the
 * overview, the summary, the scope, a finding, one section of a finding —
 * offered from the slides themselves (`anchorOptions`), so it can name only
 * what the report holds.  Every save is a new version on the server; a
 * running presentation keeps the version it pinned until its driver switches.
 *
 * Saving names the version this editor opened (`expected_version`): when
 * someone saved since, the server says so and the editor offers theirs.
 */
import React, { useMemo, useState } from 'react';
import axios from 'axios';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus } from 'lucide-react';

import {
  createPresentationAgenda, deletePresentationAgenda, savePresentationAgenda,
  type PresentationAgenda, type Slide,
} from '../../services/api';
import { useOpeningKey } from '../../hooks/useOpeningKey';
import { invalidateReads } from '../../lib/query';
import { formatApiError } from '../../utils/apiErrors';
import { anchorOptions, itemLabel } from '../../utils/presentationAgenda';
import { selectListAtFieldWidth, selectOptionTruncates } from '../../utils/uiStyles';
import ReorderableRows from '../reports/ReorderableRows';
import { Button } from '../ui/button';
import {
  Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '../ui/dialog';
import { Input } from '../ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: number;
  reportId: number;
  /** The unfiltered slide list: every anchor of the report. */
  slides: Slide[];
  agendas: PresentationAgenda[];
  /** Open on this agenda (the one running), else the first, else a new one. */
  initialId: number | null;
}

const NEW = 'new';

const AgendaEditorDialog: React.FC<Props> = (props) => (
  <Dialog open={props.open} onOpenChange={props.onOpenChange}>
    <DialogContent size="lg">
      <Body key={useOpeningKey(props.open)} {...props} />
    </DialogContent>
  </Dialog>
);

interface Draft { id: number | null; version: number | null; name: string; items: string[] }

const draftOf = (a: PresentationAgenda | undefined): Draft =>
  a ? { id: a.id, version: a.version, name: a.name, items: [...a.items] } : { id: null, version: null, name: '', items: [] };

const Body: React.FC<Props> = ({ onOpenChange, projectId, reportId, slides, agendas, initialId }) => {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Draft>(() => draftOf(agendas.find((a) => a.id === initialId) ?? agendas[0]));
  const [adding, setAdding] = useState('');
  const [conflict, setConflict] = useState<PresentationAgenda | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const options = useMemo(() => anchorOptions(slides), [slides]);
  const rows = draft.items.map((anchor) => ({ name: itemLabel(slides, anchor), anchor }));

  const save = useMutation({
    mutationFn: (d: Draft) => (d.id == null
      ? createPresentationAgenda(projectId, reportId, { name: d.name.trim(), items: d.items })
      : savePresentationAgenda(projectId, reportId, d.id, { name: d.name.trim(), items: d.items, expected_version: d.version ?? 1 })),
    onSuccess: (saved) => {
      setDraft(draftOf(saved));
      setConflict(null);
      void invalidateReads(queryClient, 'listPresentationAgendas');
    },
    onError: (error) => {
      if (axios.isAxiosError(error) && error.response?.status === 409) {
        const agenda = (error.response.data as { detail?: { agenda?: PresentationAgenda } } | undefined)?.detail?.agenda;
        if (agenda) setConflict(agenda);
      }
    },
  });
  const remove = useMutation({
    mutationFn: (id: number) => deletePresentationAgenda(projectId, reportId, id),
    onSuccess: (_, id) => {
      setConfirmDelete(false);
      setDraft(draftOf(agendas.find((a) => a.id !== id)));
      void invalidateReads(queryClient, 'listPresentationAgendas');
    },
  });

  const pick = (value: string) => {
    setConflict(null);
    setConfirmDelete(false);
    save.reset();
    remove.reset();
    setDraft(value === NEW ? draftOf(undefined) : draftOf(agendas.find((a) => String(a.id) === value)));
  };
  const canSave = draft.name.trim().length > 0 && draft.items.length > 0 && !save.isPending;
  const current = agendas.find((a) => a.id === draft.id);

  return (
    <>
      <DialogHeader>
        <DialogTitle>Agendas</DialogTitle>
        <DialogDescription>
          An agenda walks the report in the order you choose: the whole of a finding, or one of its sections.
          It belongs to this report and can be used again. Saving makes a new version; a running presentation
          keeps its version until the driver switches.
        </DialogDescription>
      </DialogHeader>
      <DialogBody className="space-y-sm">
        <div className="flex flex-wrap items-center gap-sm">
          <Select value={draft.id == null ? NEW : String(draft.id)} onValueChange={pick}>
            <SelectTrigger className="h-8 w-64" aria-label="Agenda"><SelectValue /></SelectTrigger>
            <SelectContent className={selectListAtFieldWidth}>
              {agendas.map((a) => (
                <SelectItem key={a.id} value={String(a.id)} className={selectOptionTruncates} title={a.name}>{a.name}</SelectItem>
              ))}
              <SelectItem value={NEW}>A new agenda</SelectItem>
            </SelectContent>
          </Select>
          {draft.version != null && <span className="text-caption text-muted-foreground">Version {draft.version}</span>}
        </div>
        <Input value={draft.name} maxLength={200} aria-label="Agenda name" placeholder="Name, e.g. Network team walkthrough"
          onChange={(e) => setDraft({ ...draft, name: e.target.value })} />

        {rows.length === 0 && <p className="text-caption text-muted-foreground">No items yet — add them below.</p>}
        <ReorderableRows items={rows} noun="item" editable disabled={save.isPending}
          onChange={(next) => setDraft({ ...draft, items: next.map((r) => r.anchor) })}>
          {(row, i) => (
            <span className="min-w-0 flex-1 truncate text-metadata" data-testid={`agenda-item-${i}`}>
              {i + 1}. {row.name}
            </span>
          )}
        </ReorderableRows>

        <div className="flex flex-wrap items-center gap-xs">
          <Select value={adding} onValueChange={setAdding}>
            <SelectTrigger className="h-8 min-w-0 flex-1" aria-label="Item to add"><SelectValue placeholder="Choose what to add…" /></SelectTrigger>
            <SelectContent className={selectListAtFieldWidth}>
              {options.map((o) => (
                <SelectItem key={o.anchor} value={o.anchor} className={selectOptionTruncates} title={o.label}>
                  <span className={o.nested ? 'pl-md text-muted-foreground' : ''}>{o.label}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button type="button" variant="outline" size="sm" disabled={!adding}
            onClick={() => { setDraft({ ...draft, items: [...draft.items, adding] }); setAdding(''); }}>
            <Plus className="size-4" aria-hidden /> Add
          </Button>
        </div>

        {conflict && (
          <div role="status" className="border-l-4 border-warning bg-warning/10 px-sm py-xs text-metadata">
            Someone saved this agenda since you opened it (version {conflict.version}).{' '}
            <Button variant="link" size="sm" className="h-auto p-0" onClick={() => { setDraft(draftOf(conflict)); setConflict(null); }}>
              Load their version
            </Button>
          </div>
        )}
        {save.isError && !conflict && <p className="text-destructive">{formatApiError(save.error, 'Could not save the agenda.')}</p>}
        {remove.isError && <p className="text-destructive">{formatApiError(remove.error, 'Could not delete the agenda.')}</p>}
        {save.isSuccess && <p className="text-caption text-success" role="status">Saved — version {save.data.version}.</p>}
      </DialogBody>
      <DialogFooter>
        {current && (
          <Button variant="ghost" className="mr-auto text-destructive" disabled={remove.isPending || current.in_live_presentation}
            title={current.in_live_presentation ? 'A running presentation follows this agenda.' : undefined}
            onClick={() => (confirmDelete ? remove.mutate(current.id) : setConfirmDelete(true))}>
            {confirmDelete ? `Delete “${current.name}” for good` : 'Delete this agenda'}
          </Button>
        )}
        <Button variant="outline" onClick={() => onOpenChange(false)}>Close</Button>
        <Button onClick={() => save.mutate(draft)} disabled={!canSave} data-testid="save-agenda">
          {draft.id == null ? 'Save the agenda' : 'Save a new version'}
        </Button>
      </DialogFooter>
    </>
  );
};

export default AgendaEditorDialog;
