import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { KollabLogo, startMist } from "@/components/icons/kollab-logo";

/** A WebGL stub that records calls (+args); failure knobs drive the sad paths. */
function fakeGl(opts: {
  linked?: boolean;
  compiles?: boolean;
  programAllocFails?: boolean;
  /** Number of createShader calls that succeed before one returns null. */
  shaderAllocFailsAfter?: number;
  bufferAllocFails?: boolean;
  attribLocation?: number;
} = {}) {
  const {
    linked = true,
    compiles = true,
    programAllocFails = false,
    shaderAllocFailsAfter = Infinity,
    bufferAllocFails = false,
    attribLocation = 0,
  } = opts;
  const calls: string[] = [];
  const args: Array<[string, unknown[]]> = [];
  let shaderAllocs = 0;
  const gl = new Proxy(
    {
      LINK_STATUS: 1,
      COMPILE_STATUS: 8,
      VERTEX_SHADER: 2,
      FRAGMENT_SHADER: 3,
      ARRAY_BUFFER: 4,
      STATIC_DRAW: 5,
      FLOAT: 6,
      TRIANGLES: 7,
    },
    {
      get(target, key: string) {
        if (key in target) return target[key as keyof typeof target];
        return (...callArgs: unknown[]) => {
          calls.push(key);
          args.push([key, callArgs]);
          if (key === "getProgramParameter") return linked;
          if (key === "getShaderParameter") return compiles;
          if (key === "getAttribLocation") return attribLocation;
          if (key === "createProgram") return programAllocFails ? null : { id: "prog" };
          if (key === "createShader") {
            shaderAllocs += 1;
            return shaderAllocs > shaderAllocFailsAfter ? null : { id: `sh-${shaderAllocs}` };
          }
          if (key === "createBuffer") return bufferAllocFails ? null : { id: "buf" };
          if (key === "getExtension") return { loseContext: () => calls.push("loseContext") };
          return {};
        };
      },
    },
  );
  return { gl, calls, args };
}

function fakeCanvas(gl: unknown) {
  return { getContext: () => gl, clientWidth: 40, clientHeight: 22, width: 0, height: 0 } as unknown as HTMLCanvasElement;
}

function stubWindow(reduceMotion: boolean) {
  vi.stubGlobal("window", { matchMedia: () => ({ matches: reduceMotion }) });
  vi.stubGlobal("requestAnimationFrame", () => 1);
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
}

afterEach(() => vi.unstubAllGlobals());

describe("KollabLogo", () => {
  it("renders the bare mark without mist", () => {
    const html = renderToStaticMarkup(<KollabLogo className="h-6 w-auto" />);
    expect(html.startsWith("<svg")).toBe(true);
    expect(html).toContain('class="h-6 w-auto"');
    expect(html).not.toContain("<canvas");
  });

  it("keeps the plain mark under a K-masked canvas with mist", () => {
    const html = renderToStaticMarkup(<KollabLogo mist className="h-6 w-auto" />);
    expect(html).toMatch(/^<span class="relative inline-block h-6 w-auto">/);
    expect(html).toContain('fill="currentColor"');
    expect(html).toContain("<canvas");
    expect(html).toContain("mask-image:url(&quot;data:image/svg+xml,");
    expect(html).toContain(encodeURIComponent("m297.89 726.61"));
  });
});

