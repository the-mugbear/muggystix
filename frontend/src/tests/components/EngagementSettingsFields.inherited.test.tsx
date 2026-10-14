/**
 * Where each engagement detail comes from (owner, 2026-10-14: "Every detail
 * follows the project unless the report overrides it").
 *
 * - A DRAFT (`followed` given): its `value` is the override map.  A followed
 *   detail shows the project's value, greyed, "From the project's defaults",
 *   with "Override for this report"; an overridden one is the report's to
 *   edit, with "Revert to the project's".  Lists are overridden as a whole,
 *   and may be overridden to empty.
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

beforeEach(() => {
  vi.clearAllMocks();
  installBlobUrls();
  URL.revokeObjectURL = vi.fn();
  api.listReportTextImages.mockResolvedValue([]);
  api.getNoteAttachmentBlob.mockImplementation((_project: number, id: number) => Promise.resolve(blobFor(`blob:image-${id}`)));
});

describe('A draft — every detail follows the project unless overridden', () => {
  it('a new draft overrides nothing: each detail shows the project’s, greyed, and says so', () => {
    render(<Harness initial={{}} followed={PROJECT} />);
    const client = screen.getByLabelText('Client');
    expect(client).toHaveValue('Example Corp');
    expect(client).toBeDisabled();
    expect(client).toHaveAccessibleDescription(/From the project’s defaults/);
    expect(screen.getByLabelText('Author')).toHaveValue('Project Team');
    expect(screen.getByLabelText('Classification')).toHaveValue('Confidential');
    expect(screen.getByLabelText('System description')).toHaveValue('The estate.');
    expect(screen.getByLabelText('Disclaimer')).toHaveValue(DISCLAIMER);
    // Nothing set in the project's defaults: said in the empty field.
    expect(screen.getByLabelText('Engagement type')).toHaveAttribute('placeholder', 'Not set in the project’s defaults');
    // The lists: the project's rows, not editable, no add control.
    expect(screen.getByLabelText('Team member 1 name')).toHaveValue('Ana Lead');
    expect(screen.getByLabelText('Team member 1 name')).toBeDisabled();
    expect(screen.getByLabelText('Recipient 1 name')).toHaveValue('CISO');
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
    const client = screen.getByLabelText('Client');
    expect(client).toBeEnabled();
    expect(client).toHaveAccessibleDescription(/Overridden for this report/);
    fireEvent.change(client, { target: { value: 'Other Corp' } });
    expect(lastChange()).toEqual({ client_name: 'Other Corp' });
    // The others still follow.
    expect(screen.getByLabelText('Classification')).toBeDisabled();
  });

  it('Revert follows the project again: the key leaves the override map', () => {
    render(<Harness initial={{ client_name: 'Other Corp', classification: 'Secret' }} followed={PROJECT} />);
    expect(screen.getByLabelText('Client')).toHaveValue('Other Corp');
    expect(screen.getByLabelText('Client')).toBeEnabled();
    fireEvent.click(button('Revert Client to the project’s'));
    expect(lastChange()).toEqual({ classification: 'Secret' });
    expect(screen.getByLabelText('Client')).toHaveValue('Example Corp');
    expect(screen.getByLabelText('Client')).toBeDisabled();
    expect(button('Override Client for this report')).toBeInTheDocument();
  });

  it('an override emptied while typing stays one, and says it will follow the project once saved', () => {
    render(<Harness initial={{ client_name: 'Other Corp' }} followed={PROJECT} />);
    fireEvent.change(screen.getByLabelText('Client'), { target: { value: '' } });
    expect(lastChange()).toEqual({ client_name: '' });
    expect(screen.getByLabelText('Client')).toBeEnabled();
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
    expect(screen.getByLabelText('Team member 1 name')).toHaveValue('Ana Lead');
  });

  it('a change of the project’s defaults shows on a following detail, never on an overridden one', () => {
    const view = render(<Harness initial={{ classification: 'Secret' }} followed={PROJECT} />);
    expect(screen.getByLabelText('Client')).toHaveValue('Example Corp');
    view.rerender(
      <MemoryRouter>
        <EngagementSettingsFields idPrefix="t" value={{ classification: 'Secret' }} members={[]} onChange={onChange}
          followed={{ ...PROJECT, client_name: 'Renamed Corp', classification: 'Public', distribution: [] }} />
      </MemoryRouter>,
    );
    expect(screen.getByLabelText('Client')).toHaveValue('Renamed Corp');
    expect(screen.getByLabelText('Classification')).toHaveValue('Secret');
    expect(screen.queryByLabelText('Recipient 1 name')).toBeNull();
  });

  it('a disclaimer the project leaves to the template says so', () => {
    render(<Harness initial={{}} followed={{ ...PROJECT, disclaimer: null }} />);
    expect(screen.getByText('The project sets none: the template’s own disclaimer is printed.')).toBeInTheDocument();
    fireEvent.click(button('Override Disclaimer for this report'));
    expect(lastChange()).toEqual({ disclaimer: '' });
    expect(screen.queryByText(/The project sets none/)).toBeNull();
  });

  it('a long followed value is shown in its field, which bounds it', () => {
    render(<Harness initial={{}} followed={{ ...PROJECT, author_name: LONG_AUTHOR }} />);
    expect(screen.getByLabelText('Author')).toHaveValue(LONG_AUTHOR);
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
    expect(insert).toHaveAccessibleDescription('No image to insert: upload one below.');
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
    expect(systemToolbar().queryByRole('button', { name: 'Insert image' })).toBeNull();
    fireEvent.click(button('Override System description for this report'));
    expect(systemToolbar().getByRole('button', { name: 'Insert image' })).toBeEnabled();
  });
});
