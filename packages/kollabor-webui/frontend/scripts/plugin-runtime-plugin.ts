// One React for the app and every web plugin (docs/specs/plugins.md, "Web plugin API").
//
// The app build adds an entry per name a plugin imports bare: `react`, `react-dom`, `react/jsx-runtime` and
// `@kollab/plugin`. Each re-exports the app's own module, so a plugin built with those names external runs
// on the app's React instance. The built index.html gets an import map from the names to those files; it is
// the page's one inline script, and the web UI server allows it by its hash (kollabor_webui/server.py).
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { HtmlTagDescriptor, Plugin } from "vite";

const require = createRequire(import.meta.url);
const PREFIX = "virtual:kollab-plugin-runtime/";
const SDK = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src/plugins/sdk.ts");

/** Each bare name a plugin imports, and the entry chunk the app build gives it. */
export const PLUGIN_RUNTIME_NAMES = [
  ["react", "plugin-runtime-react"],
  ["react-dom", "plugin-runtime-react-dom"],
  ["react/jsx-runtime", "plugin-runtime-jsx-runtime"],
  ["@kollab/plugin", "plugin-runtime-kollab-plugin"],
] as const;

const SPECIFIERS = new Set<string>(PLUGIN_RUNTIME_NAMES.map(([specifier]) => specifier));

/** The module that re-exports `name`: every export of the installed package, by name, plus its default. */
export function runtimeModuleSource(name: string): string {
  if (name === "@kollab/plugin") return `export * from ${JSON.stringify(SDK)};\n`;
  // React's packages are CommonJS: named exports cannot be re-exported with `export *`, so they are listed.
  const names = Object.keys(require(name)).filter((key) => key !== "default" && /^[A-Za-z_$][\w$]*$/.test(key));
  return [
    `import runtime from ${JSON.stringify(name)};`,
    "export default runtime;",
    `export const { ${names.join(", ")} } = runtime;`,
    "",
  ].join("\n");
}

type OutputFile = { type: string; isEntry?: boolean; name?: string; fileName: string };

/** The import map tag for the built bundle; a missing runtime file fails the build. */
export function importMapTag(bundle: Record<string, OutputFile>, base: string): HtmlTagDescriptor & { children: string } {
  const entries = Object.values(bundle).filter((file) => file.type === "chunk" && file.isEntry);
  const imports: Record<string, string> = {};
  for (const [specifier, chunkName] of PLUGIN_RUNTIME_NAMES) {
    const chunk = entries.find((file) => file.name === chunkName);
    if (!chunk) throw new Error(`The app build has no ${chunkName} entry for the plugin import map.`);
    imports[specifier] = `${base}${chunk.fileName}`;
  }
  // First in <head>: an import map must come before any module script.
  return { tag: "script", attrs: { type: "importmap" }, children: JSON.stringify({ imports }), injectTo: "head-prepend" };
}

export function pluginRuntimePlugin(): Plugin {
  let building = false;
  let base = "./";
  return {
    name: "kollab-plugin-runtime",
    configResolved(config) {
      building = config.command === "build";
      base = config.base;
    },
    resolveId(source) {
      return source.startsWith(PREFIX) && SPECIFIERS.has(source.slice(PREFIX.length)) ? `\0${source}` : null;
    },
    load(id) {
      return id.startsWith(`\0${PREFIX}`) ? runtimeModuleSource(id.slice(PREFIX.length + 1)) : null;
    },
    buildStart() {
      if (!building) return;
      for (const [specifier, name] of PLUGIN_RUNTIME_NAMES)
        // "strict": the entry keeps exactly its exports, which an app build would otherwise drop as unused.
        this.emitFile({ type: "chunk", id: `${PREFIX}${specifier}`, name, preserveSignature: "strict" });
    },
    transformIndexHtml: {
      order: "post",
      handler(_html, context) {
        return context.bundle ? [importMapTag(context.bundle as Record<string, OutputFile>, base)] : undefined;
      },
    },
  };
}
