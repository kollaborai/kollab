import { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor/editor/editor.api.js";
import "monaco-editor/editor/editor.main.js";
import editorWorker from "monaco-editor/editor/editor.worker.js?worker";
import cssWorker from "monaco-editor/language/css/css.worker.js?worker";
import htmlWorker from "monaco-editor/language/html/html.worker.js?worker";
import jsonWorker from "monaco-editor/language/json/json.worker.js?worker";
import tsWorker from "monaco-editor/language/typescript/ts.worker.js?worker";

/**
 * Monaco is bundled with the app and never loaded from a CDN. Importing this module configures the
 * loader and the web workers; the code panel imports it, so that is in place before any editor mounts.
 */

export const KOLLAB_DARK_THEME = "kollab-dark";

// Monaco starts one worker per language service. Vite turns each `?worker` import into its own chunk.
self.MonacoEnvironment = {
  getWorker(_workerId: string, label: string): Worker {
    switch (label) {
      case "json":
        return new jsonWorker();
      case "css":
      case "scss":
      case "less":
        return new cssWorker();
      case "html":
      case "handlebars":
      case "razor":
        return new htmlWorker();
      case "typescript":
      case "javascript":
        return new tsWorker();
      default:
        return new editorWorker();
    }
  },
};

/** Monaco color ids and the kollab design token each one takes its value from (dark palette). */
const THEME_COLORS: ReadonlyArray<readonly [colorId: string, token: string]> = [
  ["editor.background", "--background"],
  ["editorGutter.background", "--background"],
  ["editor.foreground", "--foreground"],
  ["editorLineNumber.foreground", "--muted-foreground"],
  ["editor.lineHighlightBackground", "--card"],
  ["editor.selectionBackground", "--accent"],
];

/**
 * Reads the `.dark` block of styles.css whatever mode the page is in, so the dark editor theme never
 * picks up the light tokens.
 */
function readDarkPalette(tokens: readonly string[]): Map<string, string> {
  const probe = document.createElement("div");
  probe.className = "dark";
  document.body.append(probe);
  const style = getComputedStyle(probe);
  const values = new Map(
    tokens.map((token): [string, string] => [token, style.getPropertyValue(token).trim()]),
  );
  probe.remove();
  return values;
}

/** Any CSS color the browser parses (oklch included) as #rrggbb, the only form Monaco accepts. */
function toHex(color: string): string | undefined {
  const context = document.createElement("canvas").getContext("2d");
  if (!context || color === "") return undefined;
  context.fillStyle = color;
  context.fillRect(0, 0, 1, 1);
  const [red = 0, green = 0, blue = 0] = context.getImageData(0, 0, 1, 1).data;
  return `#${[red, green, blue].map((channel) => channel.toString(16).padStart(2, "0")).join("")}`;
}

/** Defines the `kollab-dark` editor theme from the app's dark tokens. Safe to call again. */
export function defineKollabDarkTheme(): void {
  const palette = readDarkPalette(THEME_COLORS.map(([, token]) => token));
  const colors: Record<string, string> = {};
  for (const [colorId, token] of THEME_COLORS) {
    const hex = toHex(palette.get(token) ?? "");
    if (hex) colors[colorId] = hex;
  }
  monaco.editor.defineTheme(KOLLAB_DARK_THEME, {
    base: "vs-dark",
    inherit: true,
    rules: [],
    colors,
  });
}

loader.config({ monaco });
// Editors wait on this same loader, so the theme exists before the first editor is created.
void loader.init().then(() => defineKollabDarkTheme());
