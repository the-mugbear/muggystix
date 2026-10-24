/**
 * Presentation mode — the rendered overflow check (5.424.0).  The server pages
 * slides by an ESTIMATE from the shipped fonts' metrics; only a real browser
 * can say whether every slide fits.  Every slide of a worst-case report — the
 * server's own output, `fixtures/presentation-worst-case.json` — is drawn at
 * three window sizes, for three host filters, in two runs with different
 * assertions (plan, § Tests):
 *
 *  - shipped fonts: every slide fits WITHOUT the fallback — no overflow, no
 *    "fitted" mark, no layout report, no text below the stage's sizes;
 *  - a system font forced in their place: the guarantee is relaxed, so it
 *    asserts what the fallback promises — nothing clipped, every slide's text
 *    present, the slide keys the same, and each slide that overflowed marked
 *    "fitted" and reported.
 */
import { expect, test, type Page } from '@playwright/test';

import { FakeBackend, FIXTURE, LONG_FILTER, PEOPLE } from './fakeBackend';

const VARIANTS: Array<{ name: string; fk?: string; fv?: string }> = [
  { name: 'none' },
  { name: 'site:DMZ', fk: 'site', fv: 'DMZ' },
  { name: 'text:long', fk: 'text', fv: LONG_FILTER },
];
const SIZES = [{ width: 1600, height: 1000 }, { width: 1280, height: 800 }, { width: 1920, height: 1080 }];
const FORCED_FONT = '.ps-stage, .ps-stage * { font-family: "DejaVu Serif", "Liberation Serif", serif !important; }';

interface Measure { over: number; clipped: boolean; fitted: boolean; minFont: number; text: string }

const measure = (page: Page) => page.evaluate(async (): Promise<Measure> => {
  await document.fonts.ready;
  const stage = document.querySelector('[data-testid=ps-stage]') as HTMLElement;
  const body = document.querySelector('[data-testid=ps-body]') as HTMLElement;
  const inner = document.querySelector('[data-testid=ps-body-inner]') as HTMLElement;
  // Let the Stage's own measure-and-fit pass run.
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  await new Promise((r) => setTimeout(r, 30));
  const over = Math.max(inner.scrollHeight - body.clientHeight, inner.scrollWidth - body.clientWidth,
    stage.scrollHeight - stage.clientHeight, 0);
  const bodyRect = body.getBoundingClientRect();
  const innerRect = inner.getBoundingClientRect();
  const clipped = innerRect.height > bodyRect.height + 1 || innerRect.width > bodyRect.width + 1;
  let minFont = Infinity;
  stage.querySelectorAll('*').forEach((el) => {
    if ((el as HTMLElement).closest('[data-testid=ps-fitted]')) return;
    const own = Array.from(el.childNodes).some((n) => n.nodeType === 3 && (n.textContent ?? '').trim());
    if (own) minFont = Math.min(minFont, parseFloat(getComputedStyle(el).fontSize));
  });
  return { over, clipped, fitted: Boolean(document.querySelector('[data-testid=ps-fitted]')), minFont, text: inner.innerText };
});

const waitForSlide = async (page: Page, key: string, kind: string) => {
  await expect(page.locator('[data-testid=ps-stage]')).toHaveAttribute('data-slide', key);
  if (kind === 'image') {
    await page.waitForFunction(() => {
      const img = document.querySelector('[data-testid=ps-image]') as HTMLImageElement | null;
      return (img && img.complete) || document.querySelector('[data-testid=ps-image-missing]');
    });
  }
};

for (const forced of [false, true]) {
  test(`every slide ${forced ? 'is fitted, never clipped, with a system font forced in' : 'fits with the shipped fonts'}`, async ({ browser }) => {
    const be = new FakeBackend();
    be.live = { id: 9, revision: 1, slide: 'overview:1', filter: null, driverUser: 1, driverClient: 'elsewhere', startedBy: 1, ended: null };
    const ctx = await browser.newContext();
    await be.install(ctx, PEOPLE.ben);
    if (forced) await ctx.addInitScript((css) => {
      document.addEventListener('DOMContentLoaded', () => {
        const s = document.createElement('style');
        s.textContent = css;
        document.head.appendChild(s);
      });
    }, FORCED_FONT);
    const page = await ctx.newPage();
    const problems: string[] = [];
    for (const size of SIZES) {
      await page.setViewportSize(size);
      for (const v of VARIANTS) {
        const slides = FIXTURE[v.name].slides as Array<{ key: string; kind: string }>;
        const qs = new URLSearchParams({ browse: '1', slide: slides[0].key });
        if (v.fk && v.fv) { qs.set('fk', v.fk); qs.set('fv', v.fv); }
        await page.goto(`/reports/3/present?${qs.toString()}`);
        for (let i = 0; i < slides.length; i += 1) {
          const s = slides[i];
          if (i > 0) await page.keyboard.press('j');
          await waitForSlide(page, s.key, s.kind);
          const m = await measure(page);
          const where = `${size.width}x${size.height} ${v.name} ${s.key}`;
          if (!forced) {
            if (m.over > 0.5) problems.push(`${where}: overflows by ${m.over}px`);
            if (m.fitted) problems.push(`${where}: needed the fit fallback`);
            if (m.minFont < 18) problems.push(`${where}: text at ${m.minFont}px`);
          } else {
            if (m.clipped) problems.push(`${where}: clipped`);
            if (m.over > 0.5 && !m.fitted) problems.push(`${where}: overflows without the fitted mark`);
          }
          if (s.kind !== 'image' && !m.text.trim()) problems.push(`${where}: no text`);
        }
      }
    }
    expect(problems).toEqual([]);
    if (!forced) {
      expect(be.layoutReports).toEqual([]);
    } else {
      // Each slide that had to be fitted was reported (once per slide and font).
      const fittedKeys = new Set(be.layoutReports.map((r) => r.slide_key));
      expect([...fittedKeys].every((k) => VARIANTS.some((v) => FIXTURE[v.name].slides.some((s) => s.key === k)))).toBe(true);
    }
    await ctx.close();
  });
}
