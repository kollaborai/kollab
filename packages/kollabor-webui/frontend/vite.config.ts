import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "./",
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
    },
  },
});
