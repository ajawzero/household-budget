import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// No @types/node here; this is the one Node global the config reads.
declare const process: { env: Record<string, string | undefined> };

// Defaults match scripts/dev.sh. A second dev server (screenshots, a parallel
// agent) can pick other ports with BUDGET_API_PORT and VITE_PORT.
const apiPort = process.env.BUDGET_API_PORT ?? "8000";
const webPort = Number(process.env.VITE_PORT ?? "5173");
const backend = `http://127.0.0.1:${apiPort}`;

// The backend's dev mode allows only the 5173 origin, and the proxy keeps the
// browser's Origin header as is (no changeOrigin). A dev server on another
// port sends the 5173 origin instead, so sign-in still works. Dev only.
const proxy = {
  target: backend,
  ...(webPort !== 5173 ? { headers: { origin: "http://127.0.0.1:5173" } } : {}),
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    port: webPort,
    strictPort: true,
    proxy: {
      "/api": proxy,
      "/healthz": proxy,
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
