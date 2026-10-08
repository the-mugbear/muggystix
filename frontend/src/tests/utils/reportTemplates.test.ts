/** 5.342.0 — a contact's remediation list is never offered as a client report's template. */
import { describe, expect, it } from 'vitest';

import { isClientTemplate } from '../../utils/reportTemplates';

describe('isClientTemplate', () => {
  it('keeps client templates, and templates that say nothing (an older server)', () => {
    expect(isClientTemplate({ kind: 'client' })).toBe(true);
    expect(isClientTemplate({})).toBe(true);
  });
  it('leaves out the contact kind', () => {
    expect(isClientTemplate({ kind: 'contact' })).toBe(false);
  });
});
