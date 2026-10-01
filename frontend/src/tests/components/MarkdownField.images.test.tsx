/**
 * MarkdownField — "Insert image": a finding's report section may place the
 * finding's own images ticked "In report".  The button lists them, writes
 * `![caption](evidence:<id>)` where the author was typing, and is disabled
 * with the reason when there is nothing to insert.  A field that is not a
 * finding's (no `images`) has no such button.
 */
import React, { useState } from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import MarkdownField from '../../components/MarkdownField';
import type { MarkdownImages } from '../../utils/reportImages';

const images = (over: Partial<MarkdownImages> = {}): MarkdownImages => ({
  placeable: [
    { id: 57, caption: 'The relayed session', filename: 'relay.png' },
    { id: 58, caption: null, filename: 'banner.png' },
    { id: 59, caption: 'c'.repeat(2000), filename: 'long.png' },
  ],
  urls: { 57: 'blob:fifty-seven' },
  resolver: {
    lookup: (id) => (id === 57 ? { caption: 'The relayed session', src: 'blob:fifty-seven' } : null),
    ensure: vi.fn(),
  },
  ...over,
});

const Field: React.FC<{ initial?: string; images?: MarkdownImages; onValue?: (v: string) => void }> = ({
  initial = '', images: imgs, onValue,
}) => {
  const [value, setValue] = useState(initial);
  return (
    <MarkdownField id="rt-description" label="Description" value={value} images={imgs}
      onChange={(v) => { setValue(v); onValue?.(v); }} />
  );
};

describe('MarkdownField — Insert image', () => {
  it('has no image button on a field that is not a finding’s', () => {
    render(<Field />);
    expect(screen.queryByRole('button', { name: 'Insert image' })).not.toBeInTheDocument();
  });

  it('is disabled, with the reason, when the finding has no ticked image', () => {
    render(<Field images={images({ placeable: [] })} />);
    const button = screen.getByRole('button', { name: 'Insert image' });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription(/No image to insert: attach one to a comment below and tick “In report”/);
    expect(button.parentElement).toHaveAttribute('title', expect.stringContaining('No image to insert'));
  });

  it('lists the finding’s ticked images and writes the reference where the author was typing', () => {
    const onValue = vi.fn();
    const imgs = images();
    render(<Field initial={'First paragraph.\n\nSecond paragraph.'} images={imgs} onValue={onValue} />);
    const area = screen.getByRole('textbox') as HTMLTextAreaElement;
    area.setSelectionRange(16, 16);   // the end of the first paragraph

    fireEvent.click(screen.getByRole('button', { name: 'Insert image' }));
    const list = screen.getByRole('list', { name: 'Images to insert' });
    // Thumbnails are asked for when the picker opens — only these ids.
    expect((imgs.resolver.ensure as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0])).toEqual([57, 58, 59]);
    expect(within(list).getAllByRole('listitem')).toHaveLength(3);
    expect(within(list).getByText('No caption — banner.png')).toBeInTheDocument();
    expect(list.querySelector('img')?.getAttribute('src')).toBe('blob:fifty-seven');

    fireEvent.click(within(list).getByRole('button', { name: 'Insert image 57: The relayed session' }));
    // On a line of its own, between the paragraphs: a figure is a block.
    expect(onValue).toHaveBeenLastCalledWith(
      'First paragraph.\n\n![The relayed session](evidence:57)\n\nSecond paragraph.',
    );
    expect(screen.queryByRole('list', { name: 'Images to insert' })).not.toBeInTheDocument();
  });

  it('inserts an uncaptioned image with an empty caption, and a long one cut for the source', () => {
    const onValue = vi.fn();
    render(<Field images={images()} onValue={onValue} />);
    fireEvent.click(screen.getByRole('button', { name: 'Insert image' }));
    fireEvent.click(screen.getByRole('button', { name: 'Insert image 58: banner.png' }));
    expect(onValue).toHaveBeenLastCalledWith('![](evidence:58)');
    fireEvent.click(screen.getByRole('button', { name: 'Insert image' }));
    fireEvent.click(screen.getByRole('button', { name: /^Insert image 59:/ }));
    const written = onValue.mock.calls[onValue.mock.calls.length - 1][0] as string;
    expect(written).toMatch(/!\[c{119}…\]\(evidence:59\)$/);
  });

  it('previews a placed image as the image, and an unknown reference as a note', () => {
    render(<Field initial={'![](evidence:57)\n\n![gone](evidence:404)'} images={images()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    const preview = screen.getByTestId('rt-description-preview');
    expect(preview.querySelector('img')?.getAttribute('src')).toBe('blob:fifty-seven');
    expect(within(preview).getByRole('note')).toHaveTextContent('Image not available: evidence:404');
    // The picker is for writing.
    expect(screen.getByRole('button', { name: 'Insert image' })).toBeDisabled();
  });
});
