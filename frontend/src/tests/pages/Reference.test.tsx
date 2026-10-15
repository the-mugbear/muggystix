/** Reference index (5.266.0) — a list that says what each entry does; no tiles. */
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect } from 'vitest';

import Reference from '../../pages/Reference';

describe('Reference', () => {
  it('lists every entry with what following it does', () => {
    const { container } = render(<MemoryRouter><Reference /></MemoryRouter>);
    expect(screen.getByRole('link', { name: /User guide/ })).toHaveAttribute('href', '/reference/user-guide');
    const agents = screen.getByRole('link', { name: /AI agent guide/ });
    expect(agents).toHaveAttribute('download', 'bluestick-agent-guide.md');
    expect(agents).toHaveTextContent('downloads the agent guide');
    const swagger = screen.getByRole('link', { name: /Swagger UI/ });
    expect(swagger).toHaveAttribute('target', '_blank');
    expect(swagger).toHaveTextContent('opens in a new tab');
    expect(container.querySelector('.bg-card.shadow-raised')).toBeNull();
    expect(screen.queryByText(/items?$/)).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Changing report templates/ }))
      .toHaveAttribute('href', '/reference/report-templates');
  });
});

describe('Changing report templates', () => {
  it('explains the levels of change and points to the full reference and the templates page', async () => {
    const { default: ReportTemplateGuide } = await import('../../pages/ReportTemplateGuide');
    render(<MemoryRouter><ReportTemplateGuide /></MemoryRouter>);
    expect(screen.getByRole('heading', { name: 'Changing report templates', level: 1 })).toBeInTheDocument();
    for (const title of ['How a report is made', 'How far you can go without changing BlueStick',
      'Changing a template safely', 'Rules a template must never break']) {
      expect(screen.getByText(title)).toBeInTheDocument();
    }
    for (const level of ['1 · No files', '2 · Word styles', '3 · The template', '4 · BlueStick source']) {
      expect(screen.getByText(level)).toBeInTheDocument();
    }
    expect(screen.getByRole('link', { name: 'Reports → Report templates' })).toHaveAttribute('href', '/reports/templates');
    // The diagram: the stages in the renderer's order, and where partials,
    // filters and scripts apply — the filters after BlueStick's own.
    const stages = within(screen.getByRole('list', { name: 'How a report is rendered, stage by stage' }));
    const titles = stages.getAllByText(/^(Build the data|Assemble a work folder|Fill the template \(Jinja\)|Render \(Quarto, once per format\)|Post-process \(per format\)|Deliver)$/);
    // Each title is preceded by its stage number in the same line.
    expect(titles.map((t) => (t.textContent ?? '').replace(/^\d+/, ''))).toEqual([
      'Build the data', 'Assemble a work folder', 'Fill the template (Jinja)', 'Render (Quarto, once per format)',
      'Post-process (per format)', 'Deliver',
    ]);
    const rendering = within(screen.getByRole('list', { name: 'What stage 4 uses' }));
    const order = rendering.getAllByText(/^[123]\. /).map((n) => n.textContent);
    expect(order).toEqual(['1. quarto', '2. _bluestick/fields.lua', '3. filters/*.lua']);
    expect(within(screen.getByRole('list', { name: 'What stage 3 uses' })).getByText('partials/*.qmd')).toBeInTheDocument();
    expect(within(screen.getByRole('list', { name: 'What stage 5 uses' })).getByText('scripts/… (postprocess)')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Reference' })).toHaveAttribute('href', '/reference');
  });
});
