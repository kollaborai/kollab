import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import * as React from "react";
import * as ReactDOM from "react-dom";
import * as JSX from "react/jsx-runtime";
import * as pluginReact from "./runtime/react";
import * as pluginDOM from "./runtime/react-dom";
import * as pluginJSX from "./runtime/jsx-runtime";
import { definePlugin as pluginDefine } from "./runtime/plugin";
import { definePlugin } from "./sdk";

describe("shared plugin runtime", () => {
  it("exports the exact host React, renderer, JSX and SDK instances", () => {
    expect(pluginReact.useState).toBe(React.useState);
    expect(pluginReact.createElement).toBe(React.createElement);
    expect(pluginDOM.createPortal).toBe(ReactDOM.createPortal);
    expect(pluginJSX.jsx).toBe(JSX.jsx);
    expect(pluginDefine).toBe(definePlugin);
  });

  it("declares all plugin bare imports in the app's import map", () => {
    const html = readFileSync(new URL("../../index.html", import.meta.url), "utf8");
    const match = html.match(/<script type="importmap">\s*([\s\S]*?)\s*<\/script>/);
    expect(match).not.toBeNull();
    const { imports } = JSON.parse(match![1]);
    expect(imports).toEqual({
      react: "./assets/plugin-react.js", "react-dom": "./assets/plugin-react-dom.js",
      "react/jsx-runtime": "./assets/plugin-jsx-runtime.js", "@kollab/plugin": "./assets/plugin-sdk.js",
    });
  });
});
