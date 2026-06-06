// Dynamic API URL resolution for network access - HTTPS only
export const getApiBaseUrl = () => {
  // If REACT_APP_API_URL is set, use it
  if (process.env.REACT_APP_API_URL) {
    return process.env.REACT_APP_API_URL;
  }

  // API is served through the same nginx that serves the frontend.
  // Use the current origin (same host/port) so requests go through the
  // nginx reverse proxy which terminates TLS and forwards to the backend.
  return window.location.origin;
};