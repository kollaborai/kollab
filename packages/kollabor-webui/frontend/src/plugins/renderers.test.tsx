import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { Prose } from "./renderers";

describe("plugin prose", () => {
  it("styles fenced code as a block, not as inline code", () => {
    const html = renderToStaticMarkup(<Prose text={"```ts\nconst a = 1;\n```"} />);
    const block = html.match(/<pre[\s\S]*?<\/pre>/)?.[0] ?? "";
    expect(block).toContain("<code");
    expect(block).not.toContain("aui-md-inline-code");
  });

  it("keeps inline code styled as inline code", () => {
    const html = renderToStaticMarkup(<Prose text={"use `npm test` here"} />);
    expect(html).toContain("aui-md-inline-code");
  });
});
