/**
 * Parse error fetch + types.  ParseError rows are the structured failure surface the Parse Errors page renders.
 *
 * v2.29.0 — extracted from services/api.ts.  api.ts re-exports
 * everything from here so consumers can keep importing from
 * ``../services/api`` unchanged.
 */
import { api, p } from './client';



export interface ParseError {
  id: number;
  filename: string;
  file_type: string | null;
  file_size: number | null;
  error_type: string;
  error_message: string;
  error_details: any;
  file_preview: string | null;
  user_message: string | null;
  status: string;
  created_at: string;
  updated_at: string | null;
}

export interface ParseErrorSummary {
  id: number;
  filename: string;
  file_type: string | null;
  error_type: string;
  user_message: string | null;
  status: string;
  created_at: string;
}


// consumer appears.
export const getParseError = async (errorId: number): Promise<ParseError> => {
  const response = await api.get(`${p()}/parse-errors/${errorId}`);
  return response.data;
};
