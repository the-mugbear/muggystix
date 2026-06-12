import React from 'react';

/**
 * React error boundary (audit / UX review #5).
 *
 * Without one, a single render-time exception unmounts the whole tree and
 * leaves a blank page — the entire nav + workflow gone. We use two:
 *   - a GLOBAL boundary in index.tsx (fallback offers a full reload);
 *   - a ROUTE boundary in App.tsx wrapping the page <Routes> (keyed on
 *     pathname so navigating away clears the error), so a single page's
 *     crash keeps the Layout/nav usable.
 *
 * `scope` labels the boundary in logs; `onReset` lets the route boundary
 * recover in place. The fallback is intentionally self-contained (plain
 * Tailwind, no app components) so it can't itself fail to render.
 */
interface Props {
  children: React.ReactNode;
  scope?: string;
  /** When true, the fallback offers a hard reload instead of a soft reset. */
  fullReload?: boolean;
  onReset?: () => void;
}

interface State {
  error: Error | null;
  errorId: string;
}

function makeErrorId(): string {
  // Short, user-quotable id to correlate a report with the console log.
  return Math.random().toString(36).slice(2, 10);
}

export default class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null, errorId: '' };

  static getDerivedStateFromError(error: Error): State {
    return { error, errorId: makeErrorId() };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // Telemetry seam: console for now (any future window error reporter can
    // hook here). Includes the id so the user-facing id maps to this log.
    // eslint-disable-next-line no-console
    console.error(
      `[ErrorBoundary${this.props.scope ? `:${this.props.scope}` : ''}] ${this.state.errorId}`,
      error,
      info?.componentStack,
    );
  }

  private handleReset = () => {
    this.props.onReset?.();
    this.setState({ error: null, errorId: '' });
  };

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <div
        role="alert"
        className="flex min-h-[40vh] w-full flex-col items-center justify-center gap-3 p-8 text-center"
      >
        <h2 className="text-lg font-semibold text-foreground">Something went wrong</h2>
        <p className="max-w-md text-sm text-muted-foreground">
          This {this.props.scope === 'route' ? 'page' : 'app'} hit an unexpected error.
          You can try again — if it keeps happening, reload and quote the error id below.
        </p>
        <code className="rounded bg-muted px-2 py-1 text-xs text-muted-foreground">
          error id: {this.state.errorId}
        </code>
        <div className="mt-2 flex gap-2">
          {!this.props.fullReload && (
            <button
              type="button"
              onClick={this.handleReset}
              className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-foreground hover:bg-accent"
            >
              Try again
            </button>
          )}
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90"
          >
            Reload
          </button>
        </div>
      </div>
    );
  }
}
