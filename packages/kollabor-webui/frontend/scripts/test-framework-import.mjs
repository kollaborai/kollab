export function hasTestFrameworkImport(source, framework) {
  const escapedFramework = framework.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(
    `(?:\\bfrom\\s*|\\brequire\\s*\\(\\s*)(["'])${escapedFramework}\\1\\s*\\)?`,
  ).test(source);
}
