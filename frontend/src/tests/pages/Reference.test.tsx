/** Reference index (5.266.0) — a list that says what each entry does; no tiles. */
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect } from 'vitest';

import Reference from '../../pages/Reference';

describe('Reference', () => {
  it('lists every entry with what following it does', () => {
    const { container } = render(<MemoryRouter><Reference /></MemoryRouter>);
    expect(screen.getByRole('link', { name: /User guide/ })).toHaveAttribute('href', '/reference/user-guide');
    const agents = screen.getByRole('link', { name: /AI agent guide/ });
    expect(agents).toHaveAttribute('download', 'AGENTS.md');
    expect(agents).toHaveTextContent('downloads AGENTS.md');
    const swagger = screen.getByRole('link', { name: /Swagger UI/ });
    expect(swagger).toHaveAttribute('target', '_blank');
    expect(swagger).toHaveTextContent('opens in a new tab');
    expect(container.querySelector('.bg-card.shadow-raised')).toBeNull();
    expect(screen.queryByText(/items?$/)).not.toBeInTheDocument();
  });
});
