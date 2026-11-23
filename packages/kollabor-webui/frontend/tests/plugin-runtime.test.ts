import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import * as React from "react";
import * as ReactDOM from "react-dom";
import * as JsxRuntime from "react/jsx-runtime";
import { describe, expect, it } from "vitest";
import RuntimeReact, { useState as runtimeUseState } from "virtual:kollab-plugin-runtime/react";
import RuntimeReactDom, { createPortal as runtimeCreatePortal } from "virtual:kollab-plugin-runtime/react-dom";
import { jsx as runtimeJsx, Fragment as RuntimeFragment } from "virtual:kollab-plugin-runtime/react/jsx-runtime";
import * as RuntimeSdk from "virtual:kollab-plugin-runtime/@kollab/plugin";
import * as Sdk from "../src/plugins/sdk";
import { importMapTag, PLUGIN_RUNTIME_NAMES, runtimeModuleSource } from "../scripts/plugin-runtime-plugin";
import { bundlesOwnReact } from "../src/plugins/loader";

const require = createRequire(import.meta.url);

describe("plugin runtime modules", () => {
  it("re-export every name of the app's own react, react-dom and react/jsx-runtime", () => {
    for (const [name, exported] of [
      ["react", Object.keys(require("react"))],
      ["react-dom", Object.keys(require("react-dom"))],
      ["react/jsx-runtime", Object.keys(require("react/jsx-runtime"))],
    ] as const) {
      const source = runtimeModuleSource(name);
      expect(source, name).toContain(`import runtime from "${name}";`);
      expect(source, name).toContain("export default runtime;");
      for (const key of exported) expect(source, `${name}.${key}`).toMatch(new RegExp(`\\b${key}\\b`));
    }
  });

  it("are the app's own instances: one React for the app and every plugin", () => {
    expect(RuntimeReact.useState).toBe(React.useState);
    expect(runtimeUseState).toBe(React.useState);
    expect(RuntimeReactDom.createPortal).toBe(ReactDOM.createPortal);
    expect(runtimeCreatePortal).toBe(ReactDOM.createPortal);
    expect(runtimeJsx).toBe(JsxRuntime.jsx);
    expect(RuntimeFragment).toBe(JsxRuntime.Fragment);
    expect(RuntimeSdk.definePlugin).toBe(Sdk.definePlugin);
  });
});

describe("import map", () => {
  const chunk = (name: string, fileName: string) => ({ type: "chunk" as const, isEntry: true, name, fileName });
  const bundle = Object.fromEntries(
    PLUGIN_RUNTIME_NAMES.map(([, chunkName]) => [chunkName, chunk(chunkName, `assets/${chunkName}-AbC123.js`)]),
  );

  it("maps react, react-dom, react/jsx-runtime and @kollab/plugin to the built files, first in head", () => {
    const tag = importMapTag(bundle, "./");
    expect(tag).toMatchObject({ tag: "script", attrs: { type: "importmap" }, injectTo: "head-prepend" });
    expect(JSON.parse(tag.children)).toEqual({
      imports: {
        react: "./assets/plugin-runtime-react-AbC123.js",
        "react-dom": "./assets/plugin-runtime-react-dom-AbC123.js",
        "react/jsx-runtime": "./assets/plugin-runtime-jsx-runtime-AbC123.js",
        "@kollab/plugin": "./assets/plugin-runtime-kollab-plugin-AbC123.js",
      },
    });
  });

  it("fails the build when a runtime file is missing instead of shipping a broken map", () => {
    const { "plugin-runtime-react": _missing, ...rest } = bundle;
    expect(() => importMapTag(rest, "./")).toThrow("plugin-runtime-react");
  });
});

// The installed react package's own files (its exports map hides cjs/).
const reactFile = (file: string) => path.join(path.dirname(require.resolve("react/package.json")), file);

describe("a plugin bundle with its own React", () => {
  it("is recognized from React's own package text", () => {
    expect(bundlesOwnReact(readFileSync(reactFile("cjs/react.production.js"), "utf8"))).toBe(true);
    expect(bundlesOwnReact(readFileSync(reactFile("cjs/react.development.js"), "utf8"))).toBe(true);
  });

  it("is not confused with the jsx runtime, which carries no React of its own", () => {
    expect(bundlesOwnReact(readFileSync(reactFile("cjs/react-jsx-runtime.production.js"), "utf8"))).toBe(false);
  });
});
