/**
 * Where each engagement detail comes from (owner, 2026-10-14: "Every detail
 * follows the project unless the report overrides it").
 *
 * - A DRAFT (`followed` given): its `value` is the override map.  A followed
 *   detail shows the project's value as text, "From the project's defaults",
 *   with "Override for this report"; an overridden one is the report's to
 *   edit, with "Revert to the project's".  Lists are overridden as a whole,
 *   and may be overridden to empty.  One action per line is visible, the
 *   others are in its "⋯" menu.
 * - The project's defaults (no `followed`): plain editing; an empty author or
 *   disclaimer says it uses the installation's.
 * - `readOnly` (an issued report): its values, no action.
 *
 * The System description places the project's own report images with the
 * reference a finding's images use.
 */
import React, { useState } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  getProjectReportTeam: vi.fn(),
  listReportTextImages: vi.fn(),
  uploadReportTextImage: vi.fn(),
  getNoteAttachmentBlob: vi.fn(),
  setNoteAttachmentCaption: vi.fn(),
  deleteNoteAttachment: vi.fn(),
}));
vi.mock('../../services/api', () => api);
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => ({ currentProject: { id: 1, name: 'P' } }) }));

import type { EngagementOverrides, EngagementSettings, InheritedReportDetails } from '../../services/api';
import EngagementSettingsFields, { cleanSettings, isOverridden } from '../../components/reports/EngagementSettingsFields';
import { blobFor, installBlobUrls } from '../helpers/blobUrls';

const LONG_AUTHOR = 'Example-Security-Consulting-International-'.repeat(6);
const DISCLAIMER = 'This report is **confidential**.\n\nIt reflects the systems as tested.';
const settings = (over: Partial<EngagementSettings> = {}): EngagementSettings => ({
  client_name: 'Acme', classification: null, engagement_type: null, testers: [], distribution: [],
  system_description: null, author_name: null, disclaimer: null, ...over,
});
const ANA = { user_id: 3, name: 'Ana Lead', role: 'Engagement lead', email: 'ana@example.com' };
const CISO = { name: 'CISO', email: 'ciso@example.com' };
/** What a draft of the project follows. */
const PROJECT = settings({
  client_name: 'Example Corp', classification: 'Confidential', author_name: 'Project Team',
  disclaimer: DISCLAIMER, testers: [ANA], distribution: [CISO], system_description: 'The estate.',
});

const onChange = vi.fn();
interface HarnessProps {
  initial?: EngagementOverrides;
  followed?: EngagementSettings | null;
  inherited?: InheritedReportDetails | null;
  readOnly?: boolean;
}
const Harness: React.FC<HarnessProps> = ({ initial = settings(), followed, inherited, readOnly }) => {
  const [value, setValue] = useState<EngagementOverrides>(initial);
  return (
    <MemoryRouter>
      <EngagementSettingsFields idPrefix="t" value={value} members={[]} readOnly={readOnly}
        followed={followed} inherited={inherited}
        onChange={(next) => { onChange(next); setValue(next); }} />
    </MemoryRouter>
  );
};
const lastChange = () => onChange.mock.calls[onChange.mock.calls.length - 1][0] as EngagementOverrides;
const button = (name: string) => screen.getByRole('button', { name });
/** A detail's "⋯" menu, opened (Radix opens a menu on a key, not a click, in jsdom). */
const openMore = async (label: string) => {
  fireEvent.keyDown(button(`More actions for ${label}`), { key: 'Enter' });
  return screen.findByRole('menu');
};
/** Choose `name` in the "⋯" menu of the detail `label`. */
const fromMenu = async (label: string, name: string) => {
  const menu = await openMore(label);
  fireEvent.click(within(menu).getByRole('menuitem', { name }));
};
/** The buttons on a detail's source line, in order (the "⋯" trigger included). */
const visibleActions = (key: string) =>
  within(document.getElementById(`t-${key}-source`)!).getAllByRole('button')
    .map((b) => b.getAttribute('aria-label'));

beforeEach(() => {
  vi.clearAllMocks();
  installBlobUrls();
  URL.revokeObjectURL = vi.fn();
  api.listReportTextImages.mockResolvedValue([]);
  api.getNoteAttachmentBlob.mockImplementation((_project: number, id: number) => Promise.resolve(blobFor(`blob:image-${id}`)));
});

