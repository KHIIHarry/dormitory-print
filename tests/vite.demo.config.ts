import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
export default defineConfig({
  root: fileURLToPath(new URL("..", import.meta.url)),
  cacheDir: "node_modules/.vite-motion-demo",
  plugins: [react()],
  server: { port: 5177, strictPort: true, host: "127.0.0.1" },
});
