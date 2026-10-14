/**
 * The report's Author and Disclaimer are inherited when left empty (the
 * project's, else the installation's), and the field says what the report
 * will then print; the System description places the project's own report
 * images with the reference a finding's images use.  An issued report
 * (`readOnly`) shows what it holds and nothing else.
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

import type { EngagementSettings, InheritedReportDetails } from '../../services/api';
import EngagementSettingsFields, { cleanSettings } from '../../components/reports/EngagementSettingsFields';
import { blobFor, installBlobUrls } from '../helpers/blobUrls';

const LONG_AUTHOR = 'Example-Security-Consulting-International-'.repeat(6);
const DISCLAIMER = 'This report is **confidential**.\n\nIt reflects the systems as tested.';
const settings = (over: Partial<EngagementSettings> = {}): EngagementSettings => ({
  client_name: 'Acme', classification: null, engagement_type: null, testers: [], distribution: [],
  system_description: null, author_name: null, disclaimer: null, ...over,
});

const onChange = vi.fn();
interface HarnessProps {
  initial?: EngagementSettings;
  inherited?: InheritedReportDetails | null;
  inheritedFrom?: 'installation' | 'project';
  readOnly?: boolean;
}
const Harness: React.FC<HarnessProps> = ({ initial = settings(), inherited, inheritedFrom, readOnly }) => {
  const [value, setValue] = useState(initial);
  return (
    <MemoryRouter>
      <EngagementSettingsFields idPrefix="t" value={value} members={[]} readOnly={readOnly}
        inherited={inherited} inheritedFrom={inheritedFrom}
        onChange={(next) => { onChange(next); setValue(next); }} />
    </MemoryRouter>
  );
};
const lastChange = () => onChange.mock.calls[onChange.mock.calls.length - 1][0] as EngagementSettings;

beforeEach(() => {
  vi.clearAllMocks();
  installBlobUrls();
  URL.revokeObjectURL = vi.fn();
  api.listReportTextImages.mockResolvedValue([]);
  api.getNoteAttachmentBlob.mockImplementation((_project: number, id: number) => Promise.resolve(blobFor(`blob:image-${id}`)));
});

describe('Author', () => {
  it('is a field beside Client, and what is typed is the report’s own', () => {
    render(<Harness />);
    const author = screen.getByLabelText('Author');
    expect(author).toHaveAttribute('placeholder', 'e.g. Example Security Ltd');
    expect(author).toHaveAttribute('maxlength', '255');
    fireEvent.change(author, { target: { value: 'Red Team Ltd' } });
    expect(lastChange().author_name).toBe('Red Team Ltd');
    fireEvent.change(screen.getByLabelText('Author'), { target: { value: '' } });
    expect(lastChange().author_name).toBeNull();
  });

  it('empty, it says which inherited name the report will print — in the words of where it comes from', () => {
    const first = render(<Harness inherited={{ author_name: 'Example Security Ltd', disclaimer: null }} />);
    expect(screen.getByText('Empty: uses the installation’s — “Example Security Ltd”')).toBeInTheDocument();
    expect(screen.getByLabelText('Author')).toHaveAccessibleDescription(/uses the installation’s/);
    first.unmount();

    render(<Harness inheritedFrom="project" inherited={{ author_name: LONG_AUTHOR }} />);
    const note = screen.getByText(`Empty: uses the project’s or installation’s default — “${LONG_AUTHOR}”`);
    // Any length: clamped, with the whole of it on hover.
    expect(note.className).toContain('line-clamp-2');
    expect(note).toHaveAttribute('title', LONG_AUTHOR);
  });

  it('says nothing once the report writes its own, or when nothing is inherited', () => {
    const first = render(<Harness initial={settings({ author_name: 'Ours' })} inherited={{ author_name: 'Example Security Ltd' }} />);
    expect(screen.queryByText(/Empty: uses/)).toBeNull();
    first.unmount();
    render(<Harness inherited={{ author_name: null, disclaimer: null }} />);
    expect(screen.queryByText(/Empty: uses/)).toBeNull();
  });
});

describe('Disclaimer', () => {
  it('is Markdown, after the other targets', () => {
    render(<Harness />);
    const box = screen.getByLabelText('Disclaimer');
    expect(screen.getByRole('toolbar', { name: 'Disclaimer formatting' })).toBeInTheDocument();
    expect(screen.getByLabelText('Other targets').compareDocumentPosition(box) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.change(box, { target: { value: 'Ours.' } });
    expect(lastChange().disclaimer).toBe('Ours.');
  });

  it('empty with nothing inherited: the template’s own is printed', () => {
    render(<Harness inherited={{ author_name: null, disclaimer: null }} />);
    expect(screen.getByText('Empty: the template’s own disclaimer is printed.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit a copy' })).toBeNull();
  });

  it('empty with an inherited one: shows it on request, rendered, and offers a copy to edit', () => {
    render(<Harness inherited={{ disclaimer: DISCLAIMER }} />);
    const inherited = screen.getByTestId('t-disclaimer-inherited');
    expect(within(inherited).getByText('Empty: uses the installation’s disclaimer. Show it')).toBeInTheDocument();
    expect(inherited.querySelector('strong')?.textContent).toBe('confidential');
    expect(inherited.querySelector('.overflow-y-auto')).not.toBeNull();
    expect(screen.queryByText(/the template’s own disclaimer/)).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Edit a copy' }));
    expect(lastChange().disclaimer).toBe(DISCLAIMER);
    expect(screen.getByLabelText('Disclaimer')).toHaveValue(DISCLAIMER);
    // It is the report's own now: nothing is inherited, and no copy is offered.
    expect(screen.queryByTestId('t-disclaimer-inherited')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Edit a copy' })).toBeNull();
  });

  it('a draft names the project too', () => {
    render(<Harness inheritedFrom="project" inherited={{ disclaimer: DISCLAIMER }} />);
    expect(screen.getByText('Empty: uses the project’s or installation’s default disclaimer. Show it')).toBeInTheDocument();
  });
});

describe('An issued report (readOnly)', () => {
  it('shows what it was issued with: the inherited author and disclaimer as its values, no note, no copy, no image list — and asks for no images', () => {
    render(<Harness readOnly inherited={{ author_name: 'Example Security Ltd', disclaimer: DISCLAIMER }} />);
    expect(screen.getByLabelText('Author')).toBeDisabled();
    // Its own are empty: it printed what it inherited when it was issued.
    expect(screen.getByLabelText('Author')).toHaveValue('Example Security Ltd');
    expect(screen.getByLabelText('Disclaimer')).toBeDisabled();
    expect(screen.getByLabelText('Disclaimer')).toHaveValue(DISCLAIMER);
    expect(screen.queryByText(/Empty:/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Edit a copy' })).toBeNull();
    expect(screen.queryByTestId('t-system-images')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Upload image' })).toBeNull();
    expect(within(screen.getByRole('toolbar', { name: 'System description formatting' }))
      .queryByRole('button', { name: 'Insert image' })).toBeNull();
    expect(api.listReportTextImages).not.toHaveBeenCalled();
  });

  it('shows its own author and disclaimer', () => {
    render(<Harness readOnly initial={settings({ author_name: 'Red Team Ltd', disclaimer: 'Ours.' })} />);
    expect(screen.getByLabelText('Author')).toHaveValue('Red Team Ltd');
    expect(screen.getByLabelText('Disclaimer')).toHaveValue('Ours.');
  });
});

describe('cleanSettings', () => {
  it('keeps the author and the disclaimer', () => {
    const cleaned = cleanSettings(settings({
      author_name: 'Red Team Ltd', disclaimer: 'Ours.', testers: [{ name: '  ', role: null, email: null }],
    }));
    expect(cleaned).toMatchObject({ author_name: 'Red Team Ltd', disclaimer: 'Ours.', testers: [] });
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
    const button = systemToolbar().getByRole('button', { name: 'Insert image' });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription('No image to insert: upload one below.');
  });

  it('no other field of the form takes images', async () => {
    render(<Harness />);
    await screen.findByText('No image uploaded yet.');
    expect(within(screen.getByRole('toolbar', { name: 'Disclaimer formatting' }))
      .queryByRole('button', { name: 'Insert image' })).toBeNull();
  });
});
