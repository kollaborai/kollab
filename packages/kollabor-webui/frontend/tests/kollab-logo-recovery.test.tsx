// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { K_POLYS } from "@/components/icons/kollab-logo";

// Regression tests for #1246 review findings: the flat K must stay visible until
// the glass has really drawn a frame, and must come back when the GPU context is lost.

type Listener = (event: { preventDefault: () => void }) => void;

function mockThree(renders: { count: number }) {
  class Disposable {
    dispose() {}
  }
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
    setSize(x: number, y: number) {
      this.size = { x, y };
    }
    getSize(out: { x: number; y: number }) {
      out.x = this.size.x;
      out.y = this.size.y;
      return out;
    }
    render() {
      renders.count += 1;
    }
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
  class Scene {
    add() {}
  }
  class Camera {
    position = { z: 0 };
    updateProjectionMatrix() {}
  }
  class Vector2 {
    constructor(
      public x = 0,
      public y = 0,
    ) {}
  }
  class Vector3 {
    constructor(
      public x = 0,
      public y = 0,
      public z = 0,
    ) {}
  }
  class Light {
    position = { set() {} };
    intensity = 0;
  }
  class Mesh {
    position = { z: 0 };
    rotation = { x: 0, y: 0, z: 0 };
    constructor(..._args: unknown[]) {}
  }
  class PMREMGenerator {
    fromScene() {
      return { texture: new Disposable() };
    }
    dispose() {}
  }
  vi.doMock("three", () => ({
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
    Shape: class {
      constructor(..._args: unknown[]) {}
    },
    Vector2,
    Vector3,
    WebGLRenderTarget: class extends Disposable {
      texture = new Disposable();
    },
    WebGLRenderer: Renderer,
  }));
  vi.doMock("three/addons/environments/RoomEnvironment.js", () => ({
    RoomEnvironment: class {
      dispose() {}
    },
  }));
  vi.doMock("three/addons/utils/BufferGeometryUtils.js", () => ({
    toCreasedNormals: (geometry: unknown) => geometry,
  }));
}

/** A canvas on a hardware GPU whose size and context events the test controls. */
function controllableCanvas() {
  const listeners = new Map<string, Listener>();
  const context = {
    RENDERER: 1,
    getExtension: (name: string) =>
      name === "WEBGL_debug_renderer_info"
        ? { UNMASKED_RENDERER_WEBGL: 2 }
        : null,
    getParameter: () => "Apple M3",
  };
  const canvas = {
    clientWidth: 0,
    clientHeight: 0,
    getContext: () => context,
    addEventListener: (type: string, fn: Listener) => listeners.set(type, fn),
    removeEventListener: (type: string) => listeners.delete(type),
  };
  return { canvas: canvas as unknown as HTMLCanvasElement, raw: canvas, listeners };
}

describe("glass K recovery", () => {
  afterEach(() => {
    vi.doUnmock("three");
    vi.doUnmock("three/addons/environments/RoomEnvironment.js");
    vi.doUnmock("three/addons/utils/BufferGeometryUtils.js");
    vi.doUnmock("@/components/icons/kollab-logo-glass");
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("reduced motion: reports a frame only once the canvas has a size, then draws it", async () => {
    vi.resetModules();
    const renders = { count: 0 };
    let resize: () => void = () => {};
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(cb: () => void) {
          resize = cb;
        }
        observe() {}
        disconnect() {}
      },
    );
    vi.stubGlobal("window", {
      devicePixelRatio: 1,
      matchMedia: () => ({
        matches: true,
        addEventListener() {},
        removeEventListener() {},
      }),
      addEventListener() {},
      removeEventListener() {},
      clearTimeout() {},
    });
    vi.stubGlobal("document", {
      documentElement: { classList: { contains: () => true } },
    });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    mockThree(renders);
    const { startGlass } = await import("@/components/icons/kollab-logo-glass");
    const { canvas, raw } = controllableCanvas();
    const onFrame = vi.fn();
    const glass = startGlass(canvas, K_POLYS, { onFrame, onLost: vi.fn() });
    expect(glass).not.toBeNull();
    // Hidden at mount: nothing drawn, so the flat K must not be told to hide.
    expect(onFrame).not.toHaveBeenCalled();
    raw.clientWidth = 80;
    raw.clientHeight = 80;
    resize();
    expect(renders.count).toBe(2);
    expect(onFrame).toHaveBeenCalledOnce();
    glass?.stop();
  });

  it("reports a lost WebGL context so the flat mark can come back", async () => {
    vi.resetModules();
    const renders = { count: 0 };
    vi.stubGlobal("window", {
      devicePixelRatio: 1,
      matchMedia: () => ({
        matches: true,
        addEventListener() {},
        removeEventListener() {},
      }),
      addEventListener() {},
      removeEventListener() {},
      clearTimeout() {},
    });
    vi.stubGlobal("document", {
      documentElement: { classList: { contains: () => true } },
    });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    mockThree(renders);
    const { startGlass } = await import("@/components/icons/kollab-logo-glass");
    const { canvas, raw, listeners } = controllableCanvas();
    raw.clientWidth = 80;
    raw.clientHeight = 80;
    const onLost = vi.fn();
    const preventDefault = vi.fn();
    startGlass(canvas, K_POLYS, { onFrame: vi.fn(), onLost });
    listeners.get("webglcontextlost")?.({ preventDefault });
    expect(preventDefault).toHaveBeenCalled();
    expect(onLost).toHaveBeenCalledOnce();
  });

  it("KollabLogo hides the flat mark only while the glass is drawing", async () => {
    vi.resetModules();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    let hooks: { onFrame: () => void; onLost: () => void } | undefined;
    vi.doMock("@/components/icons/kollab-logo-glass", () => ({
      startGlass: vi.fn(
        (_c: unknown, _p: unknown, h: { onFrame: () => void; onLost: () => void }) => {
          hooks = h;
          return { stop: vi.fn(), flare: vi.fn() };
        },
      ),
    }));
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    try {
      const { KollabLogo } = await import("@/components/icons/kollab-logo");
      await act(async () => {
        root.render(<KollabLogo glass />);
      });
      const svg = () => container.querySelector("svg")!;
      // Started but no frame yet: the flat mark stays visible.
      expect(svg().getAttribute("class")).not.toContain("invisible");
      await act(async () => hooks?.onFrame());
      expect(svg().getAttribute("class")).toContain("invisible");
      await act(async () => hooks?.onLost());
      expect(svg().getAttribute("class")).not.toContain("invisible");
    } finally {
      await act(async () => root.unmount());
      container.remove();
    }
  });
});