const textbox = (name: string) => screen.getByRole('textbox', { name });
const followedValue = (name: string) => screen.getByRole('group', { name });

describe('A draft — every detail follows the project unless overridden', () => {
  it('a new draft overrides nothing: each detail is the project’s value as text, and says where it comes from', () => {
    render(<Harness initial={{}} followed={PROJECT} />);
    // Text, not a greyed field (owner, 2026-10-14: a disabled box read as "locked").
    expect(screen.queryByRole('textbox', { name: 'Client' })).toBeNull();
    const client = followedValue('Client');
    expect(client).toHaveTextContent('Example Corp');
    expect(client).toHaveAccessibleDescription(/From the project’s defaults/);
    expect(followedValue('Author')).toHaveTextContent('Project Team');
    expect(followedValue('Classification')).toHaveTextContent('Confidential');
    expect(followedValue('System description')).toHaveTextContent('The estate.');
    // Markdown is rendered, in a bounded box that scrolls.
    const disclaimer = followedValue('Disclaimer');
    expect(disclaimer.querySelector('strong')?.textContent).toBe('confidential');
    expect(disclaimer.className).toContain('max-h-48');
    expect(disclaimer.className).toContain('overflow-y-auto');
    // Nothing set in the project's defaults: said where the value would be.
    expect(followedValue('Engagement type')).toHaveTextContent('Not set in the project’s defaults');
    // The lists: the project's rows as text, no field, no add control.
    expect(screen.getByTestId('t-testers-followed')).toHaveTextContent('Ana Lead — Engagement lead · ana@example.com');
    expect(screen.queryByLabelText('Team member 1 name')).toBeNull();
    expect(screen.getByTestId('t-distribution-followed')).toHaveTextContent('CISO · ciso@example.com');
    expect(screen.queryByRole('button', { name: /Someone else/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Add recipient/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Move .* up/ })).toBeNull();
    // Every detail offers to be overridden; none to be reverted.
    for (const label of ['Client', 'Author', 'Classification', 'Engagement type', 'Assessment team', 'Distribution',
      'System description', 'Application URLs and API endpoints', 'Thick client applications', 'Other targets',
      'Disclaimer']) {
      expect(button(`Override ${label} for this report`)).toHaveTextContent('Override for this report');
    }
    expect(screen.queryByRole('button', { name: /^Revert/ })).toBeNull();
    // The defaults' wording about the installation is not a draft's.
    expect(screen.queryByText(/Empty: uses/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Edit a copy' })).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('Override makes an editable copy of the project’s value — the report’s own from then on', () => {
    render(<Harness initial={{}} followed={PROJECT} />);
    fireEvent.click(button('Override Client for this report'));
    expect(lastChange()).toEqual({ client_name: 'Example Corp' });
    const client = textbox('Client');
    expect(client).toBeEnabled();
    expect(client).toHaveAccessibleDescription(/Overridden for this report/);
    fireEvent.change(client, { target: { value: 'Other Corp' } });
    expect(lastChange()).toEqual({ client_name: 'Other Corp' });
    // The others still follow.
    expect(screen.queryByRole('textbox', { name: 'Classification' })).toBeNull();
  });

  it('Revert follows the project again: the key leaves the override map', () => {
    render(<Harness initial={{ client_name: 'Other Corp', classification: 'Secret' }} followed={PROJECT} />);
    expect(textbox('Client')).toHaveValue('Other Corp');
    fireEvent.click(button('Revert Client to the project’s'));
    expect(lastChange()).toEqual({ classification: 'Secret' });
    expect(followedValue('Client')).toHaveTextContent('Example Corp');
    expect(screen.queryByRole('textbox', { name: 'Client' })).toBeNull();
    expect(button('Override Client for this report')).toBeInTheDocument();
  });

  it('an override emptied while typing stays one, and says it will follow the project once saved', () => {
    render(<Harness initial={{ client_name: 'Other Corp' }} followed={PROJECT} />);
    fireEvent.change(textbox('Client'), { target: { value: '' } });
    expect(lastChange()).toEqual({ client_name: '' });
    expect(textbox('Client')).toBeEnabled();
    expect(screen.getByText('Overridden for this report — empty: saved, it follows the project’s again')).toBeInTheDocument();
  });

  it('a list is overridden as a whole — and may be overridden to empty', () => {
    render(<Harness initial={{}} followed={PROJECT} />);
    fireEvent.click(button('Override Distribution for this report'));
    expect(lastChange()).toEqual({ distribution: [CISO] });
    // A copy: the project's rows are not shared with the form.
    expect(lastChange().distribution?.[0]).not.toBe(PROJECT.distribution[0]);
    fireEvent.click(button('Remove CISO'));
    expect(lastChange()).toEqual({ distribution: [] });
    expect(isOverridden(lastChange(), 'distribution')).toBe(true);
    // Empty on purpose, and still the report's own (not the project's again).
    expect(screen.queryByLabelText('Recipient 1 name')).toBeNull();
    expect(screen.getByText('Nobody listed yet.')).toBeInTheDocument();
    expect(button('Revert Distribution to the project’s')).toBeInTheDocument();
    fireEvent.click(button('Add recipient'));
    expect(lastChange().distribution).toEqual([{ name: '', email: null }]);
  });

  it('an assessment team overridden to empty shows nobody, and the project’s team comes back on Revert', () => {
    render(<Harness initial={{ testers: [] }} followed={PROJECT} />);
    expect(screen.queryByLabelText('Team member 1 name')).toBeNull();
    expect(screen.getByRole('button', { name: /Someone else/ })).toBeInTheDocument();
    fireEvent.click(button('Revert Assessment team to the project’s'));
    expect(lastChange()).toEqual({});
    expect(screen.getByTestId('t-testers-followed')).toHaveTextContent('Ana Lead');
  });

  it('a change of the project’s defaults shows on a following detail, never on an overridden one', () => {
    const view = render(<Harness initial={{ classification: 'Secret' }} followed={PROJECT} />);
    expect(followedValue('Client')).toHaveTextContent('Example Corp');
    view.rerender(
      <MemoryRouter>
        <EngagementSettingsFields idPrefix="t" value={{ classification: 'Secret' }} members={[]} onChange={onChange}
          followed={{ ...PROJECT, client_name: 'Renamed Corp', classification: 'Public', distribution: [] }} />
      </MemoryRouter>,
    );
    expect(followedValue('Client')).toHaveTextContent('Renamed Corp');
    expect(textbox('Classification')).toHaveValue('Secret');
    expect(screen.getByTestId('t-distribution-followed')).toHaveTextContent('Not set in the project’s defaults');
  });

  it('a disclaimer the project leaves to the template says so', () => {
    render(<Harness initial={{}} followed={{ ...PROJECT, disclaimer: null }} />);
    expect(followedValue('Disclaimer')).toHaveTextContent('The project sets none: the template’s own disclaimer is printed.');
    fireEvent.click(button('Override Disclaimer for this report'));
    expect(lastChange()).toEqual({ disclaimer: '' });
    expect(screen.queryByText(/The project sets none/)).toBeNull();
  });

  it('a long followed value wraps inside a bounded box that scrolls', () => {
    render(<Harness initial={{}} followed={{ ...PROJECT, author_name: LONG_AUTHOR }} />);
    const author = followedValue('Author');
    expect(author).toHaveTextContent(LONG_AUTHOR);
    expect(author.className).toContain('max-h-24');
    expect(author.className).toContain('overflow-y-auto');
    expect(author.querySelector('p')?.className).toContain('[overflow-wrap:anywhere]');
  });
});

describe('A draft — a change pushed up to the project (owner, 2026-10-14)', () => {
  const onSet = vi.fn();
  const onPromote = vi.fn();
  const Pushing: React.FC<HarnessProps & {
    from?: Partial<Record<keyof EngagementSettings, 'project' | 'installation' | 'project_team' | null>>;
  }> = ({ initial = {}, followed = PROJECT, from }) => {
    const [value, setValue] = useState<EngagementOverrides>(initial);
    return (
      <MemoryRouter>
        <EngagementSettingsFields idPrefix="t" value={value} members={[]} followed={followed} followedFrom={from}
          onSetForProject={onSet} onPromote={onPromote}
          onChange={(next) => { onChange(next); setValue(next); }} />
      </MemoryRouter>
    );
  };
  beforeEach(() => {
    onSet.mockReset().mockResolvedValue(undefined);
    onPromote.mockReset();
  });

  it('one visible action per detail; the others in its “⋯” menu (owner, 2026-10-14)', async () => {
    render(<Pushing initial={{ client_name: 'Other Corp' }} followed={{ ...PROJECT, engagement_type: null }}
      from={{ client_name: 'project', classification: 'project', engagement_type: null,
        author_name: 'installation', testers: 'project_team' }} />);
    // Overridden: Revert; the menu makes it the project's default.
    expect(visibleActions('client_name')).toEqual(['Revert Client to the project’s', 'More actions for Client']);
    expect(button('Revert Client to the project’s')).toHaveTextContent('Revert to the project’s');
    let menu = await openMore('Client');
    expect(within(menu).getAllByRole('menuitem').map((m) => m.textContent)).toEqual(['Make this the project’s default']);
    fireEvent.keyDown(menu, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
    // Followed, the project holds a value: Override; the menu edits the default.
    expect(visibleActions('classification'))
      .toEqual(['Override Classification for this report', 'More actions for Classification']);
    menu = await openMore('Classification');
    expect(within(menu).getByRole('menuitem', { name: 'Edit Classification in the project’s defaults' }))
      .toHaveTextContent('Edit the project’s default');
    fireEvent.keyDown(menu, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
    // Followed, empty in the project: Set for the project; the menu overrides.
    expect(visibleActions('engagement_type'))
      .toEqual(['Set Engagement type for the project', 'More actions for Engagement type']);
    // From the installation or the project's members: Override; the menu sets it for the project.
    expect(visibleActions('author_name')).toEqual(['Override Author for this report', 'More actions for Author']);
    expect(visibleActions('testers'))
      .toEqual(['Override Assessment team for this report', 'More actions for Assessment team']);
    // The source words stay on the line.
    expect(document.getElementById('t-classification-source')).toHaveTextContent('From the project’s defaults');
    expect(document.getElementById('t-author_name-source')).toHaveTextContent('From the installation’s defaults');
  });

  it('an overridden detail offers “Make this the project’s default” with the value shown — edited or not', async () => {
    render(<Pushing initial={{ client_name: 'Other Corp', distribution: [] }} />);
    fireEvent.change(textbox('Client'), { target: { value: '  Renamed Corp ' } });
    await fromMenu('Client', 'Make Client the project’s default');
    await waitFor(() => expect(onPromote).toHaveBeenCalledWith('client_name', 'Renamed Corp'));
    // A list as a whole, even empty.
    await fromMenu('Distribution', 'Make Distribution the project’s default');
    await waitFor(() => expect(onPromote).toHaveBeenLastCalledWith('distribution', []));
    // An emptied text has nothing to push up: no menu at all, Revert alone.
    fireEvent.change(textbox('Client'), { target: { value: ' ' } });
    expect(visibleActions('client_name')).toEqual(['Revert Client to the project’s']);
  });

  it('a followed detail the project leaves empty: “Set for the project” first, edited here, saved to the project', async () => {
    const followed = { ...PROJECT, engagement_type: null };
    render(<Pushing followed={followed} from={{ engagement_type: null, client_name: 'project' }} />);
    expect(followedValue('Engagement type')).toHaveTextContent('Not set in the project’s defaults');
    expect(visibleActions('engagement_type'))
      .toEqual(['Set Engagement type for the project', 'More actions for Engagement type']);
    const menu = await openMore('Engagement type');
    expect(within(menu).getAllByRole('menuitem').map((m) => m.getAttribute('aria-label')))
      .toEqual(['Override Engagement type for this report']);
    fireEvent.keyDown(menu, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull());

    fireEvent.click(button('Set Engagement type for the project'));
    const field = textbox('Engagement type');
    expect(field).toHaveAccessibleDescription(/Editing the project’s default/);
    // The caret is put in the field (walkthrough 2026-10-14: it opened
    // unfocused and typing went nowhere), and an empty value cannot be saved.
    await waitFor(() => expect(field).toHaveFocus());
    expect(button('Save Engagement type for the project')).toBeDisabled();
    fireEvent.change(field, { target: { value: ' Internal ' } });
    expect(button('Save Engagement type for the project')).toBeEnabled();
    // Not an override: the draft's own map is untouched.
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(button('Save Engagement type for the project'));
    expect(onSet).toHaveBeenCalledWith('engagement_type', 'Internal');
    // Saved: the detail is text again (the page's re-read shows the new value).
    await waitFor(() => expect(screen.queryByRole('textbox', { name: 'Engagement type' })).toBeNull());
  });

  it('Cancel leaves the project as it was; a failed save keeps the edit open', async () => {
    render(<Pushing from={{ classification: 'project' }} />);
    await fromMenu('Classification', 'Edit Classification in the project’s defaults');
    expect(await screen.findByRole('textbox', { name: 'Classification' })).toHaveValue('Confidential');
    // Editing the project's default: Save and Cancel, no menu.
    expect(visibleActions('classification'))
      .toEqual(['Save Classification for the project', 'Cancel editing the project’s Classification']);
    fireEvent.click(button('Cancel editing the project’s Classification'));
    expect(screen.queryByRole('textbox', { name: 'Classification' })).toBeNull();
    expect(onSet).not.toHaveBeenCalled();

    onSet.mockRejectedValueOnce(new Error('refused'));
    await fromMenu('Classification', 'Edit Classification in the project’s defaults');
    fireEvent.click(await screen.findByRole('button', { name: 'Save Classification for the project' }));
    await waitFor(() => expect(onSet).toHaveBeenCalled());
    expect(textbox('Classification')).toBeInTheDocument();
  });

  it('author and disclaimer from the installation say so, and “Set for the project” makes a project value', async () => {
    const followed = { ...PROJECT, author_name: 'Installation Ltd', disclaimer: 'The installation’s terms.' };
    render(<Pushing followed={followed} from={{ author_name: 'installation', disclaimer: 'installation' }} />);
    expect(followedValue('Author')).toHaveAccessibleDescription(/From the installation’s defaults/);
    expect(followedValue('Disclaimer')).toHaveAccessibleDescription(/From the installation’s defaults/);
    await fromMenu('Author', 'Set Author for the project');
    // starts from what it prints
    expect(await screen.findByRole('textbox', { name: 'Author' })).toHaveValue('Installation Ltd');
    fireEvent.change(textbox('Author'), { target: { value: 'Project Team' } });
    fireEvent.click(button('Save Author for the project'));
    expect(onSet).toHaveBeenCalledWith('author_name', 'Project Team');
  });

  it('a team taken from the project’s members says so, and can be set for the project', async () => {
    render(<Pushing from={{ testers: 'project_team' }} />);
    expect(screen.getByRole('group', { name: 'Assessment team' }))
      .toHaveAccessibleDescription(/From the project’s analysts and admins/);
    await fromMenu('Assessment team', 'Set Assessment team for the project');
    expect(await screen.findByLabelText('Team member 1 name')).toHaveValue('Ana Lead');
    fireEvent.click(button('Save Assessment team for the project'));
    expect(onSet).toHaveBeenCalledWith('testers', [ANA]);
  });

  it('none of it for someone who may not write: no push-up actions without the handlers', () => {
    render(<Harness initial={{ client_name: 'Other Corp' }} followed={{ ...PROJECT, engagement_type: null }} />);
    expect(screen.queryByRole('button', { name: /project’s default|for the project|in the project’s defaults/ })).toBeNull();
    // One action each: no "⋯" menu anywhere.
    expect(screen.queryByRole('button', { name: /^More actions/ })).toBeNull();
    // Override and Revert stay.
    expect(button('Override Engagement type for this report')).toBeInTheDocument();
    expect(button('Revert Client to the project’s')).toBeInTheDocument();
  });
});

describe('The project’s defaults — plain editing', () => {
  it('Author is a field beside Client, and what is typed is the defaults’ own', () => {
    render(<Harness />);
    const author = screen.getByLabelText('Author');
    expect(author).toHaveAttribute('placeholder', 'e.g. Example Security Ltd');
    expect(author).toHaveAttribute('maxlength', '255');
    fireEvent.change(author, { target: { value: 'Red Team Ltd' } });
    expect(lastChange().author_name).toBe('Red Team Ltd');
    fireEvent.change(screen.getByLabelText('Author'), { target: { value: '' } });
    expect(lastChange().author_name).toBeNull();
    // No follow / override wording outside a draft.
    expect(screen.queryByText(/project’s defaults/)).toBeNull();
    expect(screen.queryByRole('button', { name: /Override/ })).toBeNull();
  });

  it('an empty author says the installation’s name it will use, clamped', () => {
    const first = render(<Harness inherited={{ author_name: 'Example Security Ltd', disclaimer: null }} />);
    expect(screen.getByText('Empty: uses the installation’s — “Example Security Ltd”')).toBeInTheDocument();
    expect(screen.getByLabelText('Author')).toHaveAccessibleDescription(/uses the installation’s/);
    first.unmount();

    render(<Harness inherited={{ author_name: LONG_AUTHOR }} />);
    const note = screen.getByText(`Empty: uses the installation’s — “${LONG_AUTHOR}”`);
    expect(note.className).toContain('line-clamp-2');
    expect(note).toHaveAttribute('title', LONG_AUTHOR);
  });

  it('says nothing once the defaults write their own, or when the installation has none', () => {
    const first = render(<Harness initial={settings({ author_name: 'Ours' })} inherited={{ author_name: 'Example Security Ltd' }} />);
    expect(screen.queryByText(/Empty: uses/)).toBeNull();
    first.unmount();
    render(<Harness inherited={{ author_name: null, disclaimer: null }} />);
    expect(screen.queryByText(/Empty: uses/)).toBeNull();
  });

  it('the disclaimer is Markdown, after the other targets', () => {
    render(<Harness />);
    const box = screen.getByLabelText('Disclaimer');
    expect(screen.getByRole('toolbar', { name: 'Disclaimer formatting' })).toBeInTheDocument();
    expect(screen.getByLabelText('Other targets').compareDocumentPosition(box) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.change(box, { target: { value: 'Ours.' } });
    expect(lastChange().disclaimer).toBe('Ours.');
  });

  it('an empty disclaimer with nothing from the installation: the template’s own is printed', () => {
    render(<Harness inherited={{ author_name: null, disclaimer: null }} />);
    expect(screen.getByText('Empty: the template’s own disclaimer is printed.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit a copy' })).toBeNull();
  });

  it('an empty disclaimer with the installation’s: shows it on request, rendered, and offers a copy to edit', () => {
    render(<Harness inherited={{ disclaimer: DISCLAIMER }} />);
    const inherited = screen.getByTestId('t-disclaimer-inherited');
    expect(within(inherited).getByText('Empty: uses the installation’s disclaimer. Show it')).toBeInTheDocument();
    expect(inherited.querySelector('strong')?.textContent).toBe('confidential');
    expect(inherited.querySelector('.overflow-y-auto')).not.toBeNull();
    expect(screen.queryByText(/the template’s own disclaimer/)).toBeNull();

    fireEvent.click(button('Edit a copy'));
    expect(lastChange().disclaimer).toBe(DISCLAIMER);
    expect(screen.getByLabelText('Disclaimer')).toHaveValue(DISCLAIMER);
    expect(screen.queryByTestId('t-disclaimer-inherited')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Edit a copy' })).toBeNull();
  });
});

describe('An issued report (readOnly)', () => {
  it('shows what it was issued with: no follow wording, no action, no image list — and asks for no images', () => {
    render(<Harness readOnly initial={settings({ author_name: 'Example Security Ltd', disclaimer: DISCLAIMER })} />);
    expect(screen.getByLabelText('Author')).toBeDisabled();
    expect(screen.getByLabelText('Author')).toHaveValue('Example Security Ltd');
    expect(screen.getByLabelText('Disclaimer')).toBeDisabled();
    expect(screen.getByLabelText('Disclaimer')).toHaveValue(DISCLAIMER);
    expect(screen.queryByText(/Empty:/)).toBeNull();
    expect(screen.queryByText(/project’s defaults|Overridden/)).toBeNull();
    expect(screen.queryByRole('button', { name: /Override|Revert|Edit a copy/ })).toBeNull();
    expect(screen.queryByTestId('t-system-images')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Upload image' })).toBeNull();
    expect(within(screen.getByRole('toolbar', { name: 'System description formatting' }))
      .queryByRole('button', { name: 'Insert image' })).toBeNull();
    expect(api.listReportTextImages).not.toHaveBeenCalled();
  });

  it('issued with no disclaimer: the template’s own was printed', () => {
    render(<Harness readOnly initial={settings()} />);
    expect(screen.getByText('None was written: the template’s own disclaimer was printed.')).toBeInTheDocument();
  });
});

describe('cleanSettings', () => {
  it('keeps the author and the disclaimer, and drops unnamed rows', () => {
    const cleaned = cleanSettings(settings({
      author_name: 'Red Team Ltd', disclaimer: 'Ours.', testers: [{ name: '  ', role: null, email: null }],
    }));
    expect(cleaned).toMatchObject({ author_name: 'Red Team Ltd', disclaimer: 'Ours.', testers: [] });
  });

  it('leaves a list a draft does not override absent, and keeps an empty override', () => {
    expect(cleanSettings({ client_name: 'Ours' })).toEqual({ client_name: 'Ours' });
    expect(cleanSettings({ distribution: [] })).toEqual({ distribution: [] });
    expect(cleanSettings({ distribution: [{ name: ' CISO ', email: null }, { name: ' ', email: null }] }))
      .toEqual({ distribution: [{ name: 'CISO', email: null }] });
  });
});

describe('System description — the project’s own report images', () => {
  const image = (id: number, caption: string | null = null) => ({
    id, filename: `diagram-${id}.png`, caption, content_type: 'image/png', size_bytes: 10, uploaded_by_id: 1,
    created_at: null, placed_in: [], can_modify: true,
  });
  const systemToolbar = () => within(screen.getByRole('toolbar', { name: 'System description formatting' }));

  it('Insert image writes the reference a finding’s images use, where the author was typing', async () => {
    api.listReportTextImages.mockResolvedValue([image(31, 'The assessed network'), image(32)]);
    render(<Harness initial={settings({ system_description: 'The network.\n\nIts servers.' })} />);
    await screen.findByLabelText('Caption for image 31 (diagram-31.png)');
    const area = screen.getByLabelText('System description') as HTMLTextAreaElement;
    area.setSelectionRange(12, 12);

    fireEvent.click(systemToolbar().getByRole('button', { name: 'Insert image' }));
    const list = screen.getByRole('list', { name: 'Images to insert' });
    // The picker speaks of the report's own images, not of a finding's.
    expect(screen.getByText(
      'Images uploaded for the report’s own text, below. An image placed in the text prints there with its caption.',
    )).toBeInTheDocument();
    expect(screen.queryByText(/In report/)).toBeNull();
    fireEvent.click(within(list).getByRole('button', { name: 'Insert image 31: The assessed network' }));
    expect(lastChange().system_description).toBe('The network.\n\n![](evidence:31)\n\nIts servers.');
  });

  it('the preview shows a placed image from the project’s list, and says when a reference is not one of them', async () => {
    api.listReportTextImages.mockResolvedValue([image(31, 'The assessed network')]);
    render(<Harness initial={settings({ system_description: '![](evidence:31)\n\n![](evidence:99)' })} />);
    await screen.findByLabelText('Caption for image 31 (diagram-31.png)');
    fireEvent.click(systemToolbar().getByRole('button', { name: 'Preview' }));
    const preview = screen.getByTestId('t-system-preview');
    await waitFor(() => expect(preview.querySelector('img[src="blob:image-31"]')).not.toBeNull());
    expect(within(preview).getByTestId('evidence-image-31')).toHaveTextContent('Figure: The assessed network');
    expect(within(preview).getByTestId('evidence-missing-99')).toHaveTextContent(
      'Image not available: evidence:99 is not one of the images uploaded for the report’s own text',
    );
    // One fetch of the picture for the form: the list under the field shows the same bytes.
    expect(api.getNoteAttachmentBlob).toHaveBeenCalledTimes(1);
  });

  it('with none uploaded, Insert image is disabled and says where to upload one', async () => {
    render(<Harness />);
    await screen.findByText('No image uploaded yet.');
    const insert = systemToolbar().getByRole('button', { name: 'Insert image' });
    expect(insert).toBeDisabled();
    expect(insert).toHaveAccessibleDescription('No image to insert: paste a screenshot into the text, or upload one below.');
  });

  it('no other field of the form takes images', async () => {
    render(<Harness />);
    await screen.findByText('No image uploaded yet.');
    expect(within(screen.getByRole('toolbar', { name: 'Disclaimer formatting' }))
      .queryByRole('button', { name: 'Insert image' })).toBeNull();
  });

  it('a draft that follows the project’s description inserts nothing into it, and still lists the images', async () => {
    api.listReportTextImages.mockResolvedValue([image(31, 'The assessed network')]);
    render(<Harness initial={{}} followed={PROJECT} />);
    await screen.findByLabelText('Caption for image 31 (diagram-31.png)');
    // Followed: the description is text — no toolbar at all.
    expect(screen.queryByRole('toolbar', { name: 'System description formatting' })).toBeNull();
    fireEvent.click(button('Override System description for this report'));
    expect(systemToolbar().getByRole('button', { name: 'Insert image' })).toBeEnabled();
  });
});
