/**
 * Presentation mode — the multi-user run (5.424.0), in three browser contexts
 * (three people) over one in-memory backend:
 *
 *   A (Avery) opens the Display — what the audience sees;
 *   B (Ben) starts and drives; a second Controller opened FROM B's tab (B2)
 *     has its own tab id and is not the driver;
 *   C (Cara) browses on her own, then takes control.
 *
 * A follows both drivers through long text pages, an image slide and endpoint
 * pages, a host filter changed from a later endpoint page, a held key, dropped
 * heartbeats (A says it is out of sync, then catches up), a hidden tab, and
 * End.  A never shows an earlier slide after a later one; no move conflicts.
 */
import { expect, test, type Browser, type Page } from '@playwright/test';

import { FakeBackend, FIXTURE, PEOPLE, type Person } from './fakeBackend';

const SLIDES = FIXTURE.none.slides.map((s) => s.key);
const DMZ = FIXTURE['site:DMZ'].slides.map((s) => s.key);

const open = async (browser: Browser, be: FakeBackend, who: Person) => {
  const ctx = await browser.newContext();
  await be.install(ctx, who);
  return ctx;
};

const shown = (page: Page) => page.locator('[data-testid=ps-stage]');

test('three people, one presentation', async ({ browser }) => {
  const be = new FakeBackend();
  const ctxA = await open(browser, be, PEOPLE.avery);
  const ctxB = await open(browser, be, PEOPLE.ben);
  const ctxC = await open(browser, be, PEOPLE.cara);

  // B starts from the report page's "Present" (the Controller with ?start=1).
  const b = await ctxB.newPage();
  await b.goto('/reports/3/present?start=1');
  await expect(b.getByText('You are driving', { exact: true })).toBeVisible();
  expect(be.live?.driverUser).toBe(1);

  // A opens the Display; it renders the overview and says so.
  const a = await ctxA.newPage();
  await a.goto('/reports/3/present/display');
  await expect(shown(a)).toHaveAttribute('data-slide', 'overview:1');
  await expect(b.getByTestId('display-status')).toContainText('Display synchronized');
  // Record every slide A shows, in order.
  await a.evaluate(() => {
    const seen: string[] = [];
    (window as unknown as { __seen: string[] }).__seen = seen;
    const stage = document.querySelector('[data-testid=ps-stage]')!;
    new MutationObserver(() => {
      const k = stage.getAttribute('data-slide');
      if (k && seen[seen.length - 1] !== k) seen.push(k);
    }).observe(stage, { attributes: true, attributeFilter: ['data-slide'] });
  });

  // B2: a second Controller opened from B's tab — its own tab id, not the driver.
  const [b2] = await Promise.all([ctxB.waitForEvent('page'), b.evaluate(() => { window.open('/reports/3/present'); })]);
  await expect(b2.getByTestId('driving-elsewhere')).toBeVisible();
  await expect(b2.getByText('You are driving', { exact: true })).toHaveCount(0);
  await b2.keyboard.press('j');
  await b2.waitForTimeout(300);
  expect(be.live?.slide).toBe('overview:1');
  await b2.close();

  // B holds j: ten presses, coalesced — no conflict, at most one move per round trip.
  for (let i = 0; i < 10; i += 1) await b.keyboard.press('j');
  await expect.poll(() => be.live?.slide).toBe(SLIDES[10]);
  expect(be.conflicts).toBe(0);
  expect(be.moves.length).toBeLessThanOrEqual(10);
  await expect(shown(a)).toHaveAttribute('data-slide', SLIDES[10]);

  // An image slide, then a later endpoint page, picked from the slide list.
  const pick = async (page: Page, key: string) => {
    await page.getByRole('navigation', { name: 'Slides' }).locator(`[data-slide-key="${key}"]`).click();
  };
  await pick(b, 'f:F-01:image:11');
  await expect(shown(a)).toHaveAttribute('data-slide', 'f:F-01:image:11');
  await expect(a.getByTestId('ps-image')).toBeVisible();
  await pick(b, 'f:F-01:hosts:3');
  await expect(shown(a)).toHaveAttribute('data-slide', 'f:F-01:hosts:3');

  // A host filter changed from endpoint page 3 lands on page 1 of the filtered list.
  await b.getByRole('combobox', { name: 'Endpoints shown' }).click();
  await b.getByRole('option', { name: 'Site DMZ' }).click();
  await expect.poll(() => be.live?.slide).toBe('f:F-01:hosts:1');
  expect(be.live?.filter).toEqual({ kind: 'site', value: 'DMZ' });
  await expect(shown(a)).toHaveAttribute('data-slide', 'f:F-01:hosts:1');
  await expect(a.getByTestId('ps-footer')).toContainText('matching DMZ');
  expect(DMZ).toContain('f:F-01:hosts:1');

  // Dropped heartbeats: A says it is out of sync, then catches up.
  await ctxA.route('**/presentations/9/heartbeat', (route) => route.abort());
  await b.keyboard.press('j');
  await expect(a.getByRole('status')).toContainText('Reconnecting', { timeout: 10_000 });
  await ctxA.unroute('**/presentations/9/heartbeat');
  await expect(shown(a)).toHaveAttribute('data-slide', be.live!.slide, { timeout: 10_000 });

  // A hidden tab keeps following.
  await a.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await b.keyboard.press('j');
  await expect(shown(a)).toHaveAttribute('data-slide', be.live!.slide, { timeout: 10_000 });

  // C browses on her own: her own position, nothing sent.
  const before = be.live!.slide;
  const cBrowse = await ctxC.newPage();
  await cBrowse.goto('/reports/3/present?browse=1');
  await expect(shown(cBrowse)).toHaveAttribute('data-slide', before);
  await cBrowse.keyboard.press('k');
  await cBrowse.keyboard.press('k');
  await expect(shown(cBrowse)).not.toHaveAttribute('data-slide', before);
  expect(be.live!.slide).toBe(before);

  // …then takes control from a Controller: B is told, C drives, A follows C.
  const c = await ctxC.newPage();
  await c.goto('/reports/3/present');
  await c.getByTestId('take-control').click();
  await expect(c.getByText('You are driving', { exact: true })).toBeVisible();
  expect(be.live!.slide).toBe(before);                         // Take control keeps the slide
  await expect(b.getByTestId('lost-control')).toContainText('Cara Taker took control.');
  await c.keyboard.press('j');
  await expect.poll(() => be.live!.slide).not.toBe(before);
  await expect(shown(a)).toHaveAttribute('data-slide', be.live!.slide);
  await b.keyboard.press('j');                                 // B no longer drives: nothing sent
  await b.waitForTimeout(300);
  expect(be.moves.filter((m) => !m.ok)).toEqual([]);

  // A showed every slide in the order it was driven: never back to an earlier one by a late answer.
  const seen = await a.evaluate(() => (window as unknown as { __seen: string[] }).__seen);
  const driven = ['overview:1', ...be.moves.filter((m) => m.ok).map((m) => m.slide)];
  // What A showed is a subsequence of what was driven, in the same order (it may skip, never go back).
  let at = 0;
  for (const key of seen) {
    const next = driven.indexOf(key, at);
    expect(next, `A showed ${key} out of order (driven: ${driven.join(', ')})`).toBeGreaterThanOrEqual(0);
    at = next;
  }
  expect(seen.length).toBeGreaterThan(3);

  // End (B started it): every tab hears it.
  await b.getByTestId('end-presentation').click();
  await expect(a.getByText(/The presentation ended/)).toBeVisible({ timeout: 10_000 });
  await expect(c.getByText(/The presentation ended/)).toBeVisible({ timeout: 10_000 });
  expect(be.conflicts).toBe(0);

  await Promise.all([ctxA.close(), ctxB.close(), ctxC.close()]);
});
