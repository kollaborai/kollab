import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import pkg from "./package.json";

// The web bundle version tracks the release version (frontend package.json,
// gated by scripts/check_version_parity.py). Injected at build time so the
// bundle never hardcodes a version that drifts from the engine.
const __APP_VERSION__ = JSON.stringify(pkg.version);

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "./",
  define: {
    __APP_VERSION__: __APP_VERSION__,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8080",
      "/sessions": "http://127.0.0.1:7433",
      "/agents": "http://127.0.0.1:7433",
      "/profiles": "http://127.0.0.1:7433",
      "/mcp": "http://127.0.0.1:7433",
      "/hub": "http://127.0.0.1:7433",
      "/prompts": "http://127.0.0.1:7433",
    },
  },
  test: {
    // The System Prompts page suite; the older tests stay on `node --test`.
    include: ["tests/prompts-page.test.ts"],
    environment: "node",
  },
});
