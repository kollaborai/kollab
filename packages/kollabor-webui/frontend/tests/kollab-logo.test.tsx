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
    const { canvas, lost } = fakeCanvas("Apple M3");
    expect(() => startGlass(canvas, K_POLYS)).not.toThrow();
    expect(startGlass(canvas, K_POLYS)).toBeNull();
    expect(lost).toEqual(["lost", "lost"]);
  });

  it("draws one still frame without scheduling work for reduced motion", async () => {
    vi.resetModules();
    const setTimeout = vi.fn(() => 1);
    vi.stubGlobal("window", {
      devicePixelRatio: 1,
      matchMedia: () => ({ matches: true }),
      addEventListener() {},
      removeEventListener() {},
      setTimeout,
      clearTimeout() {},
    });
    vi.stubGlobal("document", {
      documentElement: { classList: { contains: () => true } },
    });
    vi.doMock("three", () => {
      class Disposable { dispose() {} }
      class Geometry extends Disposable {
        scale() {}
        rotateX() {}
        center() {}
        setDrawRange() {}
      }
      class Renderer {
        private size = { x: 0, y: 0 };
        setPixelRatio() {}
        setRenderTarget() {}
        setSize(x: number, y: number) { this.size = { x, y }; }
        getSize(out: { x: number; y: number }) {
          out.x = this.size.x;
          out.y = this.size.y;
          return out;
        }
        render() {}
        dispose() {}
        forceContextLoss() {}
      }
      class Material extends Disposable {
        attenuationColor = { set() {} };
        constructor(options: object = {}) {
          super();
          Object.assign(this, options);
          this.attenuationColor = { set() {} };
        }
      }
      class Scene { add() {} }
      class Camera { position = { z: 0 }; updateProjectionMatrix() {} }
      class Vector2 { constructor(public x = 0, public y = 0) {} }
      class Vector3 { constructor(public x = 0, public y = 0, public z = 0) {} }
      class Light { position = { set() {} }; intensity = 0; }
      class Mesh { position = { z: 0 }; rotation = { x: 0, y: 0, z: 0 }; constructor(..._args: unknown[]) {} }
      class PMREMGenerator {
        fromScene() { return { texture: new Disposable() }; }
        dispose() {}
      }
      return {
        Camera,
        DirectionalLight: Light,
        ExtrudeGeometry: Geometry,
        Mesh,
        MeshBasicMaterial: Material,
        MeshPhysicalMaterial: Material,
        NeutralToneMapping: 0,
        PerspectiveCamera: Camera,
        PlaneGeometry: Geometry,
        PMREMGenerator,
        PointLight: Light,
        Scene,
        ShaderMaterial: Material,
        Shape: class { constructor(..._args: unknown[]) {} },
        Vector2,
        Vector3,
        WebGLRenderTarget: class extends Disposable {
          texture = new Disposable();
        },
        WebGLRenderer: Renderer,
      };
    });
    vi.doMock("three/addons/environments/RoomEnvironment.js", () => ({
      RoomEnvironment: class { dispose() {} },
    }));
    vi.doMock("three/addons/utils/BufferGeometryUtils.js", () => ({
      toCreasedNormals: (geometry: unknown) => geometry,
    }));

    try {
      const { startGlass: startGlassWithMockRenderer } = await import(
        "@/components/icons/kollab-logo-glass"
      );
      const context = {
        RENDERER: 1,
        getExtension: (name: string) =>
          name === "WEBGL_debug_renderer_info"
            ? { UNMASKED_RENDERER_WEBGL: 2 }
            : null,
        getParameter: () => "Apple M3",
      };
      const canvas = {
        clientWidth: 80,
        clientHeight: 80,
        getContext: () => context,
      } as unknown as HTMLCanvasElement;

      expect(startGlassWithMockRenderer(canvas, K_POLYS)).not.toBeNull();
      expect(setTimeout).not.toHaveBeenCalled();
    } finally {
      vi.doUnmock("three");
      vi.doUnmock("three/addons/environments/RoomEnvironment.js");
      vi.doUnmock("three/addons/utils/BufferGeometryUtils.js");
      vi.resetModules();
    }
  });
});
