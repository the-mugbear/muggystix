import { render } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { BreakableName, nameSegments } from '../../components/ui/breakable-name';

// Screenshot review 2026-09-24: `break-all` split "netexec-spider-172.30.77.10.json"
// as "…77.1 / 0.json" and "verbose-…-nmap-tls.xml" as "…-nma / p-tls.xml".
describe('BreakableName', () => {
  it('splits after each separator and nowhere else', () => {
    expect(nameSegments('verbose-os-nmap-tls.xml')).toEqual(['verbose-', 'os-', 'nmap-', 'tls.', 'xml']);
    expect(nameSegments('a_b/c')).toEqual(['a_', 'b/', 'c']);
    expect(nameSegments('plain')).toEqual(['plain']);
  });

  it('keeps overflow-wrap normal, and lets only a long unbreakable run split anywhere', () => {
    const { container } = render(<BreakableName name="short-name_0123456789abcdefghij.txt" />);
    const el = container.firstElementChild as HTMLElement;
    expect(el.className).toMatch(/\[overflow-wrap:normal\]/);
    expect(el.className).not.toMatch(/break-all/);
    expect(el.querySelectorAll('wbr')).toHaveLength(3);
    const long = el.querySelectorAll('span');
    expect(long).toHaveLength(1);
    expect(long[0]).toHaveTextContent('0123456789abcdefghij.');
    expect(long[0].className).toMatch(/\[overflow-wrap:anywhere\]/);
    expect(el.textContent).toBe('short-name_0123456789abcdefghij.txt');
  });
});
