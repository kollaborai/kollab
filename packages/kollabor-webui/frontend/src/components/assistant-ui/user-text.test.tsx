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

  it("replaces the whole-row placeholder with a paged content control", () => {
    const marker = {
      kind: "chat-stream-parts-preview",
      session_id: "session-1",
      row_id: "row-large",
      part_count: 100,
      output_bytes: 300_000,
      output_chars: 280_000,
      output_type: "json",
      output_revision: "a".repeat(64),
    };
    stateWith(["text"], undefined);
    const state = mocks.state.current as unknown as { message: { metadata: { custom: Record<string, unknown> } } };
    state.message.metadata.custom.chatStreamPartsPreview = marker;

    const markup = renderToStaticMarkup(<UserText text="[Large chat row. Expand to load all content.]" />);

    expect(markup).toContain("chat-stream-parts-preview");
    expect(markup).toContain("Load full row");
    expect(markup).not.toContain("[Large chat row. Expand to load all content.]");
  });

  it("renders an individual structured-part recovery control", () => {
    stateWith(["text"], undefined);
    const marker = {
      kind: "chat-stream-part-preview",
      session_id: "session-1",
      row_id: "row-large-part",
      part_index: 4,
      output_bytes: 30_000,
      output_chars: 25_000,
      output_type: "json",
      output_revision: "b".repeat(64),
      preview: "[Large part. Expand to load full content.]",
    };

    const markup = renderToStaticMarkup(
      <UserText
        text={marker.preview}
        providerMetadata={{ kollab: { chat_stream_part_preview: marker } }}
      />,
    );

    expect(markup).toContain("Expand full part");
    expect(markup).toContain("[Large part. Expand to load full content.]");
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
