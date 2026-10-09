/**
 * The user guide's "Triage & Analysis" page.  Its prose is static; the one
 * thing it READS is the host query language's schema, for the field table and
 * the `has:` flags — the same source as the Hosts query bar's syntax help.
 *
 * Pinned: what is asked (the project first, the query's signal), what the
 * reader sees from the answer, while it loads, and when it could not be read.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { HostQueryField, HostQuerySchema } from '../../services/api';

const api = vi.hoisted(() => ({ getHostQuerySchema: vi.fn() }));
vi.mock('../../services/api', () => api);

const project = vi.hoisted(() => ({ current: { id: 1, name: 'Demo' } as { id: number; name: string } | null }));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: project.current }),
}));

import TriageGuide from '../../pages/userguide/TriageGuide';

const field = (over: Partial<HostQueryField> & { name: string }): HostQueryField => ({
  aliases: [], value_source: 'free', trgm: false, enum_values: [], description: '', enum_descriptions: {}, ...over,
});

// Field names the page's own prose never uses, so a match is the table's.
const SCHEMA: HostQuerySchema = {
  fields: [
    field({ name: 'zport', aliases: ['zp', 'zports'], description: 'An open port number, from every port scan.' }),
    field({ name: 'zowner', description: 'The registered owner, from the scope file.' }),
    field({
      name: 'has',
      description: 'A flag the host carries.',
      enum_values: ['zweb', 'zcritical'],
      enum_descriptions: { zweb: 'has a web interface', zcritical: 'has a critical scanner observation' },
    }),
  ],
  examples: [],
};

const LOADING = 'Loading field reference…';
const TABLE_HEADING = 'Fields & where the data comes from';

const show = () => render(<MemoryRouter><TriageGuide /></MemoryRouter>);
/** The field table's body rows, as the reader reads them. */
const fieldRows = (): string[] => within(screen.getByRole('table')).getAllByRole('row')
  .slice(1).map((row) => row.textContent ?? '');
/** The `has:` flags, as the reader reads them. */
const flagLines = (): string[] => screen.getAllByRole('listitem')
  .map((li) => li.textContent ?? '').filter((text) => text.startsWith('has:z'));

beforeEach(() => {
  vi.clearAllMocks();
  project.current = { id: 1, name: 'Demo' };
  api.getHostQuerySchema.mockResolvedValue(SCHEMA);
});

