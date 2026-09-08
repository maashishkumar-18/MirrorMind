/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
// @ts-expect-error type error without @types/node package
import process from "node:process";
const host = process.env.TAURI_DEV_HOST;

// https://vite.dev/config/
export default defineConfig(() => ({
  plugins: [react()],

  // Pre-bundle the Tauri API entrypoints so Vite never discovers them
  // mid-session and forces a full-page reload — that reload races Tauri's
  // IPC init and surfaces as "Cannot read properties of undefined (reading
  // 'transformCallback')".
  optimizeDeps: {
    include: ["@tauri-apps/api/core", "@tauri-apps/api/event"],
  },

  // Vite options tailored for Tauri development, applied in `tauri dev` / `tauri build`.
  // 1. prevent Vite from obscuring rust errors
  clearScreen: false,
  // 2. tauri expects a fixed port, fail if that port is not available
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    // 3. tell Vite to ignore watching `src-tauri`
    watch: {
      ignored: ["**/src-tauri/**"],
    },
  },

  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
}));
