import { parse } from "@babel/parser";

export function hasTestFrameworkImport(source, framework, fileName) {
  const extension = fileName.match(/\.[^.]+$/)?.[0].toLowerCase();
  const plugins = ["decorators-legacy"];
  if ([".ts", ".mts", ".cts"].includes(extension)) plugins.push("typescript");
  else if (extension === ".tsx") plugins.push("typescript", "jsx");
  else if ([".js", ".mjs", ".cjs", ".jsx"].includes(extension)) plugins.push("jsx");
  else throw new Error(`Unsupported test file kind: ${fileName}`);

  let ast;
  try {
    ast = parse(source, { sourceType: "unambiguous", sourceFilename: fileName, plugins });
  } catch (error) {
    throw new SyntaxError(`${fileName}: ${error.message}`, { cause: error });
  }
  const pending = [ast];
  while (pending.length) {
    const node = pending.pop();
    if (!node || typeof node !== "object") continue;

    if (
      node.type === "ImportDeclaration" &&
      node.source?.value === framework &&
      node.importKind !== "type" &&
      !(node.specifiers?.length && node.specifiers.every((specifier) => specifier.importKind === "type"))
    ) return true;
    if (
      node.type === "CallExpression" &&
      node.callee?.type === "Identifier" &&
      node.callee.name === "require" &&
      node.arguments?.length === 1 &&
      node.arguments[0]?.type === "StringLiteral" &&
      node.arguments[0].value === framework
    ) {
      return true;
    }

    for (const [key, value] of Object.entries(node)) {
      if (["loc", "comments", "tokens", "start", "end", "extra"].includes(key)) continue;
      if (Array.isArray(value)) pending.push(...value);
      else if (value && typeof value === "object") pending.push(value);
    }
  }
  return false;
}
