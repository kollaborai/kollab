import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { MediaThumb, MediaImageProvider } from "@/components/media/media-view";
import type { EngineApi } from "@/api";

const stubApi = {
  request: async () => ({ ok: false, status: 404 }),
} as unknown as EngineApi;

const render = (ui: React.ReactNode) => renderToStaticMarkup(ui);

describe("MediaThumb empty-ref guard", () => {
  it("renders an inert placeholder, not a button, for an empty ref", () => {
    const html = render(
      createElement(MediaImageProvider, { api: stubApi }, createElement(MediaThumb, { ref_: "" })),
    );
    expect(html).toContain("image unavailable");
    expect(html).not.toContain("<button");
    expect(html).not.toContain("Open image");
  });

  it("renders the clickable thumbnail button for a stored ref", () => {
    const html = render(
      createElement(MediaImageProvider, { api: stubApi }, createElement(MediaThumb, { ref_: "abc" })),
    );
    expect(html).toContain("<button");
    expect(html).toContain("Open image");
  });
});
