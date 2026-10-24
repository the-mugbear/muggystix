/**
 * An in-memory BlueStick for the presentation Playwright suites (5.424.0).
 *
 * The presentation routes apply the SERVER's rules (backend
 * services/presentation_service.py, pinned there by
 * tests/test_presentation_mode.py): a move needs the signed-in driver AND the
 * driving tab, at the revision it saw, while live; Take control always wins
 * while live; only a move, Take control and End raise the revision; presence
 * and a Display's acknowledgment (monotonic) never do.  The slides are the
 * server's own output for a worst-case report (fixtures/presentation-worst-case.json,
 * pinned to the slide builder by backend/tests/test_presentation_e2e_fixture.py).
 *
 * Every browser context of a suite talks to ONE instance, through
 * `page.route` — so three people in three contexts share one presentation.
 * Anything else the app asks while booting answers an empty value.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { BrowserContext, Route } from '@playwright/test';

export const FIXTURE = JSON.parse(readFileSync(join(__dirname, 'fixtures', 'presentation-worst-case.json'), 'utf8')) as
  Record<string, { stage: Record<string, number>; slides: Array<{ key: string; kind: string; ref?: string }>; sites: string[]; segments: string[]; filter: unknown }>;
export const LONG_FILTER = 'no-such-endpoint-'.repeat(11);

// A 1×1 PNG (the frozen copy of image 11).
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64',
);

export interface Person { id: number; username: string; full_name: string; token: string }
export const PEOPLE: Record<string, Person> = {
  ben: { id: 1, username: 'ben', full_name: 'Ben Starter', token: 'tok-ben' },
  cara: { id: 2, username: 'cara', full_name: 'Cara Taker', token: 'tok-cara' },
  avery: { id: 3, username: 'avery', full_name: 'Avery Display', token: 'tok-avery' },
};

type Filter = { kind: string; value: string } | null;
interface Viewer { user: number; client: string; kind: string; seen: number; rendered: null | { revision: number; slide_key: string; host_filter: Filter; fitted: boolean } }

const filterKey = (f: Filter) => (f ? `${f.kind}:${f.kind === 'text' && f.value === LONG_FILTER ? 'long' : f.value}` : 'none');
const sameFilter = (a: Filter, b: Filter) => (a?.kind ?? null) === (b?.kind ?? null) && (a?.value ?? null) === (b?.value ?? null);

export class FakeBackend {
  live: null | {
    id: number; revision: number; slide: string; filter: Filter; driverUser: number | null;
    driverClient: string | null; startedBy: number; ended: null | { reason: string; by: number };
  } = null;
  viewers = new Map<string, Viewer>();
  conflicts = 0;
  moves: Array<{ user: number; client: string; revision: number; slide: string; ok: boolean }> = [];
  layoutReports: Array<{ slide_key: string; overflow_px: number; font: string }> = [];
  unmatched = new Set<string>();
  /** Hooks a test sets to hold or alter answers. */
  delayHeartbeat: ((user: number) => number) | null = null;

  slidesFor(f: Filter) {
    return FIXTURE[filterKey(f)];
  }

  private person(id: number | null) {
    if (id == null) return null;
    const p = Object.values(PEOPLE).find((x) => x.id === id)!;
    return { id: p.id, name: p.full_name, profile_image: null };
  }

  state(user: number, client: string | null) {
    const p = this.live!;
    const now = Date.now();
    const displays = [...this.viewers.values()].filter((v) => v.kind === 'display' && now - v.seen < 300_000).map((v, i) => ({
      client: i + 1, person: this.person(v.user), fitted: v.rendered?.fitted ?? false,
      status: now - v.seen > 10_000 ? 'disconnected' : v.rendered && v.rendered.revision >= p.revision ? 'synchronized' : 'catching_up',
      rendered: v.rendered ? { revision: v.rendered.revision, slide_key: v.rendered.slide_key, host_filter: v.rendered.host_filter } : null,
    }));
    return {
      id: p.id, report_id: 3, revision: p.revision, slide_key: p.slide, host_filter: p.filter,
      driver: this.person(p.driverUser),
      you_are_driving: !p.ended && client != null && p.driverUser === user && p.driverClient === client,
      driving_elsewhere: !p.ended && p.driverUser === user && p.driverClient !== client,
      started_by: this.person(p.startedBy), started_at: null,
      ended: p.ended ? { at: new Date().toISOString(), reason: p.ended.reason, by: this.person(p.ended.by) } : null,
      watching: [...this.viewers.values()].filter((v) => now - v.seen < 30_000).map((v) => ({ person: this.person(v.user), kind: v.kind })),
      displays,
    };
  }

  private validPosition(slide: string, f: Filter) {
    const list = this.slidesFor(f);
    return Boolean(list && list.slides.some((s) => s.key === slide));
  }

  private landing(f: Filter) {
    const p = this.live!;
    const ref = p.slide.startsWith('f:') ? p.slide.split(':')[1] : null;
    if (!ref) return p.slide;
    return this.slidesFor(f).slides.find((s) => s.ref === ref && s.kind === 'hosts')?.key ?? p.slide;
  }

  /** The routes, for one person's browser context. */
  async install(context: BrowserContext, who: Person) {
    await context.addInitScript(([token, user]) => {
      window.localStorage.setItem('auth_token', token);
      window.localStorage.setItem('auth_user', user);
    }, [who.token, JSON.stringify({ id: who.id, username: who.username, full_name: who.full_name, role: 'member' })]);
    await context.route('**/api/v1/**', (route) => this.handle(route, who));
  }

  private json(route: Route, status: number, body: unknown) {
    return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  }

  private async handle(route: Route, who: Person) {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^\/api\/v1/, '');
    const method = req.method();
    const body = req.postDataJSON?.() ?? null;
    const user = who.id;

    if (path === '/auth/profile') {
      return this.json(route, 200, { id: who.id, username: who.username, full_name: who.full_name, role: 'member',
        is_active: true, last_login: null, created_at: '2026-01-01T00:00:00Z', profile_image: null });
    }
    if (path === '/projects/' || path === '/projects') {
      return this.json(route, 200, [{ id: 1, name: 'Acme', slug: 'acme', my_role: 'auditor', status: 'active', is_archived: false }]);
    }

    let m = path.match(/^\/projects\/1\/client-reports\/3\/presentation(\/.*)?$/);
    if (m) {
      const rest = m[1] ?? '';
      if (rest === '' && method === 'GET') {
        return this.json(route, 200, { live: this.live && !this.live.ended ? this.state(user, url.searchParams.get('client_id')) : null });
      }
      if (rest === '' && method === 'POST') {
        if (!this.live || this.live.ended) {
          this.live = { id: 9, revision: 1, slide: 'overview:1', filter: null, driverUser: user, driverClient: body.client_id,
            startedBy: user, ended: null };
          this.viewers.clear();
        }
        return this.json(route, 200, this.state(user, body.client_id));
      }
      if (rest === '/slides') {
        const kind = url.searchParams.get('host_filter_kind');
        const value = url.searchParams.get('host_filter_value');
        const list = this.slidesFor(kind && value ? { kind, value } : null);
        return list ? this.json(route, 200, list) : this.json(route, 422, { detail: 'filter' });
      }
      m = rest.match(/^\/images\/(\d+)$/);
      if (m) {
        if (m[1] === '11') return route.fulfill({ status: 200, contentType: 'image/png', body: PNG });
        return this.json(route, m[1] === '13' ? 410 : 404, { detail: 'no copy' });
      }
      if (rest === '/layout-reports') {
        this.layoutReports.push(body);
        return this.json(route, 202, { recorded: true });
      }
    }

    m = path.match(/^\/projects\/1\/presentations\/9\/(heartbeat|move|take-control|end)$/);
    if (m && this.live) {
      const p = this.live;
      const action = m[1];
      if (action === 'heartbeat') {
        if (!p.ended) {
          const key = body.client_id as string;
          const v = this.viewers.get(key) ?? { user, client: key, kind: body.kind, seen: 0, rendered: null };
          v.seen = Date.now();
          v.kind = body.kind;
          if (body.rendered && body.kind === 'display') {
            const r = body.rendered;
            if (r.revision > p.revision || !this.validPosition(r.slide_key, r.host_filter ?? null)) {
              return this.json(route, 422, { detail: 'not a render of this report' });
            }
            if (!v.rendered || v.rendered.revision < r.revision) v.rendered = r;
          }
          this.viewers.set(key, v);
        }
        const answer = this.state(user, body.client_id);
        const delay = this.delayHeartbeat?.(user) ?? 0;
        if (delay) await new Promise((r) => setTimeout(r, delay));
        return this.json(route, 200, answer);
      }
      if (action === 'move') {
        const ok = !p.ended && body.revision === p.revision && p.driverUser === user && p.driverClient === body.client_id;
        const f: Filter = body.host_filter ?? null;
        if (ok && !this.validPosition(body.slide_key, f)) return this.json(route, 422, { detail: 'slide' });
        if (ok && !sameFilter(f, p.filter) && body.slide_key !== this.landing(f)) return this.json(route, 422, { detail: 'landing' });
        this.moves.push({ user, client: body.client_id, revision: body.revision, slide: body.slide_key, ok });
        if (!ok) {
          this.conflicts += 1;
          return this.json(route, 409, { detail: {
            reason: p.ended ? 'ended' : p.driverClient !== body.client_id || p.driverUser !== user ? 'not_driver' : 'stale',
            message: 'refused', presentation: this.state(user, body.client_id),
          } });
        }
        p.slide = body.slide_key;
        p.filter = f;
        p.revision += 1;
        return this.json(route, 200, this.state(user, body.client_id));
      }
      if (action === 'take-control') {
        if (p.ended) return this.json(route, 409, { detail: { reason: 'ended', message: 'ended', presentation: this.state(user, body.client_id) } });
        p.driverUser = user;
        p.driverClient = body.client_id;
        p.revision += 1;
        return this.json(route, 200, this.state(user, body.client_id));
      }
      if (action === 'end') {
        if (p.startedBy !== user) return this.json(route, 403, { detail: 'not the starter' });
        p.ended = { reason: 'ended', by: user };
        p.driverClient = null;
        p.revision += 1;
        return this.json(route, 200, this.state(user, body?.client_id ?? null));
      }
    }

    // The app shell's own reads, answered empty.
    const SHELL: Record<string, unknown> = {
      '/notifications/unread-count': { count: 0 },
      '/remediation-policy': { enabled: false },
      '/projects/1/proposals/summary': { pending: 0, by_kind: {}, mine: 0, pending_mine: 0 },
      '/projects/1/agent-sessions': { items: [], total: 0, limit: 50, offset: 0, returned: 0, has_more: false },
    };
    if (method === 'GET' && path in SHELL) return this.json(route, 200, SHELL[path]);
    // Anything else the app shell asks while booting.
    this.unmatched.add(`${method} ${path}`);
    return this.json(route, 200, method === 'GET' ? (path.endsWith('/') ? [] : {}) : {});
  }
}
