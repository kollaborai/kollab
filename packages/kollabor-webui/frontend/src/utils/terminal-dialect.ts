/**
 * Agents write for the terminal (bundles/agents/_base/sections/meta/
 * terminal-formatting.md): plain "[x]" / "[ ]" checkboxes under lowercase
 * labels such as "todo:". Rewrite the checkboxes as GFM task items so the web
 * shows a checklist. Fenced code passes through untouched.
 */
export function terminalDialectToMarkdown(text: string): string {
  let fenced = false;
  return text
    .split("\n")
    .flatMap((line) => {
      if (/^\s*(```|~~~)/.test(line)) fenced = !fenced;
      if (fenced) return [line];
      // "todo: [x] item" on one line: the label, then the item.
      const inline = line.match(/^([a-z][a-z ()/-]*:)\s+(\[[ xX]\]\s.*)$/);
      if (inline) return [inline[1], "", `- ${inline[2]}`];
      return [line.replace(/^(\s*)(\[[ xX]\]\s)/, "$1- $2")];
    })
    .join("\n");
}
