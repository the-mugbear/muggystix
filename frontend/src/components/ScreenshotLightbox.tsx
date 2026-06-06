import React, { useEffect } from 'react';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { Loader2, X } from 'lucide-react';
import { Alert, AlertDescription } from './ui/alert';
import { cn } from '../utils/cn';

interface ScreenshotLightboxProps {
  open: boolean;
  onClose: () => void;
  // Pre-fetched blob URL.  Parent is responsible for fetching + revoking
  // (typical pattern: fetchWebInterfaceScreenshot() inside useEffect).
  src: string | null;
  loading?: boolean;
  error?: string | null;
  // Context line shown above the image — usually the interface URL
  // or "https://host:port — title".
  caption?: string;
}

/**
 * Full-size screenshot overlay for the HostDetail "Web Interfaces"
 * card.  Dismissed by click-outside, ESC, or the close button.
 *
 * Renders a Radix Dialog directly (instead of the styled `Dialog`
 * primitive) because the lightbox content lives on a fully-black
 * surface — sharing the standard Dialog's card styling would fight
 * that.
 */
const ScreenshotLightbox: React.FC<ScreenshotLightboxProps> = ({
  open,
  onClose,
  src,
  loading = false,
  error = null,
  caption,
}) => {
  // ESC to close.  Radix Dialog handles this too, but wire an explicit
  // listener in case an outer click-capture swallows the key event
  // before the dialog sees it.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, onClose]);

  return (
    <DialogPrimitive.Root open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay
          className={cn(
            'fixed inset-0 z-50 bg-black/90',
            'data-[state=open]:animate-in data-[state=closed]:animate-out',
            'data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0',
          )}
        />
        <DialogPrimitive.Content
          aria-label={caption ?? 'Screenshot'}
          className={cn(
            'fixed left-1/2 top-1/2 z-50 max-h-[95vh] max-w-[95vw] -translate-x-1/2 -translate-y-1/2',
            'flex flex-col items-center justify-center gap-sm p-md',
            'focus:outline-none',
          )}
        >
          <DialogPrimitive.Close
            className="absolute right-md top-md z-10 inline-flex size-8 items-center justify-center rounded-full bg-black/40 text-white hover:bg-black/70 focus:outline-none focus:ring-2 focus:ring-white"
            aria-label="Close screenshot"
          >
            <X className="size-4" aria-hidden />
          </DialogPrimitive.Close>
          {caption && (
            <p className="max-w-full break-all text-center font-mono text-metadata text-white">
              {caption}
            </p>
          )}
          {loading && (
            <div className="flex items-center gap-xs text-white">
              <Loader2 className="size-5 animate-spin" aria-hidden />
              <span>Loading screenshot…</span>
            </div>
          )}
          {!loading && error && (
            <Alert variant="destructive" className="max-w-[500px]">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          {!loading && !error && src && (
            // Audit PRF·M3: the bare <img> had no decoding hint, so
            // large screenshots blocked the main thread during decode.
            // `decoding="async"` lets the browser decode off-thread.
            // The image stays viewport-fit (max-h/max-w + object-contain)
            // — a fixed-aspect wrapper would letterbox arbitrary
            // screenshot dimensions, which is worse UX than the
            // tiny CLS this lightbox can incur (it's a modal that
            // already reserves the viewport, so layout-shift impact
            // is contained).
            <img
              src={src}
              alt={caption || 'Screenshot'}
              decoding="async"
              className="max-h-[calc(95vh-80px)] max-w-full rounded-control object-contain"
            />
          )}
          {!loading && !error && !src && (
            <p className="text-body text-white">Screenshot not available.</p>
          )}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
};

export default ScreenshotLightbox;
