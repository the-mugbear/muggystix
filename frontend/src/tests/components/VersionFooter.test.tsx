import React from 'react';
import { render, screen } from '@testing-library/react';

// Mock buildInfo.json to control fallback values
vi.mock('../../buildInfo.json', () => ({
  default: {
    frontendVersion: '1.4.2',
    backendVersion: '1.2.2',
    buildTime: '2024-01-01T12:00:00.000Z',
    gitCommit: 'abc123def456',
  }
}));

import VersionFooter from '../../components/VersionFooter';

// Mock environment variables
const mockEnv = {
  REACT_APP_VERSION: '1.4.2',
  REACT_APP_BUILD_TIME: '2024-01-01T12:00:00.000Z',
  REACT_APP_GIT_COMMIT: 'abc123def456',
  REACT_APP_BACKEND_VERSION: '1.2.2',
};

// Helper function — kept its old name + `darkMode` arg for call-site
// compatibility even though the v4 VersionFooter doesn't read either.
// The theme is now driven by CSS variables on <html>, not a wrapping
// provider.
const renderWithTheme = (component: React.ReactElement, _darkMode = false) =>
  render(component);

describe('VersionFooter', () => {
  // Store original env
  const originalEnv = process.env;

  beforeEach(() => {
    // Reset environment variables
    process.env = { ...originalEnv, ...mockEnv };
  });

  afterEach(() => {
    // Restore original env
    process.env = originalEnv;
  });

  it('renders version information correctly', () => {
    renderWithTheme(<VersionFooter />);
    
    const footer = screen.getByText(/BlueStick v/);
    expect(footer).toHaveTextContent(`BlueStick v${mockEnv.REACT_APP_VERSION}`);
    expect(footer).toHaveTextContent(`API ${mockEnv.REACT_APP_BACKEND_VERSION}`);
    expect(footer).toHaveTextContent('Built');
    expect(footer).toHaveTextContent(/abc123d/);
  });

  it('renders with light theme styles', () => {
    renderWithTheme(<VersionFooter />, false);
    
    const footer = screen.getByText(/BlueStick v/).closest('div');
    expect(footer).toBeInTheDocument();
  });

  it('renders with dark theme styles', () => {
    renderWithTheme(<VersionFooter />, true);
    
    const footer = screen.getByText(/BlueStick v/).closest('div');
    expect(footer).toBeInTheDocument();
  });

  it('handles missing environment variables gracefully', () => {
    // Clear environment variables
    delete process.env.REACT_APP_VERSION;
    delete process.env.REACT_APP_BUILD_TIME;
    delete process.env.REACT_APP_GIT_COMMIT;
    delete process.env.REACT_APP_BACKEND_VERSION;

    renderWithTheme(<VersionFooter />);
    
    // Should fall back to buildInfo.json values
    const footer = screen.getByText(/BlueStick v/);
    expect(footer).toHaveTextContent('BlueStick v1.4.2');
    expect(footer).toHaveTextContent('API 1.2.2');
    expect(footer).toHaveTextContent('Built');
  });

  it('truncates git commit hash to 7 characters', () => {
    process.env.REACT_APP_GIT_COMMIT = 'abcdefghijklmnop';
    
    renderWithTheme(<VersionFooter />);
    
    // Should only show first 7 characters
    expect(screen.getByText(/abcdefg/)).toBeInTheDocument();
    expect(screen.queryByText(/abcdefghijklmnop/)).not.toBeInTheDocument();
  });

  it('formats build time correctly', () => {
    renderWithTheme(<VersionFooter />);
    
    // Should display formatted date
    const buildTimeElement = screen.getByText(/Built/);
    expect(buildTimeElement).toBeInTheDocument();
    
    // The exact format depends on locale, but should contain date info
    expect(buildTimeElement.textContent).toMatch(/\d{1,2}\/\d{1,2}\/\d{4}/);
  });
});
