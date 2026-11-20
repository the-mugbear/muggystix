/**
 * How the app's router publishes a navigation (react-router 7).
 *
 * Left unset, react-router 7 wraps every router state update in
 * `React.startTransition`, so the address a write just made reaches the page
 * in a LATER render than the state set beside it.  The pages treat the address
 * as the committed state and write it once per action (`useUrlSearchDraft`,
 * `usePagedList`, `keepAddressPage`): with transitions a box lost the space
 * the reader had just typed, and a restored Hosts session asked for the
 * unfiltered list first.  `false` is react-router 6's behaviour, which those
 * were built and tested on.
 *
 * `index.tsx` passes it to the app's one router; the suites' routers get it
 * from `tests/helpers/reactRouterDom.tsx`, so a test never runs under a router
 * the app does not.
 */
export const ROUTER_TRANSITIONS = false;
