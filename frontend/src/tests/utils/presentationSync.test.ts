/**
 * Presentation mode's client state machine (`utils/presentationSync`), as
 * three tabs over one in-memory server that applies the server's rules
 * (a move needs the driver AND the driving tab, at the revision it saw, while
 * live; Take control always wins; only those and End raise the revision).
 * The network is a queue the test answers in any order, so delayed, crossed
 * and failed answers are exercised deliberately.
 *
 * A = the Display (follows), B = the driver, C = browses, then takes control;
 * B2 = a second Controller of B's account, opened from B (its own tab id).
 */
import { describe, it, expect } from 'vitest';

import type { HostFilter, PresentationSpotlight, PresentationState } from '../../services/api';
import {
  MoveConflict, MoveRefused, PresentationSync, spotlightShown, type Target,
} from '../../utils/presentationSync';

const PEOPLE = {
  1: { id: 1, name: 'Ben', profile_image: null },
  2: { id: 2, name: 'Cara', profile_image: null },
  3: { id: 3, name: 'Avery', profile_image: null },
} as const;

class Server {
  revision = 1;
  slide = 'overview:1';
  filter: HostFilter | null = null;
  driverUser = 1;
  driverClient = 'B';
  ended = false;
  spotSeq = 0;
  spot: PresentationSpotlight | null = null;
  agenda: Target['agenda'] = null;

  stateFor(user: number, client: string): PresentationState {
    return {
      id: 7, report_id: 3, revision: this.revision, slide_key: this.slide, host_filter: this.filter,
      driver: PEOPLE[this.driverUser as 1 | 2 | 3], started_by: PEOPLE[1], started_at: null,
      you_are_driving: !this.ended && this.driverUser === user && this.driverClient === client,
      driving_elsewhere: !this.ended && this.driverUser === user && this.driverClient !== client,
      ended: this.ended ? { at: 'now', reason: 'ended', by: PEOPLE[1] } : null,
      watching: [], displays: [],
      agenda: this.agenda ? {
        id: this.agenda.agenda_id, name: 'Walkthrough', version: this.agenda.version, latest_version: this.agenda.version,
        mode: this.agenda.mode, item_index: this.agenda.item_index, items: ['overview'],
      } : null,
      // As the server answers it: only while it is for the current revision.
      spotlight: this.spot && this.spot.revision === this.revision ? { ...this.spot } : null,
    };
  }

  /** The driver's click, at the current revision; never raises it. */
  spotlight(x: number, y: number) {
    this.spotSeq += 1;
    this.spot = { seq: this.spotSeq, revision: this.revision, x, y, remaining_ms: 5000 };
  }

  move(user: number, client: string, revision: number, target: Target): PresentationState {
    if (this.ended || revision !== this.revision || this.driverUser !== user || this.driverClient !== client) {
      throw new MoveConflict(this.stateFor(user, client));
    }
    this.slide = target.slide_key;
    this.filter = target.host_filter;
    this.agenda = target.agenda;
    this.revision += 1;
    return this.stateFor(user, client);
  }

  take(user: number, client: string) {
    this.driverUser = user;
    this.driverClient = client;
    this.revision += 1;
  }

  end() {
    this.ended = true;
    this.revision += 1;
  }
}

/** Requests wait here until the test answers them. */
class Network {
  pending: Array<{ label: string; resolve: () => void; fail: (e: unknown) => void }> = [];

  send<T>(label: string, run: () => T): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      this.pending.push({
        label,
        resolve: () => { try { resolve(run()); } catch (e) { reject(e); } },
        fail: (e) => reject(e),
      });
    });
  }

  labels() { return this.pending.map((p) => p.label); }

  async answer(index = 0) {
    const [p] = this.pending.splice(index, 1);
    p.resolve();
    await flush();
  }

  async fail(index = 0, error: unknown = new Error('network')) {
    const [p] = this.pending.splice(index, 1);
    p.fail(error);
    await flush();
  }
}

const flush = async () => { for (let i = 0; i < 5; i += 1) await Promise.resolve(); };

const tab = (server: Server, net: Network, user: number, client: string, clock?: { t: number }) => {
  let reads = 0;
  const sync = new PresentationSync({
    // The server state is decided when the request ARRIVES (answered), as on a real server.
    move: (revision, target) => net.send(`move ${client} ${target.slide_key}@${revision}`,
      () => server.move(user, client, revision, target)),
    requestRead: () => { reads += 1; },
    now: clock ? () => clock.t : undefined,
  });
  return {
    sync,
    read: () => sync.read(server.stateFor(user, client)),
    /** A read taken NOW, delivered later (a delayed poll). */
    snapshot: () => server.stateFor(user, client),
    reads: () => reads,
  };
};

