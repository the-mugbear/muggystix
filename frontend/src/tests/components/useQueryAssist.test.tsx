import React from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api', () => ({
  getHostQuerySchema: vi.fn(),
  validateHostQuery: vi.fn(),
  listHostQueryHistory: vi.fn(),
  recordHostQuery: vi.fn(),
  deleteHostQuery: vi.fn(),
  clearHostQueryHistory: vi.fn(),
}));
// One toast object for the file, so a test can see what was said.
const toast = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(), dismiss: vi.fn(),
}));
vi.mock('../../contexts/ToastContext', async () => ({
  ...(await vi.importActual<typeof import('../../contexts/ToastContext')>('../../contexts/ToastContext')),
  useToast: () => toast,
}));

import * as api from '../../services/api';
import { useQueryAssist } from '../../components/hosts/useQueryAssist';
import { createQueryClient } from '../../lib/query';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const schema = { fields: [{ name: 'port', aliases: [], value_source: 'port', trgm: false, enum_values: [] }], examples: [] };
const entry = (id: number, q: string) => ({ id, q, result_count: 3, created_at: '2026-10-01T00:00:00Z' });

beforeEach(() => {
  vi.clearAllMocks();
  mocked.getHostQuerySchema.mockResolvedValue(schema);
  mocked.listHostQueryHistory.mockResolvedValue([entry(1, 'port:22'), entry(2, 'port:443')]);
  mocked.recordHostQuery.mockResolvedValue(entry(3, 'os:linux'));
  mocked.deleteHostQuery.mockResolvedValue(undefined);
  mocked.clearHostQueryHistory.mockResolvedValue(undefined);
});

const queries = (result: { current: ReturnType<typeof useQueryAssist> }) => result.current.history.map((h) => h.q);

describe('useQueryAssist — the recent queries', () => {
  it('removing one takes it out of the list without reading the list again', async () => {
    const { result } = renderHook(() => useQueryAssist(''));
    await waitFor(() => expect(queries(result)).toEqual(['port:22', 'port:443']));

    act(() => { result.current.removeHistory(1); });
    await waitFor(() => expect(queries(result)).toEqual(['port:443']));
    expect(mocked.deleteHostQuery).toHaveBeenCalledWith(1, 1);
    expect(mocked.listHostQueryHistory).toHaveBeenCalledTimes(1);
  });

  it('clearing empties the list', async () => {
    const { result } = renderHook(() => useQueryAssist(''));
    await waitFor(() => expect(queries(result)).toHaveLength(2));

    act(() => { result.current.clearHistory(); });
    await waitFor(() => expect(queries(result)).toEqual([]));
    expect(mocked.clearHostQueryHistory).toHaveBeenCalledWith(1);
  });

  it('recording a query reads the list again; blank text records nothing', async () => {
    const { result } = renderHook(() => useQueryAssist(''));
    await waitFor(() => expect(queries(result)).toHaveLength(2));
    mocked.listHostQueryHistory.mockResolvedValue([entry(3, 'os:linux'), entry(1, 'port:22'), entry(2, 'port:443')]);

    act(() => { result.current.recordQuery('   '); });
    expect(mocked.recordHostQuery).not.toHaveBeenCalled();
    act(() => { result.current.recordQuery(' os:linux ', 3); });
    await waitFor(() => expect(queries(result)).toEqual(['os:linux', 'port:22', 'port:443']));
    expect(mocked.recordHostQuery).toHaveBeenCalledWith(1, 'os:linux', 3);
  });

  // They used to fail silently: a ✕ that did nothing.
  it('a refused write is said, and the list stays as it was', async () => {
    const refused = { response: { status: 403, data: { detail: 'Not allowed' } } };
    mocked.deleteHostQuery.mockRejectedValue(refused);
    mocked.clearHostQueryHistory.mockRejectedValue(refused);
    mocked.recordHostQuery.mockRejectedValue(refused);
    const { result } = renderHook(() => useQueryAssist(''));
    await waitFor(() => expect(queries(result)).toHaveLength(2));

    act(() => { result.current.removeHistory(1); });
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1));
    act(() => { result.current.clearHistory(); });
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(2));
    // Recording is the page's doing on every search: a refusal is not put in
    // front of the reader (it would fire on each Enter), only logged.
    const logged = vi.spyOn(console, 'warn').mockImplementation(() => {});
    act(() => { result.current.recordQuery('os:linux'); });
    await waitFor(() => expect(logged).toHaveBeenCalledTimes(1));
    logged.mockRestore();
    expect(toast.warning).not.toHaveBeenCalled();
    for (const [said] of toast.error.mock.calls) {
      expect(typeof said).toBe('string');
      expect(said).not.toBe('');
    }
    expect(queries(result)).toEqual(['port:22', 'port:443']);
    expect(mocked.listHostQueryHistory).toHaveBeenCalledTimes(1);
  });
});

// The schema's lifecycle is its own (`rememberFor` + `retryOnMount`): one
// client for both mounts, as in the app — two plain renders are two clients.
describe('useQueryAssist — the DSL schema is remembered', () => {
  const sharedClient = () => {
    const client = createQueryClient();
    return ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  };

  it('a second visit asks nothing, and has the fields at once', async () => {
    const wrapper = sharedClient();
    const first = renderHook(() => useQueryAssist(''), { wrapper });
    await waitFor(() => expect(first.result.current.schema).toEqual(schema));
    first.unmount();

    const second = renderHook(() => useQueryAssist(''), { wrapper });
    expect(second.result.current.schema).toEqual(schema);
    await waitFor(() => expect(mocked.listHostQueryHistory).toHaveBeenCalledTimes(2));
    expect(mocked.getHostQuerySchema).toHaveBeenCalledTimes(1);
  });

  it('a failure is not remembered: the next visit asks again', async () => {
    mocked.getHostQuerySchema.mockRejectedValueOnce(new Error('503'));
    const wrapper = sharedClient();
    const first = renderHook(() => useQueryAssist(''), { wrapper });
    await waitFor(() => expect(mocked.getHostQuerySchema).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(queries(first.result)).toHaveLength(2));
    expect(first.result.current.schema).toBeNull();
    first.unmount();

    const second = renderHook(() => useQueryAssist(''), { wrapper });
    await waitFor(() => expect(second.result.current.schema).toEqual(schema));
    expect(mocked.getHostQuerySchema).toHaveBeenCalledTimes(2);
  });
});
