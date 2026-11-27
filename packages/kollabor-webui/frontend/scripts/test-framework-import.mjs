import { parse } from "@babel/parser";

export function hasTestFrameworkImport(source, framework) {
  const ast = parse(source, {
    sourceType: "unambiguous",
    plugins: ["typescript", "jsx"],
  });
  const pending = [ast];
  while (pending.length) {
    const node = pending.pop();
    if (!node || typeof node !== "object") continue;

    if (node.type === "ImportDeclaration" && node.source?.value === framework) return true;
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