const to = (slide_key: string, host_filter: HostFilter | null = null): Target => ({ slide_key, host_filter, agenda: null });

describe('presentation sync — three tabs over one server', () => {
  it('the Display follows B, then C after C takes control', async () => {
    const server = new Server();
    const net = new Network();
    const A = tab(server, net, 3, 'A');
    const B = tab(server, net, 1, 'B');
    const C = tab(server, net, 2, 'C');
    [A, B, C].forEach((t) => t.read());
    expect(B.sync.go(to('summary:1'))).toBe(true);
    expect(B.sync.getView().position?.slide_key).toBe('summary:1');     // optimistic
    await net.answer();
    A.read();
    expect(A.sync.getView().position?.slide_key).toBe('summary:1');
    expect(C.sync.go(to('scope:1'))).toBe(false);                       // C does not drive
    server.take(2, 'C');
    C.read();
    expect(C.sync.getView().state?.you_are_driving).toBe(true);
    C.sync.go(to('f:F-01'));
    await net.answer();
    A.read();
    expect(A.sync.getView().position?.slide_key).toBe('f:F-01');
    B.read();
    expect(B.sync.getView().lostControlTo?.name).toBe('Cara');
    expect(B.sync.getView().position?.slide_key).toBe('f:F-01');
  });

  it('a second Controller of the driver’s account is not the driver', () => {
    const server = new Server();
    const B2 = tab(server, new Network(), 1, 'B2');
    B2.read();
    expect(B2.sync.getView().state?.you_are_driving).toBe(false);
    expect(B2.sync.getView().state?.driving_elsewhere).toBe(true);
    expect(B2.sync.go(to('summary:1'))).toBe(false);
  });

  it('a delayed older answer arriving after a newer one is dropped', async () => {
    const server = new Server();
    const net = new Network();
    const A = tab(server, net, 3, 'A');
    const B = tab(server, net, 1, 'B');
    A.read(); B.read();
    const old = A.snapshot();                                           // revision 1, overview
    B.sync.go(to('summary:1'));
    await net.answer();
    A.read();                                                           // revision 2
    A.sync.read(old);                                                   // the delayed poll lands
    expect(A.sync.getView().state?.revision).toBe(2);
    expect(A.sync.getView().position?.slide_key).toBe('summary:1');
  });

  it('ten presses send one move per round trip and never conflict', async () => {
    const server = new Server();
    const net = new Network();
    const B = tab(server, net, 1, 'B');
    B.read();
    const keys = Array.from({ length: 10 }, (_, i) => `f:F-01:hosts:${i + 1}`);
    keys.forEach((k) => B.sync.go(to(k)));
    expect(net.labels()).toEqual(['move B f:F-01:hosts:1@1']);
    expect(B.sync.getView().position?.slide_key).toBe('f:F-01:hosts:10');
    await net.answer();
    expect(net.labels()).toEqual(['move B f:F-01:hosts:10@2']);       // the latest target, at the new revision
    await net.answer();
    expect(net.labels()).toEqual([]);
    expect(server.slide).toBe('f:F-01:hosts:10');
    expect(server.revision).toBe(3);
  });

  it('a takeover read before the in-flight move answers drops the queue, and the late answer moves nothing', async () => {
    const server = new Server();
    const net = new Network();
    const A = tab(server, net, 3, 'A');
    const B = tab(server, net, 1, 'B');
    A.read(); B.read();
    B.sync.go(to('summary:1'));
    B.sync.go(to('scope:1'));                                           // queued
    B.sync.go(to('f:F-01'));                                            // queued (latest)
    // C takes control and B's poll brings it BEFORE B's move answers.
    server.take(2, 'C');
    B.read();
    expect(B.sync.getView().pending).toBeNull();
    // B's move now reaches the server: refused (not the driver), and its answer changes nothing.
    await net.answer();
    expect(net.labels()).toEqual([]);                                   // nothing more sent
    expect(server.slide).toBe('overview:1');
    expect(B.sync.getView().position?.slide_key).toBe('overview:1');
    A.read();
    expect(A.sync.getView().position?.slide_key).toBe('overview:1');
  });

  it('a move whose answer arrives after a poll of the same revision frees the slot and the queue is sent', async () => {
    const server = new Server();
    const net = new Network();
    const B = tab(server, net, 1, 'B');
    for (let i = 0; i < 9; i += 1) server.revision += 1;                // revision 10
    B.read();
    B.sync.go(to('summary:1'));
    // The move commits revision 11 on the server, but its answer is held…
    const held = net.pending.shift()!;
    const delivered = new Promise<void>((resolve) => {
      net.pending.push({ label: 'held', resolve: () => { held.resolve(); resolve(); }, fail: held.fail });
    });
    B.sync.go(to('scope:1'));                                           // queued meanwhile
    // …so commit it now, poll first (revision 11), then deliver the answer.
    const committed = server.move(1, 'B', 10, to('summary:1'));
    expect(committed.revision).toBe(11);
    B.read();
    expect(B.sync.getView().state?.revision).toBe(11);
    // The held answer is the same revision (the duplicate move is refused as stale: its body is revision 11).
    await net.answer(0);
    await delivered;
    await flush();
    expect(net.labels()).toEqual(['move B scope:1@11']);
    await net.answer();
    expect(server.slide).toBe('scope:1');
  });

  it('after an unknown outcome nothing is sent until a read; then the target is cleared or sent once', async () => {
    const server = new Server();
    const net = new Network();
    const B = tab(server, net, 1, 'B');
    B.read();
    B.sync.go(to('summary:1'));
    // The request times out — but it DID reach the server.
    server.move(1, 'B', 1, to('summary:1'));
    await net.fail(0, new Error('timeout'));
    expect(B.sync.getView().reconciling).toBe(true);
    expect(B.reads()).toBe(1);
    B.sync.go(to('scope:1'));                                           // pressed while reconciling
    expect(net.labels()).toEqual([]);                                   // not sent blind
    B.read();                                                           // the read: revision 2, summary
    expect(B.sync.getView().reconciling).toBe(false);
    expect(net.labels()).toEqual(['move B scope:1@2']);                 // sent once, with the read's revision
    await net.answer();
    expect(server.slide).toBe('scope:1');

    // A move that failed and did NOT commit, whose target the read does not show: sent once after the read.
    B.sync.go(to('f:F-01'));
    await net.fail(0, new Error('network'));
    expect(net.labels()).toEqual([]);
    B.read();
    expect(net.labels()).toEqual(['move B f:F-01@3']);
    await net.answer();

    // A failure during which control was lost: the read drops the target.
    B.sync.go(to('f:F-02'));
    await net.fail(0, new Error('network'));
    server.take(2, 'C');
    B.read();
    expect(net.labels()).toEqual([]);
    expect(B.sync.getView().pending).toBeNull();
  });

  it('a target the reconciling read already shows is cleared, not resent', async () => {
    const server = new Server();
    const net = new Network();
    const B = tab(server, net, 1, 'B');
    B.read();
    B.sync.go(to('summary:1'));
    server.move(1, 'B', 1, to('summary:1'));
    await net.fail(0, new Error('timeout'));
    B.read();
    expect(net.labels()).toEqual([]);
    expect(B.sync.getView().pending).toBeNull();
  });

  it('an equal revision refreshes the watching list and nothing else', () => {
    const server = new Server();
    const A = tab(server, new Network(), 3, 'A');
    A.read();
    const same = { ...A.snapshot(), slide_key: 'scope:1', watching: [{ person: PEOPLE[2], kind: 'controller' as const }] };
    A.sync.read(same);
    expect(A.sync.getView().position?.slide_key).toBe('overview:1');
    expect(A.sync.getView().state?.watching).toHaveLength(1);
  });

  it('a refused move (a 422) is dropped and said, never resent', async () => {
    const server = new Server();
    const net = new Network();
    const B = tab(server, net, 1, 'B');
    B.read();
    B.sync.go(to('f:F-99'));
    await net.fail(0, new MoveRefused('That slide is not in this report for this host filter.'));
    expect(B.sync.getView().reconciling).toBe(false);
    expect(B.sync.getView().pending).toBeNull();
    expect(B.sync.getView().refused).toMatch(/not in this report/);
    B.read();
    B.read();
    expect(net.labels()).toEqual([]);                                   // the code review's loop: none
    expect(B.reads()).toBe(0);
    B.sync.go(to('summary:1'));                                         // a new move clears the message
    expect(B.sync.getView().refused).toBeNull();
    await net.answer();
    expect(server.slide).toBe('summary:1');
  });

  it('a target whose move keeps getting no answer is sent again once, then dropped', async () => {
    const server = new Server();
    const net = new Network();
    const B = tab(server, net, 1, 'B');
    B.read();
    B.sync.go(to('summary:1'));
    await net.fail(0, new Error('network'));
    B.read();
    expect(net.labels()).toEqual(['move B summary:1@1']);               // the one resend
    await net.fail(0, new Error('network'));
    B.read();
    expect(net.labels()).toEqual([]);                                   // not again
    expect(B.sync.getView().pending).toBeNull();
    expect(B.sync.getView().refused).not.toBeNull();
  });

  it('End reaches all three and stops every move', async () => {
    const server = new Server();
    const net = new Network();
    const A = tab(server, net, 3, 'A');
    const B = tab(server, net, 1, 'B');
    const C = tab(server, net, 2, 'C');
    [A, B, C].forEach((t) => t.read());
    B.sync.go(to('summary:1'));
    server.end();
    [A, B, C].forEach((t) => t.read());
    [A, B, C].forEach((t) => expect(t.sync.getView().state?.ended).not.toBeNull());
    await net.answer();                                                 // the in-flight move: refused, ended
    expect(B.sync.go(to('scope:1'))).toBe(false);
    expect(net.labels()).toEqual([]);
  });
});

