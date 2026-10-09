import { useCallback, useMemo } from 'react';
import { useNavigationType, useSearchParams } from 'react-router-dom';

/**
 * A list's page, kept in the address (UI_STYLE_GUIDE §45): `?page=`, 1-based,
 * left out for the first page — so a reload, a shared link and Back from a
 * row's own page land on the same rows.
 *
 *   const list = usePagedList(fetchPage, [filters], { pageSize, page: useUrlPage() });
 *
 * Choosing a page REPLACES the history entry: paging is not a place Back
 * should stop at.
 */
export interface UrlPage {
  /** Zero-based; anything but a whole number ≥ 2 in the address is the first page. */
  page: number;
  setPage: (page: number) => void;
  /** The address's query — it differs after every navigation that changed it. */
  search: string;
  /** The address was last reached by Back / Forward (or by opening it). */
  byHistory: boolean;
}

/** The ONE reading of a page in the address: digits only, so `2 `, `1e3`,
 *  `0x10` and `2.0` are the first page like `abc` — a page is never a number
 *  the reader did not write as one. */
export const pageFromParams = (params: URLSearchParams, param = 'page'): number => {
  const raw = params.get(param) ?? '';
  return /^[1-9]\d{0,6}$/.test(raw) ? Number(raw) - 1 : 0;
};

export function useUrlPage(param = 'page'): UrlPage {
  const [params, setParams] = useSearchParams();
  const byHistory = useNavigationType() === 'POP';
  const page = pageFromParams(params, param);
  const search = params.toString();
  const setPage = useCallback((next: number) => {
    setParams((prev) => {
      const out = new URLSearchParams(prev);
      if (next > 0) out.set(param, String(next + 1)); else out.delete(param);
      return out;
    }, { replace: true });
  }, [param, setParams]);
  return useMemo(() => ({ page, setPage, search, byHistory }), [page, setPage, search, byHistory]);
}

export default useUrlPage;
