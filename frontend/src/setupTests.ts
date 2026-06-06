// jest-dom adds custom jest matchers for asserting on DOM nodes.
// allows you to do things like:
// expect(element).toHaveTextContent(/react/i)
// learn more: https://github.com/testing-library/jest-dom
import '@testing-library/jest-dom';
import { vi } from 'vitest';

// Mock axios for API calls
vi.mock('axios');

// Mock Chart.js for charts
vi.mock('chart.js', () => ({
  Chart: {
    register: vi.fn(),
  },
  CategoryScale: vi.fn(),
  LinearScale: vi.fn(),
  BarElement: vi.fn(),
  ArcElement: vi.fn(),
  Title: vi.fn(),
  Tooltip: vi.fn(),
  Legend: vi.fn(),
}));

// Mock react-chartjs-2
vi.mock('react-chartjs-2', () => ({
  Bar: () => 'Bar Chart Mock',
  Doughnut: () => 'Doughnut Chart Mock',
}));

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
