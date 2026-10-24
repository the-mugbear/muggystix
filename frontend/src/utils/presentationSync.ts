/**
 * Presentation mode's client state machine (5.424.0) — what a tab does with the
 * presentation's answers and with its own moves.  Plain TypeScript with an
 * injected transport, so it is tested as three clients over one in-memory
 * server (`tests/utils/presentationSync.test.ts`); `hooks/usePresentationSync`
 * wires it to the API.
 *
 * The rules (planning: Moves, handoffs, ending):
 *
 * - **One answer rule, for a poll and a move's response alike.**  The tab keeps
 *   the highest-revision state it has accepted.  A LOWER revision is ignored
 *   whole; an EQUAL one changes nothing on screen but refreshes the watching
 *   list and connection health; a HIGHER one is accepted.  An accepted state
 *   saying this tab no longer drives (someone took control) or that the
 *   presentation ended discards the pending target and the optimistic
 *   position.  A target the accepted state already shows is cleared.
 * - **Moves are serialized and coalesced.**  One move in flight; presses
 *   while it is out only replace the TARGET (the latest slide wanted), so a
 *   held key sends at most one move per round trip and never conflicts with
 *   itself.  The driver's own screen shows the target at once.
 * - **Finishing a request is bookkeeping.**  The in-flight slot is freed on
 *   ANY outcome — an answer of any revision, a 409, a network error, a
 *   timeout.  Whether its state is accepted is the answer rule's business.
 * - **Awaiting reconciliation.**  A move whose outcome is unknown (a network
 *   error or a timeout — it may have committed) puts the tab in
 *   `reconciling`: no target is sent until a successful READ has been
 *   accepted.  Then a target the read shows is cleared; otherwise, if the tab
 *   still drives, the latest target is sent with that read's revision.  A
 *   409 is a definite outcome: its body is the current state.
 * - **A refused move is a definite outcome too.**  Any answer other than a
 *   409 that the server gave (a 422 for a slide not in the report, a 403…)
 *   drops the target and says why (`refused`); it is never retried.  Only a
 *   move with NO answer (a network error, a timeout) is of unknown outcome —
 *   and its target is sent again at most once after reconciling, so a server
 *   that keeps failing cannot make the tab send forever (code review
 *   2026-10-24: a 422 read as unknown resent itself 179 times in 3.5 s).
 * - **Before a target is sent** the tab rechecks the highest accepted state:
 *   still driving, not ended — else the target is dropped.
 * - **The agenda state is part of the position** (5.425.0): a target names it
 *   too, so choosing an agenda, exploring and coming back are moves like any.
 * - **The spotlight has its own ordering** (5.425.0): every answer's
 *   `spotlight` is taken when its `seq` is higher than any seen — whatever
 *   the answer's revision, so an equal-revision answer still brings it — and
 *   its time left is counted from when the answer landed.  Which revision it
 *   must match to be SHOWN is the tab's (`spotlightShown`): a Controller's
 *   accepted one, a Display's RENDERED one.
 */
import type {
  AgendaPosition, HostFilter, PresentationPerson, PresentationState, RenderedAck,
} from '../services/api';

export interface Target { slide_key: string; host_filter: HostFilter | null; agenda: AgendaPosition | null }

/** A spotlight as this tab holds it: `expiresAt` on this tab's clock. */
export interface HeldSpotlight { seq: number; revision: number; x: number; y: number; expiresAt: number }

/** Whether a held spotlight rings now, on a tab showing `revision`. */
export const spotlightShown = (s: HeldSpotlight | null, revision: number | undefined, now: number): boolean =>
  Boolean(s && revision != null && s.revision === revision && now < s.expiresAt);

/** A move the server refused for good (not a 409): its message is said. */
export class MoveRefused extends Error {}

/** A 409 from a move: the server's current state rides with it. */
export class MoveConflict extends Error {
  constructor(public readonly state: PresentationState) {
    super('The presentation refused the move.');
  }
}

export interface SyncTransport {
  /** Send a move at `revision`; resolves with the new state, rejects with
   *  `MoveConflict` (a 409), `MoveRefused` (any other answer the server gave)
   *  or any other error (no answer: an unknown outcome). */
  move(revision: number, target: Target): Promise<PresentationState>;
  /** Ask for a read of the presentation now (after an unknown outcome). */
  requestRead(): void;
  now?(): number;
}

