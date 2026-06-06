import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { visualizer } from 'rollup-plugin-visualizer'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    react(),
    // Tailwind v4 — only scans src/{components,pages}/v4/** (see
    // tailwind.config.ts content[]).  Preflight is opt-in via the CSS
    // @import; we deliberately do NOT import it so MUI's CssBaseline
    // continues to own base styles.
    tailwindcss(),
    // Emits build/stats.html when ANALYZE=1.  Kept off by default so a
    // normal `vite build` does not write the report file.
    process.env.ANALYZE
      ? visualizer({
          filename: 'build/stats.html',
          template: 'treemap',
          gzipSize: true,
          brotliSize: true,
        })
      : null,
  ],
  server: {
    port: 3000,
    host: true
  },
  build: {
    outDir: 'build',
    sourcemap: true
  },
  define: {
    'process.env.REACT_APP_API_URL': JSON.stringify(process.env.REACT_APP_API_URL ?? ''),
    'process.env.REACT_APP_VERSION': JSON.stringify(process.env.REACT_APP_VERSION ?? ''),
    'process.env.REACT_APP_BUILD_TIME': JSON.stringify(process.env.REACT_APP_BUILD_TIME ?? ''),
    'process.env.REACT_APP_GIT_COMMIT': JSON.stringify(process.env.REACT_APP_GIT_COMMIT ?? ''),
    'process.env.REACT_APP_BACKEND_VERSION': JSON.stringify(process.env.REACT_APP_BACKEND_VERSION ?? ''),
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/setupTests.ts',
  }
})