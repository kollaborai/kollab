// Builds the web app, then PhoneLink's two halves into <outDir>/phonelink:
//   loader/ - the only code a connection directory serves (small, no app UI)
//   app/    - the phone app, served by the user's computer and signed there
import { copyFileSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";
import { build } from "vite";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const flag = process.argv.indexOf("--outDir");
const out = path.resolve(root, flag > 0 ? process.argv[flag + 1] : "dist");
process.chdir(root);

await build({ root, build: { outDir: out, emptyOutDir: true } });

const phone = path.join(out, "phonelink");
const scratch = path.join(phone, ".build");
async function bundle(mode) {
  process.env.KOLLAB_PHONE_OUT = scratch;
  await build({ root, mode });
  const files = readdirSync(scratch);
  const css = files.find((name) => name.endsWith(".css"));
  return { js: readFileSync(path.join(scratch, "out.js")), css: css ? readFileSync(path.join(scratch, css)) : null };
}

const loader = path.join(phone, "loader");
mkdirSync(loader, { recursive: true });
const shell = await bundle("phone-loader");
writeFileSync(path.join(loader, "loader.js"), shell.js);
for (const name of ["index.html", "manifest.webmanifest"])
  copyFileSync(path.join(root, "src/phonelink/loader", name), path.join(loader, name));
for (const name of ["apple-touch-icon.png", "icon-192.png", "icon-512.png", "icon-maskable-512.png"])
  copyFileSync(path.join(root, "public", name), path.join(loader, name));

const app = path.join(phone, "app");
mkdirSync(app, { recursive: true });
const phoneApp = await bundle("phone-app");
writeFileSync(path.join(app, "phone-app.js.gz"), gzipSync(phoneApp.js, { level: 9 }));
writeFileSync(path.join(app, "phone-app.css"), phoneApp.css ?? "");
rmSync(scratch, { recursive: true, force: true });