export interface SyncView {
  /** The highest-revision state accepted, or null before the first. */
  state: PresentationState | null;
  /** What this tab shows: its pending target while driving, else the state's position. */
  position: Target | null;
  pending: Target | null;
  inFlight: boolean;
  reconciling: boolean;
  /** When an answer last landed (connection health). */
  lastAnswerAt: number;
  /** Who took control from this tab, until it drives again. */
  lostControlTo: PresentationPerson | null;
  /** Why the last move was refused, until the next one is asked for. */
  refused: string | null;
  /** The highest-seq spotlight seen (shown only by `spotlightShown`). */
  spotlight: HeldSpotlight | null;
  /** A Display's last finished render's revision. */
  renderedRevision: number | undefined;
}

const filterEq = (a: HostFilter | null, b: HostFilter | null) =>
  (a?.kind ?? null) === (b?.kind ?? null) && (a?.value ?? null) === (b?.value ?? null);

const agendaEq = (a: AgendaPosition | null, b: AgendaPosition | null) =>
  (a == null && b == null) || (a != null && b != null && a.agenda_id === b.agenda_id && a.version === b.version
    && a.mode === b.mode && a.item_index === b.item_index);

/** A state's agenda as a position's part. */
export const agendaOf = (s: Pick<PresentationState, 'agenda'>): AgendaPosition | null =>
  s.agenda ? { agenda_id: s.agenda.id, version: s.agenda.version, mode: s.agenda.mode, item_index: s.agenda.item_index } : null;

/** The position a state names. */
export const positionOf = (s: PresentationState): Target =>
  ({ slide_key: s.slide_key, host_filter: s.host_filter, agenda: agendaOf(s) });

export const samePosition = (t: Target, s: Target) =>
  t.slide_key === s.slide_key && filterEq(t.host_filter, s.host_filter) && agendaEq(t.agenda, s.agenda);

export class PresentationSync {
  private accepted: PresentationState | null = null;
  private target: Target | null = null;
  private inFlight = false;
  private reconciling = false;
  private lastAnswerAt = 0;
  private lostControlTo: PresentationPerson | null = null;
  private refused: string | null = null;
  /** The target last resent after an unknown outcome: never resent twice. */
  private resentOnce: Target | null = null;
  private listeners = new Set<() => void>();
  private snapshot: SyncView;
  /** A Display's last finished render, sent with every heartbeat. */
  private rendered: RenderedAck | null = null;
  /** The presentation that render belongs to: a revision means nothing in another. */
  private renderedFor: number | null = null;
  private spotlight: HeldSpotlight | null = null;
  /** The highest spotlight seq seen, per presentation. */
  private spotlightSeq = 0;

  /** Record a finished render; true when it is news (a new revision or fit). */
  setRendered(ack: RenderedAck | null): boolean {
    const before = this.rendered;
    this.rendered = ack;
    this.renderedFor = ack ? this.accepted?.id ?? null : null;
    const news = Boolean(ack && (!before || before.revision !== ack.revision || before.fitted !== ack.fitted));
    if (before?.revision !== ack?.revision) this.emit();
    return news;
  }

  /**
   * The render to acknowledge to `presentationId` — never one of an earlier
   * presentation: a Display left open through End and the next Start sent the
   * ended one's revision, which the new one refused with a 422 every second
   * (Chrome pass 2026-10-24).
   */
  renderedAck(presentationId: number): RenderedAck | null {
    return this.renderedFor === presentationId ? this.rendered : null;
  }

