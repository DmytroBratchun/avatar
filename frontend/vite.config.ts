import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // the file watcher on Windows sometimes misses a save, and the browser then gets
    // the old module (a component without its new import); polling doesn't miss
    watch: { usePolling: true, interval: 300 },
  },
})
