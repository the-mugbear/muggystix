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
 * - **Before a target is sent** the tab rechecks the highest accepted state:
 *   still driving, not ended — else the target is dropped.
 */
import type { HostFilter, PresentationPerson, PresentationState, RenderedAck } from '../services/api';

export interface Target { slide_key: string; host_filter: HostFilter | null }

/** A 409 from a move: the server's current state rides with it. */
export class MoveConflict extends Error {
  constructor(public readonly state: PresentationState) {
    super('The presentation refused the move.');
  }
}

export interface SyncTransport {
  /** Send a move at `revision`; resolves with the new state, rejects with
   *  `MoveConflict` (a 409) or any other error (an unknown outcome). */
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
}

const filterEq = (a: HostFilter | null, b: HostFilter | null) =>
  (a?.kind ?? null) === (b?.kind ?? null) && (a?.value ?? null) === (b?.value ?? null);

export const samePosition = (t: Target, s: { slide_key: string; host_filter: HostFilter | null }) =>
  t.slide_key === s.slide_key && filterEq(t.host_filter, s.host_filter);

export class PresentationSync {
  private accepted: PresentationState | null = null;
  private target: Target | null = null;
  private inFlight = false;
  private reconciling = false;
  private lastAnswerAt = 0;
  private lostControlTo: PresentationPerson | null = null;
  private listeners = new Set<() => void>();
  private snapshot: SyncView;
  /** A Display's last finished render, sent with every heartbeat. */
  private rendered: RenderedAck | null = null;

  /** Record a finished render; true when it is news (a new revision or fit). */
  setRendered(ack: RenderedAck | null): boolean {
    const before = this.rendered;
    this.rendered = ack;
    return Boolean(ack && (!before || before.revision !== ack.revision || before.fitted !== ack.fitted));
  }

  renderedAck(): RenderedAck | null {
    return this.rendered;
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
      : s ? { slide_key: s.slide_key, host_filter: s.host_filter } : null;
    return {
      state: s, position, pending: this.target, inFlight: this.inFlight, reconciling: this.reconciling,
      lastAnswerAt: this.lastAnswerAt, lostControlTo: this.lostControlTo,
    };
  }

  private emit(): void {
    this.snapshot = this.build();
    this.listeners.forEach((fn) => fn());
  }

  /** The answer rule. */
  private accept(answer: PresentationState): void {
    const prev = this.accepted;
    if (prev && answer.id === prev.id && answer.revision < prev.revision) return;
    this.lastAnswerAt = this.now();
    if (prev && answer.id === prev.id && answer.revision === prev.revision) {
      this.accepted = { ...prev, watching: answer.watching, displays: answer.displays };
      return;
    }
    if (prev?.you_are_driving && !answer.you_are_driving && !answer.ended) {
      this.lostControlTo = answer.driver;
    }
    if (answer.you_are_driving) this.lostControlTo = null;
    this.accepted = answer;
    if (!answer.you_are_driving || answer.ended) this.target = null;
    if (this.target && samePosition(this.target, answer)) this.target = null;
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
    this.target = samePosition(target, s) && !this.inFlight ? null : target;
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
    if (samePosition(this.target, s)) {
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
        // The move may have committed: nothing more is sent until a read says.
        this.reconciling = true;
        this.emit();
        this.transport.requestRead();
      },
    );
  }
}
