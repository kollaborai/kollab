// This example uses browser-ready JavaScript, so building only copies its sources.
import { copyFile, mkdir } from "node:fs/promises";

const source = new URL("./src/", import.meta.url);
const output = new URL("./dist/", import.meta.url);
await mkdir(output, { recursive: true });
for (const name of ["index.js", "index.css"]) {
  await copyFile(new URL(name, source), new URL(name, output));
}
