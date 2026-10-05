import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev: requests to /api go to the FastAPI backend (default :8000; override with UA_API_TARGET).
// Production builds are served by FastAPI itself, so no proxy is involved there.
const apiTarget = process.env.UA_API_TARGET ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
    },
  },
})
