import { execFileSync } from "node:child_process";
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

const repoRoot = path.resolve(frontendDir, "../../../");
const BUILD_UNKNOWN = "unknown";

function gitOutput(root: string, ...args: string[]): string | null {
  try {
    const output = execFileSync("git", args, {
      cwd: root,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
      timeout: 2_000,
    }).trim();
    return output || null;
  } catch {
    return null;
  }
}

function normalizeBranch(value: string | undefined): string | null {
  const branch = value?.trim();
  if (
    !branch ||
    branch === BUILD_UNKNOWN ||
    branch.length > 255 ||
    /[\x00-\x20\x7f]/.test(branch)
  ) {
    return null;
  }
  return branch;
}

function normalizeSha(value: string | undefined): string | null {
  const sha = value?.trim();
  if (!sha || !/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/i.test(sha)) return null;
  return sha.toLowerCase();
}

export function buildIdentity(
  root: string = repoRoot,
  environment: NodeJS.ProcessEnv = process.env,
): { branch: string; sha: string } {
  const environmentBranch = normalizeBranch(environment.KOLLAB_BUILD_BRANCH);
  const environmentSha = normalizeSha(environment.KOLLAB_BUILD_SHA);
  const gitRoot = gitOutput(root, "rev-parse", "--show-toplevel");
  // An enclosing checkout is not the source identity for an unpacked sdist.
  if (gitRoot !== null && path.resolve(gitRoot) === path.resolve(root)) {
    return {
      // A detached checkout intentionally has no source branch label.
      branch:
        normalizeBranch(
          gitOutput(root, "symbolic-ref", "--quiet", "--short", "HEAD") ?? undefined,
        ) ?? BUILD_UNKNOWN,
      sha:
        normalizeSha(gitOutput(root, "rev-parse", "--verify", "HEAD") ?? undefined) ??
        environmentSha ??
        BUILD_UNKNOWN,
    };
  }
  return {
    branch: environmentBranch ?? BUILD_UNKNOWN,
    sha: environmentSha ?? BUILD_UNKNOWN,
  };
}

const BUILD_IDENTITY = buildIdentity();

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
    define: {
      __APP_VERSION__: __APP_VERSION__,
      ...(mode === "phone-app"
        ? {
            __KOLLAB_BUILD_BRANCH__: JSON.stringify(BUILD_IDENTITY.branch),
            __KOLLAB_BUILD_SHA__: JSON.stringify(BUILD_IDENTITY.sha),
          }
        : {}),
      "process.env.NODE_ENV": JSON.stringify("production"),
    },
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
    __KOLLAB_BUILD_BRANCH__: JSON.stringify(BUILD_IDENTITY.branch),
    __KOLLAB_BUILD_SHA__: JSON.stringify(BUILD_IDENTITY.sha),
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
    // Vitest suites; Node's built-in test runner remains for older tests.
    include: [
      "tests/prompts-page.test.ts",
      "tests/history-cache.test.ts",
      "tests/phone-frame.test.ts",
      "tests/build-provenance.test.ts",
      "tests/voice-turn-groups.test.ts",
      "tests/path-text.test.ts",
      "tests/gem-headshot.test.ts",
      "tests/field-renderer-textarea.test.tsx",
    ],
    environment: "node",
  },
});
