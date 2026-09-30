/**
 * uploadReportTemplateAsset sends the file as multipart (5.311.1).
 *
 * The API client's default Content-Type is JSON, and axios then serialises a
 * FormData body as JSON: the file never reached the server, which answered
 * 422 "field required" — found in the Chrome pass, because the page tests
 * mock this function.  So this test mocks only the HTTP client.
 */
import { describe, it, expect, vi } from 'vitest';

const { put } = vi.hoisted(() => ({
  put: vi.fn((..._args: unknown[]) => Promise.resolve({ data: { template: {}, warnings: [] } })),
}));
vi.mock('../../services/api/client', () => ({ api: { put }, p: () => '/projects/7' }));

import { uploadReportTemplateAsset } from '../../services/api/client-reports';

describe('uploadReportTemplateAsset', () => {
  it('puts the file as multipart form data to the asset’s path', async () => {
    const file = new File(['png'], 'logo.png', { type: 'image/png' });
    await uploadReportTemplateAsset('pentest', 'logo', file);
    const [url, body, config] = put.mock.calls[0] as [string, FormData, { headers?: Record<string, string> }];
    expect(url).toBe('/projects/7/client-reports/templates/pentest/assets/logo');
    expect(body).toBeInstanceOf(FormData);
    expect(body.get('file')).toBe(file);
    expect(config?.headers?.['Content-Type']).toBe('multipart/form-data');
  });
});
