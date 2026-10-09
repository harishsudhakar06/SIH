import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'node:path'

const API_TARGET = process.env.VITE_DEV_PROXY || 'http://127.0.0.1:5000'

// Five separate mobile applications, one per role, built as five standalone
// HTML entry points so each app can be opened and shared independently.
export default defineConfig({
  plugins: [react()],
  base: './',
  server: {
    host: true,
    port: 5174,
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: true },
      '/uploads': { target: API_TARGET, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        citizen: resolve(__dirname, 'citizen.html'),
        field: resolve(__dirname, 'field.html'),
        community: resolve(__dirname, 'community.html'),
        response: resolve(__dirname, 'response.html'),
        analyst: resolve(__dirname, 'analyst.html'),
      },
    },
  },
})
