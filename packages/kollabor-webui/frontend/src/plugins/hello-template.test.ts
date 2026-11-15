import { execFileSync } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
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
  });

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
      expect(readFileSync(join(root, "dist/index.css"), "utf8")).toContain('[data-plugin="hello"]');
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});
