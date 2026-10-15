import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Dev-proxy target for the API. Override with API_PROXY_TARGET when port 8000 is
// taken, e.g. `API_PROXY_TARGET=http://127.0.0.1:8001 npm run dev`.
const apiTarget = process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/v1': apiTarget,
      '/healthz': apiTarget,
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['src/test/setup.ts'],
    globals: false,
  },
})
