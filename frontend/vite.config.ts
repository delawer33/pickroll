/// <reference types="vitest/config" />
import path from 'node:path'

import tailwindcss from '@tailwindcss/vite'
import { tanstackRouter } from '@tanstack/router-plugin/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [
    // Плагин роутера должен стоять до react(): он генерирует routeTree.gen.ts,
    // который затем проходит обычный конвейер.
    tanstackRouter({ target: 'react', autoCodeSplitting: true }),
    react(),
    tailwindcss(),
  ],
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
    // Базовый URL API задаётся здесь, а не читается из `.env`: тесты не должны зависеть от
    // локального файла, которого в CI нет, а без переменной клиент намеренно падает.
    env: { VITE_API_BASE_URL: 'http://api.test' },
  },
})
