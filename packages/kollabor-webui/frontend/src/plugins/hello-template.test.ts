/// <reference types="node" />
import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { appendFileSync, cpSync, existsSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// Paths come from this file, not the working directory.
const frontend = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const example = resolve(frontend, "../../../bundles/plugins/hello/web");
const manifest = resolve(example, "..", "kollab-plugin.json");
const localBin = join(frontend, "node_modules/.bin");
const toolPath = `${localBin}${delimiter}${process.env.PATH || ""}`;
// Each test makes its scratch folder here and removes it. KOLLAB_PLUGIN_TEST_DIR keeps it off the system temp folder.
const scratch = process.env.KOLLAB_PLUGIN_TEST_DIR || tmpdir();

async function waitFor(check: () => boolean, ms: number): Promise<void> {
  const deadline = Date.now() + ms;
  while (!check()) {
    if (Date.now() > deadline) throw new Error(`condition not met within ${ms} ms`);
    await new Promise((done) => setTimeout(done, 100));
  }
}

describe("bundled Hello web development loop", () => {
  it("ships generated SDK declarations and Vite build/watch commands", () => {
    const pkgPath = join(example, "package.json");
    expect(existsSync(pkgPath), "web/package.json exists").toBe(true);
    const pkg = JSON.parse(readFileSync(pkgPath, "utf8"));
    expect(pkg.scripts.build).toBe("vite build");
    expect(pkg.scripts.dev).toBe("vite build --watch");
    const dts = join(example, "src/kollab-plugin.d.ts");
    expect(existsSync(dts), "src/kollab-plugin.d.ts exists").toBe(true);
    const declarations = readFileSync(dts, "utf8");
    const sdk = readFileSync(join(frontend, "src/plugins/sdk.ts"), "utf8");
    const contract = sdk.slice(sdk.indexOf("export type OpenTarget"), sdk.indexOf("/** The app's own components"));
    expect(declarations).toContain(contract.trim());
    expect(declarations).toContain('declare module "@kollab/plugin"');
    execFileSync(process.execPath, [join(frontend, "scripts/generate-plugin-types.mjs"), "--check"], {
      cwd: frontend,
      stdio: "pipe",
    });
  });

  it("declares the hello menu row, page, view and dialog in the manifest", () => {
    expect(existsSync(manifest), "kollab-plugin.json exists").toBe(true);
    const json = JSON.parse(readFileSync(manifest, "utf8"));
    expect(json.id).toBe("hello");
    expect(json.contributes.menu.map((row: { id: string }) => row.id)).toEqual(["hello-page"]);
    expect(json.contributes.pages.map((page: { id: string }) => page.id)).toEqual(["hello-page"]);
    expect(json.contributes.views.map((view: { id: string }) => view.id)).toEqual(["hello-view"]);
    expect(json.contributes.modals.map((modal: { id: string }) => modal.id)).toEqual(["hello-dialog"]);
  });

  it("typechecks a plugin against the generated declarations", () => {
    const dts = join(example, "src/kollab-plugin.d.ts");
    expect(existsSync(dts), "src/kollab-plugin.d.ts exists").toBe(true);
    const root = mkdtempSync(join(scratch, "hello-types-"));
    try {
      cpSync(dts, join(root, "kollab-plugin.d.ts"));
      symlinkSync(join(frontend, "node_modules"), join(root, "node_modules"), "dir");
      writeFileSync(
        join(root, "plugin.ts"),
        `import { definePlugin } from "@kollab/plugin";
export default definePlugin(k => {
  k.view({ id: "view", label: "View", render: ({ k }) => { k.toast("ok"); return null; } });
});
`,
      );
      writeFileSync(
        join(root, "tsconfig.json"),
        JSON.stringify({
          compilerOptions: {
            strict: true,
            noEmit: true,
            module: "esnext",
            moduleResolution: "bundler",
            target: "es2022",
            types: [],
          },
          files: ["plugin.ts", "kollab-plugin.d.ts"],
        }),
      );
      execFileSync(join(localBin, "tsc"), ["-p", root], { cwd: root, timeout: 30000, stdio: "pipe" });
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  }, 30000);

  it("rebuilds after source edits in watch mode", async () => {
    expect(existsSync(join(example, "package.json")), "web/package.json exists").toBe(true);
    const root = mkdtempSync(join(scratch, "hello-watch-"));
    let child: ChildProcess | undefined;
    try {
      cpSync(example, root, { recursive: true });
      rmSync(join(root, "dist"), { recursive: true, force: true });
      const bundle = join(root, "dist/index.js");
      child = spawn("npm", ["run", "dev"], {
        cwd: root,
        env: { ...process.env, PATH: toolPath },
        stdio: "ignore",
      });
      await waitFor(() => existsSync(bundle), 8000);
      appendFileSync(join(root, "src/index.js"), '\nglobalThis.__helloWatchMarker = "hello-watch-marker";\n');
      await waitFor(() => existsSync(bundle) && readFileSync(bundle, "utf8").includes("hello-watch-marker"), 8000);
    } finally {
      if (child?.pid && child.exitCode === null && child.signalCode === null) child.kill("SIGTERM");
      await new Promise((done) => setTimeout(done, 500));
      rmSync(root, { recursive: true, force: true });
    }
  }, 20000);

  it("builds a real external-React bundle with npm into a disposable directory", () => {
    expect(existsSync(join(example, "package.json")), "web/package.json exists").toBe(true);
    const root = mkdtempSync(join(scratch, "hello-web-"));
    try {
      cpSync(example, root, { recursive: true });
      execFileSync("npm", ["run", "build"], {
        cwd: root,
        timeout: 30000,
        env: { ...process.env, PATH: toolPath },
        stdio: "pipe",
      });
      const bundle = readFileSync(join(root, "dist/index.js"), "utf8");
      expect(bundle).toMatch(/from\s*["']react["']/);
      expect(bundle).toMatch(/from\s*["']@kollab\/plugin["']/);
      expect(bundle).not.toContain("react.production");
      expect(readFileSync(join(root, "dist/index.css"), "utf8")).toMatch(/\[data-plugin=["']?hello["']?\]/);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  }, 30000);
});

describe("Hello template watch process cleanup", () => {
  it("keeps the watcher interruptible and avoids signaling an exited child", () => {
    const source = readFileSync(fileURLToPath(import.meta.url), "utf8");
    expect(source).not.toMatch(/detached:\s*true/);
    expect(source).toMatch(/child\.exitCode === null/);
    expect(source).toMatch(/child\.signalCode === null/);
  });
});
