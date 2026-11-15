/// <reference types="node" />
import { execFileSync, spawn } from "node:child_process";
import { appendFileSync, cpSync, existsSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const frontend = process.cwd();
const example = resolve(frontend, "../../../bundles/plugins/hello/web");

describe("bundled Hello web development loop", () => {
  it("ships generated SDK declarations and Vite build/watch commands", () => {
    const pkg = JSON.parse(readFileSync(join(example, "package.json"), "utf8"));
    expect(pkg.scripts.build).toBe("vite build");
    expect(pkg.scripts.dev).toBe("vite build --watch");
    const declarations = readFileSync(join(example, "src/kollab-plugin.d.ts"), "utf8");
    const sdk = readFileSync(resolve(frontend, "src/plugins/sdk.ts"), "utf8");
    const contract = sdk.slice(sdk.indexOf("export type PluginOpen"), sdk.indexOf("/** Type-check"));
    expect(declarations).toContain(contract.trim());
    expect(declarations).toContain('declare module "@kollab/plugin"');
    execFileSync("node", ["scripts/generate-plugin-types.mjs", "--check"], { cwd: frontend });
  });

  it("typechecks a plugin against the generated declarations", () => {
    const root = mkdtempSync(join(process.env.KOLLAB_PLUGIN_TEST_DIR || tmpdir(), "hello-types-"));
    try {
      cpSync(join(example, "src/kollab-plugin.d.ts"), join(root, "kollab-plugin.d.ts"));
      symlinkSync(resolve(frontend, "node_modules"), join(root, "node_modules"), "dir");
      writeFileSync(join(root, "plugin.ts"), `import { definePlugin } from "@kollab/plugin";
export default definePlugin(k => { k.view({id:"view",label:"View",render:({k}) => { k.toast("ok"); return null; }}); k.command({name:"hello",description:"Hello",run:args => k.toast(args)}); });`);
      execFileSync(resolve(frontend, "node_modules/.bin/tsc"), ["--noEmit", "--strict", "--skipLibCheck", "--moduleResolution", "bundler", "--module", "esnext", "--target", "es2022", "plugin.ts", "kollab-plugin.d.ts"], { cwd: root, timeout: 30000, stdio: "pipe" });
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("rebuilds after source edits in watch mode", async () => {
    const root = mkdtempSync(join(process.env.KOLLAB_PLUGIN_TEST_DIR || tmpdir(), "hello-watch-"));
    cpSync(example, root, { recursive: true });
    rmSync(join(root, "dist"), { recursive: true, force: true });
    const child = spawn("npm", ["run", "dev"], { cwd: root, detached: process.platform !== "win32", stdio: "ignore", env: {
      ...process.env, PATH: `${resolve(frontend, "node_modules/.bin")}${delimiter}${process.env.PATH || ""}`,
    } });
    const output = join(root, "dist/index.js");
    const waitFor = async (predicate: () => boolean) => {
      const deadline = Date.now() + 10000;
      while (!predicate()) {
        if (child.exitCode !== null) throw new Error(`watch exited ${child.exitCode}`);
        if (Date.now() > deadline) throw new Error("watch did not rebuild in time");
        await new Promise(resolve => setTimeout(resolve, 50));
      }
    };
    try {
      await waitFor(() => existsSync(output));
      appendFileSync(join(root, "src/index.js"), '\nconsole.log("hello-watch-marker");\n');
      await waitFor(() => readFileSync(output, "utf8").includes("hello-watch-marker"));
    } finally {
      if (child.pid && child.exitCode === null) {
        if (process.platform === "win32") child.kill("SIGTERM");
        else { try { process.kill(-child.pid, "SIGTERM"); } catch { /* already exited */ } }
      }
      await new Promise<void>(resolve => child.exitCode !== null || child.signalCode !== null ? resolve() : child.once("exit", () => resolve()));
      rmSync(root, { recursive: true, force: true });
    }
  }, 20000);

  it("builds a real external-React bundle with npm into a disposable directory", () => {
    const root = mkdtempSync(join(process.env.KOLLAB_PLUGIN_TEST_DIR || tmpdir(), "hello-web-"));
    try {
      cpSync(example, root, { recursive: true });
      execFileSync("npm", ["run", "build"], { cwd: root, timeout: 30000, env: {
        ...process.env, PATH: `${resolve(frontend, "node_modules/.bin")}${delimiter}${process.env.PATH || ""}`,
      }, stdio: "pipe" });
      const bundle = readFileSync(join(root, "dist/index.js"), "utf8");
      expect(bundle).toMatch(/from\s*["']react["']/);
      expect(bundle).toMatch(/from\s*["']@kollab\/plugin["']/);
      expect(bundle).not.toContain("react.production");
      expect(readFileSync(join(root, "dist/index.css"), "utf8")).toMatch(/\[data-plugin=["']?hello["']?\]/);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});
