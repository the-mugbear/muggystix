import { useCallback, useState } from 'react';

import { downloadExecutionReport, ExecutionReportFormat } from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { asAxiosError, formatApiError } from '../utils/apiErrors';

/**
 * Encapsulates the test-plan execution report dialog: format picker,
 * loading spinner, error surface, and the download trigger.
 *
 * Extracted from TestPlanDetail.tsx so the page doesn't carry four
 * top-level useState calls just for a single dialog.  The dialog
 * markup stays in the page; this hook owns the state machine and
 * the download side-effect only.
 *
 * The blob error-handling path (unwrapping a backend JSON error from
 * an Axios blob response) is preserved verbatim from the original —
 * see backend/app/services/export_service.py for why the backend
 * returns errors as JSON blobs even when the success case is binary.
 */
export function useReportDownload(planId: number) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [format, setFormat] = useState<ExecutionReportFormat>('html');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const openDialog = useCallback(() => {
    setError(null);
    setFormat('html');
    setOpen(true);
  }, []);

  const closeDialog = useCallback(() => {
    if (loading) return;
    setOpen(false);
  }, [loading]);

  const download = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      await downloadExecutionReport(planId, format);
      toast.success(`Downloaded ${format.toUpperCase()} report.`);
      setOpen(false);
    } catch (err: unknown) {
      let msg = formatApiError(err, 'Failed to download execution report.');
      const blob = asAxiosError(err).response?.data;
      if (blob instanceof Blob && blob.type?.includes('json')) {
        try {
          const text = await blob.text();
          const parsed = JSON.parse(text);
          if (parsed?.detail) msg = parsed.detail;
        } catch {
          /* ignore — fall back to formatApiError */
        }
      }
      setError(msg);
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }, [planId, format, toast]);

  return {
    open,
    format,
    loading,
    error,
    openDialog,
    closeDialog,
    setFormat,
    download,
  };
}