describe('TriageGuide — the live field reference', () => {
  it('asks for the schema of the project on screen, once, with the query’s signal', async () => {
    show();
    await screen.findByRole('table');
    expect(api.getHostQuerySchema).toHaveBeenCalledTimes(1);
    expect(api.getHostQuerySchema).toHaveBeenCalledWith(1, expect.any(AbortSignal));
  });

  it('names another project when that is the one selected', async () => {
    project.current = { id: 7, name: 'Other' };
    show();
    await screen.findByRole('table');
    expect(api.getHostQuerySchema).toHaveBeenCalledWith(7, expect.any(AbortSignal));
  });

  it('lists every field with its aliases and where its data comes from', async () => {
    show();
    expect(await screen.findByRole('heading', { name: TABLE_HEADING })).toBeInTheDocument();
    expect(fieldRows()).toEqual([
      'zport:(zp: zports:)An open port number, from every port scan.',
      // No alias: no empty brackets.
      'zowner:The registered owner, from the scope file.',
      'has:A flag the host carries.',
    ]);
    expect(screen.queryByText(LOADING)).toBeNull();
  });

  it('lists the has: flags in the schema’s order, each with its meaning', async () => {
    show();
    expect(await screen.findByRole('heading', { name: 'has: flags' })).toBeInTheDocument();
    expect(flagLines()).toEqual([
      'has:zweb — has a web interface',
      'has:zcritical — has a critical scanner observation',
    ]);
  });

  it('says it is loading until the schema answers, with the rest of the guide already readable', async () => {
    let answer: (schema: HostQuerySchema) => void = () => {};
    api.getHostQuerySchema.mockReturnValue(new Promise<HostQuerySchema>((resolve) => { answer = resolve; }));
    show();

    expect(screen.getByText(LOADING)).toBeInTheDocument();
    expect(screen.queryByRole('table')).toBeNull();
    // The page does not wait for the read.
    expect(screen.getByRole('heading', { name: 'User Guide' })).toBeInTheDocument();
    expect(screen.getByText('Host search syntax')).toBeInTheDocument();
    expect(screen.getByText('Two ways to filter')).toBeInTheDocument();

    answer(SCHEMA);
    expect(await screen.findByRole('table')).toBeInTheDocument();
    expect(screen.queryByText(LOADING)).toBeNull();
  });

  it('a failed read is not left as "loading" nor shown as an empty table: it points to the syntax help', async () => {
    api.getHostQuerySchema.mockRejectedValue({ response: { status: 500, data: { detail: 'schema unavailable' } } });
    show();

    expect(await screen.findByText(/Every field is also listed in/)).toBeInTheDocument();
    expect(screen.getByText('syntax help')).toBeInTheDocument();
    expect(screen.queryByText(LOADING)).toBeNull();
    expect(screen.queryByRole('table')).toBeNull();
    expect(screen.queryByRole('heading', { name: 'has: flags' })).toBeNull();
    // The static guide is still there.
    expect(screen.getByText('Two ways to filter')).toBeInTheDocument();
    // Not asked again behind the reader's back (no automatic retry).
    expect(api.getHostQuerySchema).toHaveBeenCalledTimes(1);
  });

  // DEFECT (TriageGuide.tsx:37-45): EVERY failure prints "Select a project to
  // load the live field list" — also a 500 or a network error with a project
  // selected, where selecting a project changes nothing — and there is no
  // Retry (UI_STYLE_GUIDE §45: what could not be loaded is said where it would
  // have been shown, with Retry).  This is the correct behaviour; it fails
  // today.
  it('with a project selected, a failed read is not blamed on having none, and can be retried', async () => {
    api.getHostQuerySchema.mockRejectedValue({ response: { status: 500, data: { detail: 'schema unavailable' } } });
    show();
    await screen.findByText(/Every field is also listed in/);

    expect(screen.queryByText(/Select a project/)).toBeNull();
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
  });

  it('with no project selected it says to select one, and shows no table', async () => {
    project.current = null;
    // What the API function does for NO_PROJECT (0).
    api.getHostQuerySchema.mockImplementation(async (projectId: number) => {
      if (!projectId) throw new Error('No project selected');
      return SCHEMA;
    });
    show();

    expect(await screen.findByText(/Select a project to load the live field list/)).toBeInTheDocument();
    expect(screen.queryByRole('table')).toBeNull();
    // Nothing was asked of a real project.
    for (const call of api.getHostQuerySchema.mock.calls) expect(call[0]).toBe(0);
  });
});

describe('TriageGuide — worst-case schema', () => {
  it('a 200-character field name, no aliases and an empty description still make one row', async () => {
    const long = `z${'n'.repeat(199)}`;
    api.getHostQuerySchema.mockResolvedValue({ fields: [field({ name: long })], examples: [] });
    show();
    await screen.findByRole('table');
    expect(fieldRows()).toEqual([`${long}:`]);
  });

  it('no has: field, or one without described values, shows no flags heading', async () => {
    api.getHostQuerySchema.mockResolvedValue({
      fields: [field({ name: 'has', enum_values: ['zweb'], enum_descriptions: {} })], examples: [],
    });
    show();
    await screen.findByRole('table');
    expect(screen.queryByRole('heading', { name: 'has: flags' })).toBeNull();
    expect(flagLines()).toEqual([]);
  });

  it('an empty field list is a table with no rows, not "loading"', async () => {
    api.getHostQuerySchema.mockResolvedValue({ fields: [], examples: [] });
    show();
    await screen.findByRole('table');
    expect(fieldRows()).toEqual([]);
    await waitFor(() => expect(screen.queryByText(LOADING)).toBeNull());
  });

  // ODDITY (TriageGuide.tsx:82-86): a flag value the schema lists without a
  // description prints "has:x — " with nothing after the dash.  Correct: the
  // flag alone, no dangling dash.
  it('a flag without a description is listed without a dangling dash', async () => {
    api.getHostQuerySchema.mockResolvedValue({
      fields: [field({
        name: 'has', enum_values: ['zweb', 'zbare'], enum_descriptions: { zweb: 'has a web interface' },
      })],
      examples: [],
    });
    show();
    await screen.findByRole('heading', { name: 'has: flags' });
    expect(flagLines()).toEqual(['has:zweb — has a web interface', 'has:zbare']);
  });
});
