import { useEffect, useSyncExternalStore } from 'react';

/**
 * The record a detail page is about, for the browser tab (5.379.0): a host's
 * address, a finding's title.  Every detail page was "Host · <project>" or
 * "Finding · <project>", so several open tabs could not be told apart.
 *
 *   usePageSubject(host?.ip_address);          // the page that shows the record
 *   const subject = useCurrentPageSubject();   // the shell, which writes the title
 *
 * One value for the one page on screen: the page states it once its record is
 * read and takes it back when it leaves.  A store rather than a write to
 * `document.title` from the page, because the shell writes the title too and
 * the later write would win by effect order.
 */
let subject: string | null = null;
const listeners = new Set<() => void>();

const publish = (next: string | null) => {
  if (next === subject) return;
  subject = next;
  listeners.forEach((listener) => listener());
};

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
};

/** State what this page is about; `null` / empty while it is not known. */
export function usePageSubject(text: string | null | undefined): void {
  const stated = text && text.trim() ? text.trim() : null;
  useEffect(() => {
    publish(stated);
    return () => publish(null);
  }, [stated]);
}

export function useCurrentPageSubject(): string | null {
  return useSyncExternalStore(subscribe, () => subject, () => null);
}
