import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Frontend calls /api/* and Vite forwards it to FastAPI in dev, so the
      // browser never needs to know the backend runs on a different port —
      // sidesteps CORS entirely during local development.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