describe('presentation sync — the spotlight keeps its own order (E6)', () => {
  it('two clicks whose answers arrive out of order: the second stays', () => {
    const server = new Server();
    const clock = { t: 1000 };
    const B = tab(server, new Network(), 1, 'B', clock);
    B.read();
    server.spotlight(100, 100);
    const first = B.snapshot();
    server.spotlight(900, 500);
    const second = B.snapshot();
    B.sync.read(second);
    B.sync.read(first);                                                 // late, lower seq
    expect(B.sync.getView().spotlight).toMatchObject({ seq: 2, x: 900, y: 500 });
  });

  it('a spotlight in an equal-revision answer is taken, and its time runs from when it landed', () => {
    const server = new Server();
    const clock = { t: 1000 };
    const A = tab(server, new Network(), 3, 'A', clock);
    A.read();
    server.spotlight(10, 20);                                           // same revision as the read before
    clock.t = 2000;
    A.read();
    const held = A.sync.getView().spotlight;
    expect(held).toMatchObject({ seq: 1, revision: 1, expiresAt: 7000 });
    expect(spotlightShown(held, 1, 6999)).toBe(true);
    expect(spotlightShown(held, 1, 7000)).toBe(false);                  // expired
    expect(spotlightShown(held, 2, 3000)).toBe(false);                  // another revision's slide
  });

  it('a Display rings it only once the revision it is for has RENDERED', async () => {
    const server = new Server();
    const net = new Network();
    const clock = { t: 0 };
    const A = tab(server, net, 3, 'A', clock);
    const B = tab(server, net, 1, 'B', clock);
    [A, B].forEach((t) => t.read());
    A.sync.setRendered({ revision: 1, slide_key: 'overview:1', host_filter: null, fitted: false });
    B.sync.go(to('summary:1'));
    await net.answer();                                                 // revision 2
    server.spotlight(5, 5);                                             // on revision 2's slide
    A.read();                                                           // A accepted 2, still showing 1
    const view = A.sync.getView();
    expect(view.state?.revision).toBe(2);
    expect(spotlightShown(view.spotlight, view.renderedRevision, clock.t)).toBe(false);
    A.sync.setRendered({ revision: 2, slide_key: 'summary:1', host_filter: null, fitted: false });
    const after = A.sync.getView();
    expect(spotlightShown(after.spotlight, after.renderedRevision, clock.t + 100)).toBe(true);
    // Expired before the render finished: never shown.
    expect(spotlightShown(after.spotlight, after.renderedRevision, clock.t + 5000)).toBe(false);
  });

  it('a move clears it: the new revision no longer matches', async () => {
    const server = new Server();
    const net = new Network();
    const B = tab(server, net, 1, 'B', { t: 0 });
    B.read();
    server.spotlight(5, 5);
    B.read();
    B.sync.go(to('summary:1'));
    await net.answer();
    const view = B.sync.getView();
    expect(spotlightShown(view.spotlight, view.state?.revision, 1)).toBe(false);
  });
});

describe('presentation sync — the agenda is part of the position (E3)', () => {
  it('a target differing only in its agenda state is still a move', async () => {
    const server = new Server();
    const net = new Network();
    const B = tab(server, net, 1, 'B');
    B.read();
    const explore: Target = { ...to('overview:1'), agenda: { agenda_id: 4, version: 1, mode: 'explore', item_index: 0 } };
    expect(B.sync.go(explore)).toBe(true);
    expect(net.labels()).toEqual(['move B overview:1@1']);
    await net.answer();
    // Accepted with its agenda state: the target is settled, nothing resent.
    expect(B.sync.getView().state?.agenda?.mode).toBe('explore');
    expect(B.sync.getView().pending).toBeNull();
    expect(net.labels()).toEqual([]);
  });
});
