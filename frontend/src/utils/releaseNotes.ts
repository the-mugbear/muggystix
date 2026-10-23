/** The element id of a release's section on /reference/release-notes — the
 *  page sets it, the About dialog links to it (kept here so the menu does not
 *  pull in the page's lazy chunk). */
export const releaseAnchor = (backend: string, frontend: string): string => `release-${backend}-${frontend}`;

/** The release notes of one version: the page, opened at that release. */
export const releaseNotesPath = (backend: string, frontend: string): string =>
  `/reference/release-notes#${releaseAnchor(backend, frontend)}`;
