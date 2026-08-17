/// <reference types="vitest/config" />
import path from 'node:path'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  // .env один и лежит в корне монорепо (общий с Compose и бэкендом); без этого голый
  // `pnpm dev` не увидел бы VITE_-переменных.
  envDir: path.resolve(import.meta.dirname, '..'),
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, 'src'),
    },
  },
  server: {
    // Порт задаётся окружением, чтобы совпадать с маппингом в docker-compose.
    port: Number(process.env.FRONTEND_PORT_INTERNAL ?? 5173),
    host: true,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
})
