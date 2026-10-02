/**
 * Review 2026-10-01 M8 — the lightbox caption is an unbounded value: an
 * image's caption runs to 2,000 characters.  It wraps at words, is clamped to
 * a few lines, and opens on request in a box that scrolls.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import ScreenshotLightbox from '../../components/ScreenshotLightbox';

const show = (caption?: string) =>
  render(<ScreenshotLightbox open onClose={vi.fn()} src="blob:x" caption={caption} />);

describe('ScreenshotLightbox — caption', () => {
  it('clamps a 2,000-character caption and wraps it as prose, with the rest on request', () => {
    const long = `${'The relayed session reached the domain controller. '.repeat(40)}`.slice(0, 2000);
    show(long);
    const caption = screen.getByTestId('lightbox-caption');
    expect(caption).toHaveTextContent(long.trim());
    expect(caption.className).toContain('line-clamp-3');
    expect(caption.className).toContain('[overflow-wrap:anywhere]');
    expect(caption.className).not.toContain('break-all');
    expect(caption.className).not.toContain('font-mono');

    const toggle = screen.getByRole('button', { name: 'Show the whole caption' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    // Open, it scrolls inside its own box: the picture keeps its room.
    expect(caption.className).not.toContain('line-clamp-3');
    expect(caption.className).toContain('max-h-[30vh]');
    expect(caption.className).toContain('overflow-y-auto');
    fireEvent.click(screen.getByRole('button', { name: 'Show less' }));
    expect(caption.className).toContain('line-clamp-3');
    // The dialog's name is announced whole, so it is cut.
    expect(screen.getByRole('dialog').getAttribute('aria-label')!.length).toBeLessThanOrEqual(120);
  });

  it('offers nothing to open on a short caption, and names the dialog without one', () => {
    const { unmount } = show('https://10.0.0.5:8443 — Login');
    expect(screen.getByTestId('lightbox-caption')).toHaveTextContent('https://10.0.0.5:8443 — Login');
    expect(screen.queryByRole('button', { name: 'Show the whole caption' })).toBeNull();
    expect(screen.getByRole('dialog')).toHaveAttribute('aria-label', 'https://10.0.0.5:8443 — Login');
    unmount();
    show();
    expect(screen.queryByTestId('lightbox-caption')).toBeNull();
    expect(screen.getByRole('dialog')).toHaveAttribute('aria-label', 'Screenshot');
  });
});
