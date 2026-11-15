import { readdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { parse } from "@babel/parser";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const intrinsic = new Set(["button", "a", "input", "textarea", "select"]);
const component = /(?:Button|Trigger|MenuItem|SelectItem|Checkbox|Switch|RadioGroupItem|Link)$/;

/** Static candidates, not proof that a control is reachable or has been tested. */
export function extractControlCandidates(text, filename) {
  const source = parse(text, { sourceType: "module", plugins: ["jsx", "typescript"] });
  const candidates = [];
  function tagName(node) {
    if (node.type === "JSXIdentifier") return node.name;
    if (node.type === "JSXMemberExpression") return `${tagName(node.object)}.${tagName(node.property)}`;
    return "";
  }
  function visit(node, parent = null) {
    if (!node || typeof node !== "object") return;
    if (node.type === "JSXOpeningElement") {
      const tag = tagName(node.name);
      if (intrinsic.has(tag) || component.test(tag)) {
        const attributes = {};
        for (const attribute of node.attributes) {
          if (attribute.type !== "JSXAttribute" || attribute.name.type !== "JSXIdentifier") continue;
          const name = attribute.name.name;
          if (!["aria-label", "title", "role", "type", "data-testid", "href", "disabled", "onClick", "onChange", "onSelect"].includes(name)) continue;
          attributes[name] = attribute.value ? text.slice(attribute.value.start, attribute.value.end) : "true";
        }
        const textHint = parent?.type === "JSXElement"
          ? parent.children.filter((child) => child.type === "JSXText").map((child) => child.value.trim()).filter(Boolean).join(" ")
          : "";
        candidates.push({ file: filename, line: node.loc.start.line, column: node.loc.start.column + 1, tag, attributes, textHint, coverage: "unverified" });
      }
    }
    for (const [key, value] of Object.entries(node)) {
      if (["loc", "extra", "comments", "tokens"].includes(key)) continue;
      if (Array.isArray(value)) value.forEach((child) => visit(child, node));
      else if (value && typeof value === "object") visit(value, node);
    }
  }
  visit(source.program);
  return candidates;
}

async function sourceFiles(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) files.push(...await sourceFiles(full));
    else if (entry.isFile() && entry.name.endsWith(".tsx")) files.push(full);
  }
  return files.sort();
}

export async function inventoryControls(root = frontendRoot) {
  const files = await sourceFiles(path.join(root, "src"));
  const candidates = [];
  for (const file of files) {
    candidates.push(...extractControlCandidates(await readFile(file, "utf8"), path.relative(root, file)));
  }
  return { kind: "static-control-candidates", verified: false, filesScanned: files.length, candidates };
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const result = await inventoryControls();
  const json = `${JSON.stringify(result, null, 2)}\n`;
  if (process.argv[2]) {
    await writeFile(path.resolve(process.argv[2]), json);
    console.log(`Wrote ${result.candidates.length} unverified control candidates to ${process.argv[2]}`);
  } else process.stdout.write(json);
}