  constructor(private readonly transport: SyncTransport) {
    this.snapshot = this.build();
  }

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => { this.listeners.delete(fn); };
  };

  getView = (): SyncView => this.snapshot;

  private now(): number {
    return this.transport.now ? this.transport.now() : Date.now();
  }

  private build(): SyncView {
    const s = this.accepted;
    const position = this.target && s?.you_are_driving && !s.ended
      ? this.target
      : s ? positionOf(s) : null;
    return {
      state: s, position, pending: this.target, inFlight: this.inFlight, reconciling: this.reconciling,
      lastAnswerAt: this.lastAnswerAt, lostControlTo: this.lostControlTo, refused: this.refused,
      spotlight: this.spotlight, renderedRevision: this.rendered?.revision,
    };
  }

  private emit(): void {
    this.snapshot = this.build();
    this.listeners.forEach((fn) => fn());
  }

  /** The spotlight's own rule, before (and apart from) the revision rule. */
  private takeSpotlight(answer: PresentationState): void {
    if (this.accepted && answer.id !== this.accepted.id) {
      this.spotlightSeq = 0;
      this.spotlight = null;
    }
    const s = answer.spotlight;
    if (!s || s.seq <= this.spotlightSeq) return;
    this.spotlightSeq = s.seq;
    this.spotlight = { seq: s.seq, revision: s.revision, x: s.x, y: s.y, expiresAt: this.now() + s.remaining_ms };
  }

  /** The answer rule. */
  private accept(answer: PresentationState): void {
    this.takeSpotlight(answer);
    const prev = this.accepted;
    if (prev && answer.id !== prev.id) {
      this.rendered = null;
      this.renderedFor = null;
    }
    if (prev && answer.id === prev.id && answer.revision < prev.revision) return;
    this.lastAnswerAt = this.now();
    if (prev && answer.id === prev.id && answer.revision === prev.revision) {
      // The position is the same at an equal revision; what changes without a
      // move is presence — and the running agenda's newest version (someone
      // saved it), which "A newer version exists" reads (Chrome pass 2026-10-24:
      // it was dropped until the next move).
      this.accepted = { ...prev, watching: answer.watching, displays: answer.displays, agenda: answer.agenda };
      return;
    }
    if (prev?.you_are_driving && !answer.you_are_driving && !answer.ended) {
      this.lostControlTo = answer.driver;
    }
    if (answer.you_are_driving || answer.ended) this.lostControlTo = null;
    this.accepted = answer;
    if (!answer.you_are_driving || answer.ended) this.target = null;
    if (this.target && samePosition(this.target, positionOf(answer))) this.target = null;
  }

  /** A successful read of the presentation (a poll). */
  read(answer: PresentationState): void {
    this.accept(answer);
    this.reconciling = false;
    this.emit();
    this.flush();
  }

  /** The driver wants `target` shown.  False when this tab does not drive. */
  go(target: Target): boolean {
    const s = this.accepted;
    if (!s || !s.you_are_driving || s.ended) return false;
    this.target = samePosition(target, positionOf(s)) && !this.inFlight ? null : target;
    this.refused = null;
    this.resentOnce = null;
    this.emit();
    this.flush();
    return true;
  }

  dismissLostControl(): void {
    this.lostControlTo = null;
    this.emit();
  }

  private flush(): void {
    if (this.inFlight || this.reconciling || !this.target) return;
    const s = this.accepted;
    if (!s || !s.you_are_driving || s.ended) {
      this.target = null;
      this.emit();
      return;
    }
    if (samePosition(this.target, positionOf(s))) {
      this.target = null;
      this.emit();
      return;
    }
    const sent = this.target;
    this.inFlight = true;
    this.emit();
    this.transport.move(s.revision, sent).then(
      (answer) => {
        this.inFlight = false;
        // A move the server took settles what it sent: its answer is the row
        // after it.  (Read only by the answer's position, a target the answer
        // shows differently — an echo missing a part — would be sent forever.)
        if (this.target && samePosition(this.target, sent)) this.target = null;
        this.accept(answer);
        this.emit();
        this.flush();
      },
      (error: unknown) => {
        this.inFlight = false;
        if (error instanceof MoveConflict) {
          this.accept(error.state);
          this.emit();
          this.flush();
          return;
        }
        if (error instanceof MoveRefused) {
          // Definite: the server will refuse this target again.  Drop it, say why.
          if (this.target && samePosition(this.target, sent)) this.target = null;
          this.refused = error.message;
          this.emit();
          return;
        }
        // No answer: the move may have committed, so nothing more is sent until a
        // read says — and a target already resent once after this is dropped.
        if (this.target && this.resentOnce && samePosition(this.target, this.resentOnce)) {
          this.target = null;
          this.refused = 'The move could not be sent; the presentation shows where it is.';
        } else if (this.target && samePosition(this.target, sent)) {
          this.resentOnce = sent;
        }
        this.reconciling = true;
        this.emit();
        this.transport.requestRead();
      },
    );
  }
}
