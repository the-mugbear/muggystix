/**
 * The agenda on the Controller (5.425.0, plan E3): which agenda the
 * presentation follows (or the full report), its items with the active one
 * marked, exploring and "Back to the agenda", and "A newer version of this
 * agenda exists".  Everyone sees it; only the driver changes it — every change
 * is a move (`go`), so the agenda state stays part of the shared position.
 */
import React from 'react';

import type { PresentationAgenda, PresentationState, Slide } from '../../services/api';
import type { Target } from '../../utils/presentationSync';
import {
  backToAgenda, itemLabel, leaveAgenda, pickItem, switchAgenda, type PinnedAgenda,
} from '../../utils/presentationAgenda';
import { Button } from '../ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';

const FULL = 'full';

interface Props {
  state: PresentationState;
  /** What this tab shows (the driver's target while one is pending). */
  position: Target;
  /** The agenda `position` names, with its items. */
  pinned: PinnedAgenda | null;
  slides: Slide[];
  agendas: PresentationAgenda[];
  /** The driver's move; absent for everyone else. */
  onGo?: (target: Target) => void;
  onEdit: () => void;
}

const AgendaPanel: React.FC<Props> = ({ state, position, pinned, slides, agendas, onGo, onEdit }) => {
  const a = position.agenda;
  const running = state.agenda;
  const newer = Boolean(running && running.latest_version > running.version);
  // Its items come from the agenda list (re-read when the page sees a newer version).
  const latest = running && newer
    ? agendas.find((x) => x.id === running.id && x.version === running.latest_version) : undefined;

  const choose = (value: string) => {
    if (!onGo) return;
    if (value === FULL) { onGo(leaveAgenda(position)); return; }
    const agenda = agendas.find((x) => String(x.id) === value);
    if (!agenda) return;
    const next = pickItem(slides, position, { id: agenda.id, version: agenda.version, items: agenda.items }, 0);
    if (next) onGo(next);
  };

  return (
    <section aria-label="Agenda" className="space-y-xs border-t border-border pt-xs" data-testid="agenda-panel">
      <div className="flex flex-wrap items-center gap-xs">
        <span className="text-metadata font-semibold">Agenda</span>
        {onGo ? (
          <Select value={a ? String(a.agenda_id) : FULL} onValueChange={choose}>
            <SelectTrigger className="h-8 min-w-0 flex-1" aria-label="Follow"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={FULL}>The full report</SelectItem>
              {agendas.map((x) => <SelectItem key={x.id} value={String(x.id)}>{x.name}</SelectItem>)}
            </SelectContent>
          </Select>
        ) : (
          <span className="text-metadata text-muted-foreground" data-testid="agenda-followed">
            {running ? `${running.name} (version ${running.version})` : 'The full report'}
          </span>
        )}
        <Button variant="ghost" size="sm" onClick={onEdit} data-testid="edit-agendas">Edit agendas</Button>
      </div>

      {running && newer && (
        <p className="text-caption text-warning" data-testid="agenda-newer">
          A newer version of this agenda exists (version {running.latest_version}).
          {onGo && latest && (
            <Button variant="link" size="sm" className="ml-xs h-auto p-0" onClick={() => {
              const next = switchAgenda(slides, position, { id: latest.id, version: latest.version, items: latest.items });
              if (next) onGo(next);
            }}>
              Use it
            </Button>
          )}
        </p>
      )}

      {a && pinned && (
        <>
          {a.mode === 'explore' && (
            <p className="text-caption text-muted-foreground" data-testid="agenda-exploring">
              Exploring the full report — item {a.item_index + 1} waits.
              {onGo && (
                <Button variant="link" size="sm" className="ml-xs h-auto p-0" onClick={() => {
                  const next = backToAgenda(slides, position, pinned);
                  if (next) onGo(next);
                }}>
                  Back to the agenda
                </Button>
              )}
            </p>
          )}
          <ol className="space-y-px text-metadata">
            {pinned.items.map((anchor, i) => {
              const here = i === a.item_index;
              const label = `${i + 1}. ${itemLabel(slides, anchor)}`;
              return (
                <li key={`${i}-${anchor}`}>
                  {onGo ? (
                    <button type="button" data-agenda-item={i} aria-current={here ? 'true' : undefined}
                      onClick={() => { const next = pickItem(slides, position, pinned, i); if (next) onGo(next); }}
                      className={`w-full truncate rounded px-xs py-px text-left ${here ? (a.mode === 'agenda' ? 'bg-accent font-semibold' : 'font-semibold') : 'hover:bg-accent/50'}`}>
                      {label}
                    </button>
                  ) : (
                    <span className={`block truncate px-xs py-px ${here ? 'font-semibold' : ''}`} aria-current={here ? 'true' : undefined}>{label}</span>
                  )}
                </li>
              );
            })}
          </ol>
        </>
      )}
    </section>
  );
};

export default AgendaPanel;
