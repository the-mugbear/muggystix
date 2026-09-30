/**
 * The per-client hint is several notes joined by blank lines, with commands in
 * backticks. It used to render as ONE paragraph — a 12-line wall in the Start
 * Agent Session dialog, backticks shown literally.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import McpConnectPanel from '../../components/McpConnectPanel';
import { TooltipProvider } from '../../components/ui/tooltip';

const LONG = `codex mcp add bluestick --url https://${'a'.repeat(200)}.example/api/v1/mcp`;

const client = {
  id: 'vscode',
  label: 'VS Code Copilot',
  kind: 'file',
  path: '.vscode/mcp.json',
  payload: '{}',
  hint:
    'Save as .vscode/mcp.json in your workspace. ' +
    '\n\nKeep it out of version control; `read -rs` keeps the key out of history.' +
    `\n\nRemote host? Register it with: \`${LONG}\`` +
    '\n\nRun the client FROM the directory you want the output in.',
};

describe('McpConnectPanel hint', () => {
  it('renders each note as its own paragraph and each command as code', () => {
    const { container } = render(
      <TooltipProvider>
        <McpConnectPanel clients={[client]} />
      </TooltipProvider>,
    );

    expect(screen.getByText(/^Save as \.vscode\/mcp\.json in your workspace\.$/).tagName).toBe('P');
    expect(screen.getByText(/^Run the client FROM/).tagName).toBe('P');

    const codes = Array.from(container.querySelectorAll('code')).map((c) => c.textContent);
    expect(codes).toEqual(['read -rs', LONG]);
    // No literal backtick survives, and an unbounded URL can wrap.
    expect(container.textContent).not.toContain('`');
    expect(container.querySelectorAll('code')[1].className).toContain('break-all');
  });

  it('renders a plain one-line hint unchanged', () => {
    render(
      <TooltipProvider>
        <McpConnectPanel clients={[{ ...client, hint: 'Run in your project directory.' }]} />
      </TooltipProvider>,
    );
    expect(screen.getByText('Run in your project directory.')).toBeInTheDocument();
  });
});
