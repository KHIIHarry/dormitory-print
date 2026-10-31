import { defineConfig } from "vitest/config";
import { loadEnv } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api/v1": { target: loadEnv(mode, ".", "DORMPRINT_").DORMPRINT_DEV_API_TARGET || "http://127.0.0.1:8000", changeOrigin: false } } },
  build: { chunkSizeWarningLimit: 1500 },
  test: { environment: "node", include: ["tests/**/*.test.ts"] },
}));
