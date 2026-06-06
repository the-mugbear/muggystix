import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import './index.css';
import App from './App';

/**
 * Recover from stale-tab-after-redeploy (v4.8.2).
 *
 * Every route is a lazy `import()`, code-split into a hash-named chunk
 * (e.g. `Activity-ZjtNQQ9r.js`).  A frontend redeploy produces NEW
 * hashes — the old chunk filenames vanish from the server.  A browser
 * tab left open across the deploy still runs the previous `index.js`,
 * so the next route navigation imports a chunk that no longer exists;
 * nginx answers the missing `.js` with the SPA's `index.html`, the
 * browser rejects the `text/html` MIME for a module, and the page
 * fails to load.
 *
 * Vite fires `vite:preloadError` on exactly that failure.  A full
 * `location.reload()` re-fetches `index.html`, which references the
 * current chunk hashes — the tab self-heals on the next navigation.
 *
 * Loop guard: if a reload was attempted in the last 10s and a chunk
 * STILL won't load, the deploy is genuinely broken — stop reloading
 * and let the error surface rather than thrash.
 */
window.addEventListener('vite:preloadError', (event) => {
  const KEY = 'nm:last-preload-reload';
  const last = Number(sessionStorage.getItem(KEY) || '0');
  if (Date.now() - last < 10_000) return; // just reloaded — broken deploy, don't loop
  sessionStorage.setItem(KEY, String(Date.now()));
  event.preventDefault(); // suppress the default unhandled throw; we're reloading
  window.location.reload();
});

const root = ReactDOM.createRoot(
  document.getElementById('root') as HTMLElement
);

root.render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
