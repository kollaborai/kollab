import { useEffect, useRef } from "react";

import { cn } from "@/lib/utils";

const PATHS = [
  "M150 903.46 821.26 423H1350L623.56 813.8l263.22 263.22H685.63L384.48 903.46Z",
  "m297.89 726.61 359.29-257.16H375.29Z",
];
const VIEW_BOX = "150 423 1200 654.02";

// The K silhouette as a CSS mask, so the mist shows only inside the letter.
const MASK = `url("data:image/svg+xml,${encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${VIEW_BOX}">${PATHS.map(
    (d) => `<path d="${d}"/>`,
  ).join("")}</svg>`,
)}")`;

// Electric mist shader, ported from Mentiko's page banner (electric-mist.tsx).
const FRAG = `
precision mediump float;
uniform float uTime;
uniform vec2 uRes;
uniform vec3 uColor;
#define time uTime * 0.2
mat2 makem2(float t){ float c=cos(t), s=sin(t); return mat2(c,-s,s,c); }
float hash(vec2 p){ return fract(sin(dot(p, vec2(12.9898,78.233)))*43758.5453); }
float noise(vec2 x){
  x *= 1.5;
  vec2 p=floor(x), f=fract(x); f=f*f*(3.0-2.0*f);
  return mix(mix(hash(p),hash(p+vec2(1,0)),f.x), mix(hash(p+vec2(0,1)),hash(p+vec2(1,1)),f.x), f.y);
}
float fbm(vec2 p, int oct){
  mat2 m2 = mat2(0.8,0.6,-0.6,0.8);
  float z=2.0, rz=0.0;
  for (int i=0;i<5;i++){ if(i>=oct) break; rz+=abs((noise(p)-0.5)*4.0)/z; z*=2.0; p=m2*(p*2.0); }
  return rz;
}
void main(){
  vec2 p = gl_FragCoord.xy / uRes * 2.0 - 1.0;
  p.x *= uRes.x / uRes.y;
  p *= makem2(uTime * 0.15); // spin the whole field, ~23 s per turn
  vec2 bp = p;
  p = (p + 5.0) * 0.5;
  float rb = fbm(p*0.5 + time*0.17, 3) * 0.1;
  p *= makem2(rb*0.2 + atan(p.y,p.x) * 2.4);
  float rz = fbm(p*0.9 - time*0.7, 5) * 12.0;
  rz *= abs(sin(bp.x*0.5 - time*4.0 - 2.0));
  vec3 col = uColor / (1.15 - rz);
  gl_FragColor = vec4(sqrt(abs(col)), 1.0);
}`;
const VERT = "attribute vec2 a; void main(){ gl_Position = vec4(a, 0.0, 1.0); }";

/** Starts the mist on `canvas`; returns its stop function, or undefined when the plain mark should stay. */
export function startMist(canvas: HTMLCanvasElement): (() => void) | undefined {
  const motion = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  if (motion?.matches) return undefined;
  const gl = canvas.getContext("webgl", { premultipliedAlpha: false });
  if (!gl) return undefined;
  const prog = gl.createProgram();
  if (!prog) return undefined;
  const shaders: WebGLShader[] = [];
  // Full teardown: a valid linker assignment or any setup failure must leave the
  // static SVG mark in charge, never a blank canvas or leaked GL objects.
  const bail = () => {
    for (const sh of shaders) {
      gl.detachShader(prog, sh);
      gl.deleteShader(sh);
    }
    gl.deleteProgram(prog);
    return undefined;
  };
  for (const [type, src] of [
    [gl.VERTEX_SHADER, VERT],
    [gl.FRAGMENT_SHADER, FRAG],
  ] as const) {
    const sh = gl.createShader(type);
    if (!sh) return bail();
    shaders.push(sh);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) return bail();
    gl.attachShader(prog, sh);
  }
  // Pin `a` to slot 0 by contract, then honor whatever the linker actually did.
  gl.bindAttribLocation(prog, 0, "a");
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return bail();
  const attrib = gl.getAttribLocation(prog, "a");
  if (attrib < 0) return bail();
  gl.useProgram(prog);
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(attrib);
  gl.vertexAttribPointer(attrib, 2, gl.FLOAT, false, 0, 0);
  const uTime = gl.getUniformLocation(prog, "uTime");
  const uRes = gl.getUniformLocation(prog, "uRes");
  gl.uniform3f(gl.getUniformLocation(prog, "uColor"), 0.3, 0.3, 0.3); // neutral grey; the shader blows hot spots out to white

  let frame = 0;
  let running = true;
  let disposed = false;
  const start = performance.now();
  const draw = () => {
    if (!running || disposed) return;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
      gl.viewport(0, 0, w, h);
    }
    gl.uniform2f(uRes, w, h);
    gl.uniform1f(uTime, ((performance.now() - start) / 1000) * 1.8);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    frame = requestAnimationFrame(draw);
  };
  const onMotionChange = (event: MediaQueryListEvent) => {
    if (event.matches) {
      running = false;
      cancelAnimationFrame(frame);
    } else if (!disposed && !running) {
      running = true;
      draw();
    }
  };
  motion?.addEventListener?.("change", onMotionChange);
  draw();
  return () => {
    disposed = true;
    running = false;
    motion?.removeEventListener?.("change", onMotionChange);
    cancelAnimationFrame(frame);
    gl.deleteBuffer(buffer);
    for (const sh of shaders) gl.deleteShader(sh);
    gl.deleteProgram(prog);
    gl.getExtension("WEBGL_lose_context")?.loseContext();
  };
}

/**
 * The Kollabor K mark from the kollabor.ai navbar (k-logo-dark.svg), in currentColor.
 * `mist` paints Mentiko's electric mist inside the letter; the plain mark stays
 * underneath for reduced motion or no WebGL.
 */
export function KollabLogo({ className, mist = false }: { className?: string; mist?: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!mist || !canvas) return;
    return startMist(canvas);
  }, [mist]);

  const svg = (
    <svg aria-hidden="true" viewBox={VIEW_BOX} className={mist ? "h-full w-auto" : className} fill="currentColor">
      {PATHS.map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
  if (!mist) return svg;

  return (
    <span className={cn("relative inline-block", className)}>
      {svg}
      <canvas
        ref={canvasRef}
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 h-full w-full"
        style={{ maskImage: MASK, WebkitMaskImage: MASK, maskSize: "100% 100%", WebkitMaskSize: "100% 100%" }}
      />
    </span>
  );
}
