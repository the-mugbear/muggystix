// jest-dom adds custom jest matchers for asserting on DOM nodes.
// allows you to do things like:
// expect(element).toHaveTextContent(/react/i)
// learn more: https://github.com/testing-library/jest-dom
import '@testing-library/jest-dom';
import { vi } from 'vitest';

// Mock axios for API calls
vi.mock('axios');

// ---------------------------------------------------------------------------
// Server state (5.351.0).  Every component reads through @tanstack/react-query
// and so needs a QueryClientProvider above it.  `render` and `renderHook` give
// each call a FRESH client with the app's own defaults (lib/query: no retry,
// nothing cached after unmount), so no test sees another's data and no test
// file has to wrap its own renders.  A test's own `wrapper` goes inside it.
// ---------------------------------------------------------------------------
vi.mock('@testing-library/react', async () => {
  const actual = await vi.importActual<typeof import('@testing-library/react')>('@testing-library/react');
  const React = await vi.importActual<typeof import('react')>('react');
  const { QueryClientProvider } = await vi.importActual<typeof import('@tanstack/react-query')>('@tanstack/react-query');
  const { createQueryClient } = await vi.importActual<typeof import('./lib/query')>('./lib/query');
  type Wrapper = React.ComponentType<{ children: React.ReactNode }>;
  const withClient = (Inner?: Wrapper): Wrapper => {
    const client = createQueryClient();
    return ({ children }) => React.createElement(
      QueryClientProvider, { client }, Inner ? React.createElement(Inner, null, children) : children,
    );
  };
  return {
    ...actual,
    render: (ui: React.ReactElement, options?: Record<string, unknown>) => actual.render(
      ui, { ...options, wrapper: withClient(options?.wrapper as Wrapper | undefined) } as never,
    ),
    renderHook: (callback: (props: unknown) => unknown, options?: Record<string, unknown>) => actual.renderHook(
      callback, { ...options, wrapper: withClient(options?.wrapper as Wrapper | undefined) } as never,
    ),
  };
});

// Mock react-router-dom for navigation
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useParams: () => ({ id: '1' }),
    useLocation: () => ({
      pathname: '/',
      search: '',
      hash: '',
      state: null,
    }),
  };
});

// Mock file download - this will be handled by jsdom
global.URL = global.URL || {
  createObjectURL: vi.fn(() => 'mock-url'),
  revokeObjectURL: vi.fn(),
};

// ---------------------------------------------------------------------------
// Context-hook mocks (v4.46.0).
//
// Pre-mock, page-level tests wrapped renders only in <MemoryRouter> and never
// in <AuthProvider> / <ToastProvider> / <ProjectProvider>.  Pages call
// `useAuth()` (et al.) at the top of their bodies and throw before any
// assertion can run — 31 tests across 8 files were silently red on this
// branch.  Pages don't care WHO is logged in, only that the context exists
// and `hasPermission` is honest; mocking the hook is far less invasive than
// per-test provider wrapping.
//
// `vi.importActual` preserves every other export (the real Provider
// component, types, etc.) so call sites that import `AuthProvider` from
// these modules continue to work.
// ---------------------------------------------------------------------------

vi.mock('./contexts/AuthContext', async () => {
  const actual = await vi.importActual<typeof import('./contexts/AuthContext')>(
    './contexts/AuthContext',
  );
  return {
    ...actual,
    useAuth: () => ({
      user: {
        id: 1,
        username: 'test-user',
        email: 'test@example.com',
        role: 'admin',
        is_active: true,
        password_must_change: false,
      },
      token: 'mock-token',
      login: vi.fn(),
      logout: vi.fn(),
      updateUser: vi.fn(),
      isAuthenticated: true,
      isLoading: false,
      authStatus: 'authenticated' as const,
      hasRole: () => true,
      hasPermission: () => true,
    }),
  };
});

vi.mock('./contexts/ToastContext', async () => {
  const actual = await vi.importActual<typeof import('./contexts/ToastContext')>(
    './contexts/ToastContext',
  );
  return {
    ...actual,
    useToast: () => ({
      success: vi.fn(),
      error: vi.fn(),
      warning: vi.fn(),
      info: vi.fn(),
      dismiss: vi.fn(),
    }),
  };
});

vi.mock('./contexts/ProjectContext', async () => {
  const actual = await vi.importActual<typeof import('./contexts/ProjectContext')>(
    './contexts/ProjectContext',
  );
  return {
    ...actual,
    useProject: () => ({
      projects: [{ id: 1, name: 'Test Project', slug: 'test' }],
      currentProject: { id: 1, name: 'Test Project', slug: 'test' },
      selectProject: vi.fn(),
      isLoading: false,
      refreshProjects: vi.fn(),
      loadError: null,
    }),
  };
});

// Radix Tooltip needs a <TooltipProvider> ancestor or it throws.  Page
// tests don't assert on tooltip behavior — they just need the primitive
// not to crash.  Replace each component with a pass-through that renders
// its children (TooltipContent returns null since it's only visible on
// hover).  React.createElement avoids needing JSX in this .ts file.
vi.mock('./components/ui/tooltip', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const React = require('react') as typeof import('react');
  const passThrough = ({ children }: { children?: React.ReactNode }) =>
    React.createElement(React.Fragment, null, children);
  return {
    Tooltip: passThrough,
    TooltipTrigger: passThrough,
    TooltipContent: () => null,
    TooltipProvider: passThrough,
  };
});
// v4.59.0 (NEW I) — Radix UI components use Pointer Events APIs that
// jsdom doesn't implement.  Without these polyfills, any test that
// interacts with a Radix Select / Dropdown / Popover via userEvent
// throws "target.hasPointerCapture is not a function" mid-click,
// breaking otherwise-correct tests.  Stub them to no-ops so the
// component flow proceeds without the native pointer-capture path.
if (typeof Element !== 'undefined') {
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false;
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => undefined;
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => undefined;
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => undefined;
  }
}

// jsdom has no canvas: getContext logs "Not implemented" on every call. A
// browser without WebGL returns null — say so quietly (the Operations terrain
// then shows its table view).
Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', { value: () => null, configurable: true });
