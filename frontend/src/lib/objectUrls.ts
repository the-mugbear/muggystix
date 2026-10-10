/**
 * Object URLs: the one place that makes and revokes them (plan B1).
 *
 * An object URL (`URL.createObjectURL`) pins its bytes in the browser until
 * `URL.revokeObjectURL` — and a missed one shows nowhere.  So nothing else
 * makes one for something on screen: an API function that fetches
 * authenticated bytes answers with the `Blob` (`getNoteAttachmentBlob`,
 * `fetchWebInterfaceScreenshot`), a pasted file is a `Blob` already, and the
 * URL is made here, by the store that will revoke it (`acceptBlob`,
 * `putBlob`, `useBlobUrl`).  A lint rule holds both calls to this file and
 * `utils/download` (`eslint.config.mjs`).  There were five
 * hand-made ways of not missing one (a list of "created" URLs, an "unmounted"
 * flag, a request counter, an effect per URL, a ref read at unmount).  This is
 * the one: a URL is HELD under a key, and it is revoked — once — when
 *
 *   - its key is released (the image was taken out, its upload finished),
 *   - its key is given another URL, or asked for again (`claim`),
 *   - everything is dropped (`clear`: the page moved to another record),
 *   - its owner has gone (`close`: the component unmounted);
 *
 * and bytes that ARRIVE after one of those — the request was still out — are
 * not held: their URL is revoked at once (`claim(key).acceptBlob(blob)`).
 *
 * `useObjectUrls()` is the store of one mounted component; `useBlobUrl(blob)`
 * is the URL of bytes already in hand.  Nothing else in `src/` calls
 * `URL.createObjectURL` or `URL.revokeObjectURL` (a file save is
 * `utils/download`, which makes and revokes its own).
 *
 * Not server state: a URL is a handle on bytes in THIS tab, so it is never a
 * query's data (a query's answer can be read again after it was revoked).
 */
import { useEffect, useRef, useState } from 'react';

/** The right to say what a key's URL is, taken BEFORE the bytes are asked for. */
export interface ObjectUrlClaim {
  /**
   * The URL has arrived.  It is held under the key — and returned — only when
   * the claim is still current; otherwise it is revoked at once and `null`
   * comes back (nothing will show it, so nothing else would revoke it).
   */
  accept: (url: string) => string | null;
  /** The bytes have arrived: their URL is made here and accepted as above
   *  (so one made for a claim that is no longer current is revoked at once). */
  acceptBlob: (blob: Blob) => string | null;
  /** Still the latest claim of its key, in a store that has not been cleared
   *  or closed since: what it brings would be shown. */
  isCurrent: () => boolean;
}

export class ObjectUrlStore<K> {
  private held = new Map<K, string>();

  private claims = new Map<K, object>();

  private isClosed = false;

  /** Closed: its owner has gone, and whatever arrives is revoked. */
  get closed(): boolean {
    return this.isClosed;
  }

  /** How many URLs are held. */
  get size(): number {
    return this.held.size;
  }

  get(key: K): string | undefined {
    return this.held.get(key);
  }

  /**
   * "The URL of this key is being made": what the key holds now is revoked
   * (it is being replaced), and an earlier claim of the key that has not
   * answered yet is no longer current — when it does answer, its URL is
   * revoked.  So the last one asked for is the one shown, whatever order the
   * answers come in.
   */
  claim(key: K): ObjectUrlClaim {
    this.release(key);
    const token = {};
    if (!this.isClosed) this.claims.set(key, token);
    const isCurrent = () => !this.isClosed && this.claims.get(key) === token;
    const accept = (url: string): string | null => {
      if (!isCurrent()) {
        URL.revokeObjectURL(url);
        return null;
      }
      // Answered: the claim is spent (a second answer would be revoked).
      this.claims.delete(key);
      this.held.set(key, url);
      return url;
    };
    return { isCurrent, accept, acceptBlob: (blob) => accept(URL.createObjectURL(blob)) };
  }

  /** Hold a URL that is already made, in place of whatever the key held. */
  put(key: K, url: string): string | null {
    return this.claim(key).accept(url);
  }

  /** Make the URL of bytes already in hand (a pasted file) and hold it, in
   *  place of whatever the key held. */
  putBlob(key: K, blob: Blob): string | null {
    return this.claim(key).acceptBlob(blob);
  }

  /** The key's URL is no longer shown: revoke it, and whatever is still on
   *  its way for the key. */
  release(key: K): void {
    this.claims.delete(key);
    const url = this.held.get(key);
    if (url === undefined) return;
    this.held.delete(key);
    URL.revokeObjectURL(url);
  }

  /** Release every key that `keep` does not want. */
  keepOnly(keep: (key: K) => boolean): void {
    [...this.held.keys(), ...this.claims.keys()].forEach((key) => {
      if (!keep(key)) this.release(key);
    });
  }

  /** Everything held is revoked and everything on its way will be; the store
   *  stays usable (the page moved to another record). */
  clear(): void {
    this.claims.clear();
    const urls = [...this.held.values()];
    this.held.clear();
    urls.forEach((url) => URL.revokeObjectURL(url));
  }

  /** The owner has gone: as `clear`, and nothing is held from now on. */
  close(): void {
    this.clear();
    this.isClosed = true;
  }

  /** The owner is (again) on screen — React mounts, unmounts and mounts a
   *  component once more in development; what was claimed before stays dead. */
  open(): void {
    this.isClosed = false;
  }
}

/**
 * The object URLs of one mounted component: all of them are revoked when it
 * unmounts, and one that arrives afterwards is revoked on arrival.  The store
 * is the same object for the component's whole life (safe in a dependency
 * list, and in a `mutationFn`).
 */
export function useObjectUrls<K>(): ObjectUrlStore<K> {
  const ref = useRef<ObjectUrlStore<K> | null>(null);
  if (ref.current === null) ref.current = new ObjectUrlStore<K>();
  const store = ref.current;
  useEffect(() => {
    store.open();
    return () => store.close();
  }, [store]);
  return store;
}

/**
 * An object URL for bytes already in hand (a query's `Blob`), for as long as
 * those bytes are the ones shown: revoked when the blob changes or the
 * component goes.  `null` until it is made, and while there is no blob.
 */
export function useBlobUrl(blob: Blob | null | undefined): string | null {
  const [made, setMade] = useState<{ blob: Blob; url: string } | null>(null);
  useEffect(() => {
    if (!blob) return undefined;
    const url = URL.createObjectURL(blob);
    setMade({ blob, url });
    return () => URL.revokeObjectURL(url);
  }, [blob]);
  // Never the URL of the bytes before these: it has been revoked.
  return blob && made?.blob === blob ? made.url : null;
}