describe("startMist", () => {
  it("leaves the plain mark when reduced motion is on", () => {
    stubWindow(true);
    const { gl, calls } = fakeGl();
    expect(startMist(fakeCanvas(gl))).toBeUndefined();
    expect(calls).toEqual([]);
  });

  it("stops drawing when reduced motion is enabled while the logo is mounted", () => {
    let onChange: ((event: { matches: boolean }) => void) | undefined;
    const media = {
      matches: false,
      addEventListener: (_type: string, callback: (event: { matches: boolean }) => void) => {
        onChange = callback;
      },
      removeEventListener: vi.fn(),
    };
    vi.stubGlobal("window", { matchMedia: () => media });
    let scheduled: FrameRequestCallback | undefined;
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      scheduled = callback;
      return 1;
    });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    const { gl, calls } = fakeGl();
    const stop = startMist(fakeCanvas(gl));
    const drawCount = calls.filter((call) => call === "drawArrays").length;

    media.matches = true;
    onChange?.({ matches: true });
    scheduled?.(0);

    expect(cancelAnimationFrame).toHaveBeenCalledWith(1);
    expect(calls.filter((call) => call === "drawArrays")).toHaveLength(drawCount);
    stop?.();
  });

  it("leaves the plain mark when WebGL is unavailable", () => {
    stubWindow(false);
    expect(startMist(fakeCanvas(null))).toBeUndefined();
  });

  it("leaves the plain mark when the shader fails to link", () => {
    stubWindow(false);
    const { gl, calls } = fakeGl({ linked: false });
    expect(startMist(fakeCanvas(gl))).toBeUndefined();
    expect(calls).not.toContain("drawArrays");
  });

  it("draws, sizes the canvas, and releases the context on stop", () => {
    stubWindow(false);
    const { gl, calls } = fakeGl();
    const canvas = fakeCanvas(gl);
    const stop = startMist(canvas);
    expect(calls).toContain("drawArrays");
    expect([canvas.width, canvas.height]).toEqual([40, 22]);
    stop!();
    expect(cancelAnimationFrame).toHaveBeenCalledWith(1);
    expect(calls).toContain("loseContext");
  });

  // --- zircon P2: the "a" attribute must not silently depend on slot 0 ---

  it("binds attribute a to a fixed slot before linking", () => {
    stubWindow(false);
    const { gl, calls } = fakeGl();
    startMist(fakeCanvas(gl));
    expect(calls).toContain("bindAttribLocation");
    expect(calls.indexOf("bindAttribLocation")).toBeLessThan(calls.indexOf("linkProgram"));
  });

  it("feeds the pointer the linker's actual location for a, not hardcoded slot 0", () => {
    stubWindow(false);
    const { gl, calls, args } = fakeGl({ attribLocation: 5 });
    startMist(fakeCanvas(gl));
    const enabled = args.find(([key]) => key === "enableVertexAttribArray");
    const pointed = args.find(([key]) => key === "vertexAttribPointer");
    expect(enabled?.[1][0]).toBe(5);
    expect(pointed?.[1][0]).toBe(5);
  });

  // --- zircon P3: allocation and compile failures fall back, not throw/leak ---

  it("falls back cleanly when createProgram returns null", () => {
    stubWindow(false);
    const { gl, calls } = fakeGl({ programAllocFails: true });
    expect(() => startMist(fakeCanvas(gl))).not.toThrow();
    expect(startMist(fakeCanvas(gl))).toBeUndefined();
    expect(calls).not.toContain("shaderSource");
    expect(calls).not.toContain("drawArrays");
  });

  it("falls back cleanly when the first createShader returns null", () => {
    stubWindow(false);
    const { gl, calls } = fakeGl({ shaderAllocFailsAfter: 0 });
    expect(() => startMist(fakeCanvas(gl))).not.toThrow();
    expect(startMist(fakeCanvas(gl))).toBeUndefined();
    expect(calls).not.toContain("drawArrays");
  });

  it("falls back and deletes shaders when a shader fails to compile", () => {
    stubWindow(false);
    const { gl, calls } = fakeGl({ compiles: false });
    expect(startMist(fakeCanvas(gl))).toBeUndefined();
    expect(calls).not.toContain("drawArrays");
    expect(calls).toContain("deleteShader");
    expect(calls).toContain("deleteProgram");
  });

  it("deletes program and shaders when linking fails (no leak)", () => {
    stubWindow(false);
    const { gl, calls } = fakeGl({ linked: false });
    startMist(fakeCanvas(gl));
    expect(calls).toContain("deleteShader");
    expect(calls).toContain("deleteProgram");
  });

  it("deletes GL resources on success stop (cleanup is safe)", () => {
    stubWindow(false);
    const { gl, calls } = fakeGl();
    const stop = startMist(fakeCanvas(gl));
    stop!();
    expect(calls).toContain("deleteBuffer");
    expect(calls).toContain("deleteProgram");
    expect(calls).toContain("deleteShader");
    expect(calls).toContain("loseContext");
    expect(calls.filter((c) => c === "drawArrays").length).toBeGreaterThan(0);
  });

  // --- koordinator round-2: every failure path must also release the context ---

  it("releases the GL context when createProgram returns null", () => {
    stubWindow(false);
    const { gl, calls } = fakeGl({ programAllocFails: true });
    expect(startMist(fakeCanvas(gl))).toBeUndefined();
    expect(calls).toContain("loseContext");
    expect(calls).not.toContain("drawArrays");
  });

  it("deletes the first shader and releases context when the second createShader returns null", () => {
    stubWindow(false);
    const { gl, calls } = fakeGl({ shaderAllocFailsAfter: 1 });
    expect(startMist(fakeCanvas(gl))).toBeUndefined();
    // The first shader compiled and was attached before the second alloc failed:
    // exactly one valid detach, one delete, then program + context release.
    expect(calls.filter((c) => c === "detachShader")).toHaveLength(1);
    expect(calls.filter((c) => c === "deleteShader")).toHaveLength(1);
    expect(calls).toContain("deleteProgram");
    expect(calls).toContain("loseContext");
    expect(calls).not.toContain("drawArrays");
  });

  it("never detaches an unattached shader on compile failure and releases context", () => {
    stubWindow(false);
    const { gl, calls } = fakeGl({ compiles: false });
    expect(startMist(fakeCanvas(gl))).toBeUndefined();
    expect(calls).not.toContain("detachShader");
    expect(calls.filter((c) => c === "deleteShader")).toHaveLength(1);
    expect(calls).toContain("deleteProgram");
    expect(calls).toContain("loseContext");
  });

  it("detaches both attached shaders and releases context when linking fails", () => {
    stubWindow(false);
    const { gl, calls } = fakeGl({ linked: false });
    expect(startMist(fakeCanvas(gl))).toBeUndefined();
    expect(calls.filter((c) => c === "detachShader")).toHaveLength(2);
    expect(calls.filter((c) => c === "deleteShader")).toHaveLength(2);
    expect(calls).toContain("deleteProgram");
    expect(calls).toContain("loseContext");
  });

  it("releases context and skips drawing when the attribute location is invalid", () => {
    stubWindow(false);
    const { gl, calls } = fakeGl({ attribLocation: -1 });
    expect(startMist(fakeCanvas(gl))).toBeUndefined();
    expect(calls).not.toContain("drawArrays");
    expect(calls.filter((c) => c === "detachShader")).toHaveLength(2);
    expect(calls).toContain("deleteProgram");
    expect(calls).toContain("loseContext");
  });

  it("falls back when createBuffer returns null (no upload, no draw, context released)", () => {
    stubWindow(false);
    const { gl, calls } = fakeGl({ bufferAllocFails: true });
    expect(startMist(fakeCanvas(gl))).toBeUndefined();
    expect(calls).not.toContain("bufferData");
    expect(calls).not.toContain("drawArrays");
    expect(calls).toContain("deleteProgram");
    expect(calls).toContain("loseContext");
  });

  it("a repeated stop() is harmless (cleanup is idempotent)", () => {
    stubWindow(false);
    const { gl, calls } = fakeGl();
    const stop = startMist(fakeCanvas(gl));
    stop!();
    stop!();
    expect(calls.filter((c) => c === "loseContext")).toHaveLength(1);
    expect(calls.filter((c) => c === "deleteProgram")).toHaveLength(1);
    expect(calls.filter((c) => c === "deleteBuffer")).toHaveLength(1);
  });
});
