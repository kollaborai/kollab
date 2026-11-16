import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { UserText } from "./thread";

const mocks = vi.hoisted(() => ({
  state: { current: undefined as Record<string, unknown> | undefined },
}));

vi.mock("@assistant-ui/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@assistant-ui/react")>();
  return {
    ...actual,
    useAuiState: (selector: (state: unknown) => unknown) => selector(mocks.state.current),
  };
});

vi.mock("@/components/media/media-view", () => ({
  useMediaViewer: () => null,
  useSessionMedia: () => null,
  MediaThumb: ({ ref_ }: { ref_?: string }) => (
    <span data-testid="media-thumb" data-ref={ref_} />
  ),
}));

function stateWith(parts: string[], media: unknown) {
  mocks.state.current = {
    message: {
      metadata: { custom: media === undefined ? {} : { media } },
      parts: parts.map((type) => ({ type })),
    },
  };
}

describe("UserText render", () => {
  it("renders a token as nothing when the live row carries image parts", () => {
    // A live row shows its pictures as image parts; the [image1] token in
    // its text must not duplicate the attached picture beside it.
    stateWith(["image", "text"], undefined);
    const markup = renderToStaticMarkup(<UserText text="look [image1] now" />);
    expect(markup).not.toContain("[image1]");
    expect(markup).not.toContain("media-thumb");
  });

  it("still renders a typed [[imageN]] escape as literal text beside parts", () => {
    stateWith(["image", "text"], undefined);
    const markup = renderToStaticMarkup(<UserText text="typed [[image1]] here" />);
    expect(markup).toContain("[image1]");
  });

  it("renders a token thumb when the row has stored media and no parts", () => {
    stateWith(["text"], [{ ref: "stored-1", mime: "image/png" }]);
    const markup = renderToStaticMarkup(<UserText text="look [image1] now" />);
    expect(markup).toContain("media-thumb");
    expect(markup).toContain("stored-1");
    expect(markup).not.toContain("[image1]");
  });

  it("renders a token as literal text when the slot is missing (no parts)", () => {
    stateWith(["text"], undefined);
    const markup = renderToStaticMarkup(<UserText text="look [image1] now" />);
    expect(markup).toContain("[image1]");
    expect(markup).not.toContain("media-thumb");
  });
});

describe("UserText padded-token spelling (#1140 R4)", () => {
  it("renders an unresolved padded token with its exact original spelling", () => {
    stateWith(["text"], undefined);
    const markup = renderToStaticMarkup(<UserText text="look [image01] now" />);
    expect(markup).toContain("[image01]");
    expect(markup).not.toContain("media-thumb");
  });

  it("resolves a resolvable padded token to its stored slot (control)", () => {
    stateWith(["text"], [{ ref: "stored-pad", mime: "image/png" }]);
    const markup = renderToStaticMarkup(<UserText text="look [image01] now" />);
    expect(markup).toContain("media-thumb");
    expect(markup).toContain("stored-pad");
    expect(markup).not.toContain("[image01]");
  });

  it("keeps a typed padded escape literal exact", () => {
    stateWith(["text"], undefined);
    const markup = renderToStaticMarkup(<UserText text="typed [[image01]] here" />);
    expect(markup).toContain("[image01]");
  });
});
