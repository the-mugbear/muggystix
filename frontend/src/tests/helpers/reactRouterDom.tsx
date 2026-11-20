/**
 * `react-router-dom` as the suites see it (vitest's `alias`, vite.config.mts):
 * the real library, with the routers a test mounts given the app's own
 * transition setting (`lib/router.ROUTER_TRANSITIONS`) unless the test passes
 * one.  The app mounts ONE router, in `index.tsx`, with that setting; the
 * suites mount theirs in ~150 files, and this keeps them the same router.
 *
 * Everything comes from the ONE entry `react-router` (which `react-router-dom`
 * 7 re-exports): a second entry (`react-router/dom`) is resolved by Node, not
 * by Vite, to another copy of the library, and a hook then finds no router.
 * So `RouterProvider` here is the library's own, without `react-dom`'s
 * `flushSync` — which only a `flushSync: true` navigation uses, and none does.
 */
import React from 'react';
import {
  BrowserRouter as RealBrowserRouter,
  MemoryRouter as RealMemoryRouter,
  RouterProvider as RealRouterProvider,
} from 'react-router';

import { ROUTER_TRANSITIONS } from '../../lib/router';

export * from 'react-router';

export const MemoryRouter = (props: React.ComponentProps<typeof RealMemoryRouter>) => (
  <RealMemoryRouter useTransitions={ROUTER_TRANSITIONS} {...props} />
);

export const BrowserRouter = (props: React.ComponentProps<typeof RealBrowserRouter>) => (
  <RealBrowserRouter useTransitions={ROUTER_TRANSITIONS} {...props} />
);

export const RouterProvider = (props: React.ComponentProps<typeof RealRouterProvider>) => (
  <RealRouterProvider useTransitions={ROUTER_TRANSITIONS} {...props} />
);
