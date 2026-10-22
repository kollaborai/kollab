import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import pkg from "./package.json";


const frontendDir = path.dirname(fileURLToPath(import.meta.url));

// The web bundle version tracks the release version (frontend package.json,
// gated by scripts/check_version_parity.py). Injected at build time so the
// bundle never hardcodes a version that drifts from the engine.
const __APP_VERSION__ = JSON.stringify(pkg.version);

const KOLLAB_CHANGELOG_MODULE = "virtual:kollab-changelog";
const RESOLVED_KOLLAB_CHANGELOG_MODULE = `\0${KOLLAB_CHANGELOG_MODULE}`;

function kollabCanonicalChangelogPlugin(): Plugin {
  const canonicalChangelog = path.resolve(frontendDir, "../../../kollabor/updates/CHANGELOG.md");
  // Hatch includes the same canonical file as a build input in standalone sdists.
  const changelogPath = existsSync(canonicalChangelog)
    ? canonicalChangelog
    : path.resolve(frontendDir, "CHANGELOG.md");
  return {
    name: "kollab-canonical-changelog",
    resolveId(source) {
      return source === KOLLAB_CHANGELOG_MODULE ? RESOLVED_KOLLAB_CHANGELOG_MODULE : null;
    },
    load(id) {
      if (id !== RESOLVED_KOLLAB_CHANGELOG_MODULE) return null;
      this.addWatchFile(changelogPath);
      const markdown = readFileSync(changelogPath, "utf-8");
      return `export default ${JSON.stringify(markdown)};`;
    },
  };
}

// `phone-loader` and `phone-app` are the two halves of PhoneLink's page. The
// loader is the only code a directory serves; the app is served by the user's
// own computer and run only after its signature checks out (scripts/build.mjs).
const PHONE_BUILDS: Record<string, { entry: string; name: string; plugins: Plugin[] }> = {
  "phone-loader": { entry: "src/phonelink/loader/main.ts", name: "KollabPhoneLoader", plugins: [] },
  "phone-app": {
    entry: "src/phonelink/app-main.tsx",
    name: "KollabPhoneApp",
    plugins: [kollabCanonicalChangelogPlugin(), react(), tailwindcss()],
  },
};

export default defineConfig(({ mode }) => {
  const phone = PHONE_BUILDS[mode];
  if (!phone) return mainConfig;
  return {
    ...mainConfig,
    plugins: phone.plugins,
    publicDir: false,
    define: { ...mainConfig.define, "process.env.NODE_ENV": JSON.stringify("production") },
    build: {
      outDir: process.env.KOLLAB_PHONE_OUT ?? "dist/phonelink/.build",
      emptyOutDir: true,
      assetsInlineLimit: 100_000_000,
      cssCodeSplit: false,
      lib: { entry: path.resolve(frontendDir, phone.entry), name: phone.name, formats: ["iife"], fileName: () => "out.js" },
      rollupOptions: { output: { inlineDynamicImports: true } },
    },
  };
});

const mainConfig = defineConfig({
  plugins: [kollabCanonicalChangelogPlugin(), react(), tailwindcss()],
  base: "./",
  define: {
    __APP_VERSION__: __APP_VERSION__,
  },
  resolve: {
    alias: {
      "@": path.resolve(frontendDir, "./src"),
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
