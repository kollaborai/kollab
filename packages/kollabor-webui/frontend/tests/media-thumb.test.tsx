// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MediaThumb, MediaImageProvider, useAuthenticatedImage } from "@/components/media/media-view";
import type { EngineApi } from "@/api";

const stubApi = {
  request: async () => ({ ok: false, status: 404 }),
} as unknown as EngineApi;
const NativeURL = URL;

const render = (ui: React.ReactNode) => renderToStaticMarkup(ui);

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  const URLWithObjectUrls = class extends NativeURL {};
  Object.defineProperty(URLWithObjectUrls, "createObjectURL", {
    value: vi.fn(() => "blob:authenticated-image"),
  });
  Object.defineProperty(URLWithObjectUrls, "revokeObjectURL", {
    value: vi.fn(),
  });
  vi.stubGlobal("URL", URLWithObjectUrls);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

function AuthenticatedImageProbe({ path }: { path: string }) {
  const src = useAuthenticatedImage(path);
  return src ? <img data-testid="image" src={src} /> : <span>loading</span>;
}

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

  it("fetches queue attachment paths with the authenticated API", async () => {
    const request = vi.fn(async () => ({
      ok: true,
      blob: async () => new Blob(["image"]),
    }));
    const api = { request } as unknown as EngineApi;
    const path = "/sessions/s1/web-message-queue/items/web-a/attachments/att-a?conversation_uid=conv-a";
    await act(async () => {
      root.render(
        <MediaImageProvider api={api}>
          <AuthenticatedImageProbe path={path} />
        </MediaImageProvider>,
      );
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(request).toHaveBeenCalledWith(path);
    expect(container.querySelector("img")?.getAttribute("src")).toBe("blob:authenticated-image");
    expect(container.querySelector("img")?.getAttribute("src")).not.toBe(path);
  });
});
