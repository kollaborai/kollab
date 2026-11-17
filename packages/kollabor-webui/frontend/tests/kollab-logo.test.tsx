import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { K_POLYS, KollabLogo } from "@/components/icons/kollab-logo";
import { startGlass } from "@/components/icons/kollab-logo-glass";

/** A canvas whose WebGL2 context reports `gpu` as its renderer and records lost contexts. */
function fakeCanvas(gpu: string | null, opts: { throws?: boolean } = {}) {
  const lost: string[] = [];
  const context =
    gpu === null
      ? null
      : {
          RENDERER: 0x1f01,
          getExtension: (name: string) =>
            name === "WEBGL_debug_renderer_info"
              ? { UNMASKED_RENDERER_WEBGL: 0x9246 }
              : name === "WEBGL_lose_context"
                ? { loseContext: () => lost.push("lost") }
                : null,
          getParameter: () => gpu,
        };
  const canvas = {
    getContext: () => {
      if (opts.throws) throw new Error("context creation failed");
      return context;
    },
  } as unknown as HTMLCanvasElement;
  return { canvas, lost };
}

afterEach(() => vi.unstubAllGlobals());

describe("KollabLogo", () => {
  it("renders the bare mark without glass", () => {
    const html = renderToStaticMarkup(<KollabLogo className="h-6 w-auto" />);
    expect(html.startsWith("<svg")).toBe(true);
    expect(html).toContain('class="h-6 w-auto"');
    expect(html).not.toContain("<canvas");
  });

  it("draws one path per K polygon, from the same points the glass extrudes", () => {
    const html = renderToStaticMarkup(<KollabLogo />);
    expect(html.match(/<path /g)).toHaveLength(K_POLYS.length);
    expect(html).toContain('d="M150 903.46L821.26 423L1350 423');
    expect(html).toContain('d="M297.89 726.61L657.18 469.45L375.29 469.45Z"');
  });

  it("keeps the visible flat mark under an oversized canvas until the glass draws", () => {
    const html = renderToStaticMarkup(
      <KollabLogo glass className="h-6 w-auto" />,
    );
    expect(html).toMatch(/^<span class="relative inline-block h-6 w-auto">/);
    expect(html).toContain('fill="currentColor"');
    expect(html).not.toContain("invisible");
    expect(html).toContain("<canvas");
    expect(html).toContain("inset-[-40%]");
  });
});

describe("startGlass", () => {
  it("keeps the flat mark when WebGL2 is unavailable", () => {
    expect(startGlass(fakeCanvas(null).canvas, K_POLYS)).toBeNull();
  });

  it("keeps the flat mark when creating the context throws", () => {
    expect(
      startGlass(fakeCanvas("Apple M3", { throws: true }).canvas, K_POLYS),
    ).toBeNull();
  });

  it.each([
    "Google SwiftShader",
    "llvmpipe (LLVM 17.0.6, 256 bits)",
    "Microsoft Basic Render Driver",
  ])(
    "keeps the flat mark and releases the context on a software GPU (%s)",
    (gpu) => {
      const { canvas, lost } = fakeCanvas(gpu);
      expect(startGlass(canvas, K_POLYS)).toBeNull();
      expect(lost).toEqual(["lost"]);
    },
  );

  it("keeps the flat mark when three.js cannot start on the context", () => {
    // A hardware GPU name gets past the software check; the fake context then
    // fails inside WebGLRenderer, which must be caught, not thrown into React.
    vi.stubGlobal("window", {
      devicePixelRatio: 2,
      matchMedia: () => ({ matches: false }),
    });
    expect(() =>
      startGlass(fakeCanvas("Apple M3").canvas, K_POLYS),
    ).not.toThrow();
    expect(startGlass(fakeCanvas("Apple M3").canvas, K_POLYS)).toBeNull();
  });
});
