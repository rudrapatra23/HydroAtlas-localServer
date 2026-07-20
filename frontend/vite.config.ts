/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    // Route-level code splitting is handled by React.lazy in App.tsx
    target: "esnext",
  },
  server: {
    allowedHosts: [
      'moaning-bullish-gruffly.ngrok-free.dev',
      '.ngrok-free.dev'
    ],
    proxy: {
      '^/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, '')
      }
    }
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // jsdom cannot render WebGL; maplibre-gl is fully mocked in tests.
    css: false,
  },
});
