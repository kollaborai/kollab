/**
 * #1174 scrollbar-chrome fixture page. Mounts the REAL scroll surfaces the app
 * ships — the shared Radix ScrollArea/ScrollBar, a real Radix Dialog (portal),
 * the real TerminalViewer over a stub connection (synthetic output, never a
 * real shell), and the same Monaco editor component editor-pane mounts with
 * the same scrollbar options factory — plus plain native scroll containers,
 * including nested ones. Purely test scaffolding: it renders no app shell.
 */
import { useState } from "react";
import { createRoot } from "react-dom/client";
import EditorReact, { type Monaco } from "@monaco-editor/react";
import { ScrollArea as ScrollAreaPrimitive } from "radix-ui";

import "../src/styles.css";
import "../src/components/code/monaco-setup";
import { ScrollArea, ScrollBar } from "../src/components/ui/scroll-area";
import { Dialog, DialogContent, DialogTrigger, DialogTitle } from "../src/components/ui/dialog";
import { TerminalViewer } from "../src/components/terminal/terminal-viewer";
// The scrollbar options factory shared with the production editor pane.
import { editorScrollbars } from "../src/components/code/editor-pane";

function NativeBoth() {
  return (
    <div
      data-testid="native-both"
      tabIndex={0}
      style={{ width: 300, height: 200, overflow: "auto", border: "1px solid #888" }}
    >
      <div style={{ width: 900, height: 800, position: "relative" }}>
        {Array.from({ length: 40 }, (_, i) => (
          <div key={i} style={{ position: "absolute", top: i * 20, left: (i % 3) * 250 }}>
            row {i}
          </div>
        ))}
        <div id="native-anchor" style={{ position: "absolute", bottom: 0, right: 0 }}>
          corner
        </div>
      </div>
    </div>
  );
}

function NativeBorderless() {
  return (
    <div
      data-testid="native-borderless"
      tabIndex={0}
      style={{ width: 300, height: 200, overflow: "auto" }}
    >
      <div style={{ width: 700, height: 800 }}>borderless scroll content</div>
    </div>
  );
}

function NativeNested() {
  return (
    <div data-testid="native-nested-outer" tabIndex={0} style={{ width: 300, height: 200, overflow: "auto", border: "1px solid #888" }}>
      <div style={{ width: 500, height: 600, padding: 4 }}>
        <div data-testid="native-nested-inner" tabIndex={0} style={{ width: 240, height: 120, overflow: "auto", border: "1px dashed #888" }}>
          <div style={{ width: 600, height: 400 }}>deep content</div>
        </div>
        <div style={{ height: 500 }}>filler</div>
      </div>
    </div>
  );
}

function RadixVertical() {
  return (
    <ScrollArea data-testid="radix-v" style={{ width: 300, height: 200 }}>
      <div style={{ padding: 4 }}>
        {Array.from({ length: 60 }, (_, i) => (
          <div key={i} style={{ height: 24 }}>
            session row {i}
          </div>
        ))}
      </div>
    </ScrollArea>
  );
}

function RadixHorizontal() {
  return (
    <ScrollAreaPrimitive.Root
      data-testid="radix-h"
      className="relative"
      style={{ width: 300, height: 80, overflow: "hidden" }}
    >
      <ScrollAreaPrimitive.Viewport className="h-full w-full">
        <div style={{ width: 1200, padding: 4 }}>
          {Array.from({ length: 3 }, (_, i) => (
            <div key={i} style={{ height: 20, width: 1000 }}>
              wide row {i}
            </div>
          ))}
        </div>
      </ScrollAreaPrimitive.Viewport>
      <ScrollBar orientation="horizontal" />
      <ScrollAreaPrimitive.Corner />
    </ScrollAreaPrimitive.Root>
  );
}

function DialogSurface() {
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger>open-dialog</DialogTrigger>
      <DialogContent>
        <DialogTitle>scrollbar dialog</DialogTitle>
        <div
          data-testid="dialog-scroll"
          tabIndex={0}
          style={{ maxHeight: 180, overflow: "auto", border: "1px solid #888" }}
        >
          {Array.from({ length: 80 }, (_, i) => (
            <p key={i} style={{ height: 20 }}>
              dialog paragraph {i}
            </p>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}

const LONG_CODE = Array.from(
  { length: 400 },
  (_, i) => `function line${i}(veryLongArgumentName, anotherVeryLongArgumentName) { return ${i} * ${i}; } // pad pad pad pad pad pad pad pad pad pad`,
).join("\n");

function EditorSurface() {
  const [editor, setEditor] = useState<unknown>(null);
  return (
    <div data-testid="editor-box" style={{ width: 400, height: 200 }}>
      <EditorReact
        height="100%"
        defaultLanguage="typescript"
        defaultValue={LONG_CODE}
        theme="kollab-dark"
        onMount={(ed: unknown, _monaco: Monaco) => {
          setEditor(ed);
          (window as unknown as Record<string, unknown>).__editor = ed;
        }}
        options={{
          fontSize: 12,
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          overviewRulerBorder: false,
          hideCursorInOverviewRuler: true,
          // The production scrollbar contract, shared with editor-pane.tsx.
          ...editorScrollbars(),
        }}
      />
      <div data-has-editor={editor ? "yes" : "no"} />
    </div>
  );
}

const TERMINAL_FEED = Array.from({ length: 3000 }, (_, i) => `terminal line ${i}\r\n`).join("");

// Module-level stub: a per-render object would re-run the viewer effect on
// every parent re-render (e.g. when the editor mounts) and remount xterm
// empty. The stub identity stays stable for the page lifetime.
let terminalSink: ((text: string) => void) | null = null;
const terminalConnection = {
  send: () => {},
  resize: () => {},
  onOutput: (cb: (text: string) => void) => {
    terminalSink = cb;
    (window as unknown as Record<string, unknown>).__termWrite = (text: string) => cb(text);
    (window as unknown as Record<string, unknown>).__termFeed = () => cb(TERMINAL_FEED);
    return () => {};
  },
  onExit: () => () => {},
};

function TerminalSurface() {
  const connection = terminalConnection;
  return (
    <div data-testid="terminal-box" style={{ position: "relative", width: 400, height: 200 }}>
      <TerminalViewer connection={connection} className="h-full w-full" />
      <button
        type="button"
        onClick={() => terminalSink?.(TERMINAL_FEED)}
        style={{ position: "absolute", top: 0, right: 0, zIndex: 10 }}
      >
        feed-terminal
      </button>
    </div>
  );
}

function Page() {
  return (
    <div style={{ padding: 12, width: 1400 }}>
      <h1 style={{ fontSize: 16 }}>scrollbar chrome fixture (#1174)</h1>
      <p style={{ maxWidth: 600 }}>
        This filler exists so the document itself becomes a vertical scroller; the probe
        reads the classic-scrollbar gutter from documentElement too.
      </p>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
        <NativeBoth />
        <NativeBorderless />
        <NativeNested />
        <RadixVertical />
        <RadixHorizontal />
        <DialogSurface />
        <EditorSurface />
        <TerminalSurface />
      </div>
    </div>
  );
}

declare global {
  interface Window {
    __scrollbarProbes?: () => unknown;
    __termWrite?: (text: string) => void;
    __editor?: unknown;
    __ready?: boolean;
  }
}

const root = createRoot(document.getElementById("root")!);
root.render(<Page />);

// Probes read layout geometry, so they run after paint. visibility hidden and
// display none both count as "not visible chrome"; so does zero box size.
window.__scrollbarProbes = () => {
  const thickness = (el: Element) => {
    const cs = getComputedStyle(el);
    const bordersX = parseFloat(cs.borderLeftWidth) + parseFloat(cs.borderRightWidth);
    const bordersY = parseFloat(cs.borderTopWidth) + parseFloat(cs.borderBottomWidth);
    return {
      // offsetWidth/clientWidth delta includes the element's own borders; scrollbar chrome
      // is what remains after subtracting them.
      v: el.offsetWidth - el.clientWidth - bordersX,
      h: el.offsetHeight - el.clientHeight - bordersY,
    };
  };
  const visibleBox = (el: Element | null) => {
    if (!el) return { present: false, width: 0, height: 0, opacity: "0", display: "none", visibility: "hidden" };
    const cs = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return {
      present: true,
      width: rect.width,
      height: rect.height,
      opacity: cs.opacity,
      display: cs.display,
      visibility: cs.visibility,
    };
  };
  const q = (sel: string) => document.querySelector(sel);
  const visibleChrome = (el: Element | null) => {
    const box = visibleBox(el);
    return (
      box.present &&
      box.display !== "none" &&
      box.visibility !== "hidden" &&
      box.opacity !== "0" &&
      (box.width > 1 || box.height > 1)
    );
  };
  const byId = (id: string) => document.querySelector(`[data-testid="${id}"]`);
  const safe = (el: Element | null) => (el ? thickness(el) : { v: -1, h: -1, missing: true });
  const nativeBoth = byId("native-both");
  const doc = document.documentElement;
  return {
    document: { v: window.innerWidth - doc.clientWidth, h: window.innerHeight - doc.clientHeight },
    "native-both": safe(nativeBoth),
    "native-borderless": safe(byId("native-borderless")),
    "native-outer": safe(byId("native-nested-outer")),
    "native-inner": safe(byId("native-nested-inner")),
    "radix-v-scrollbar": visibleBox(q('[data-testid="radix-v"] [data-slot="scroll-area-scrollbar"]')),
    "radix-h-scrollbar": visibleBox(q('[data-testid="radix-h"] [data-slot="scroll-area-scrollbar"]')),
    "dialog-scroll": safe(byId("dialog-scroll")),
    "monaco-v-scrollbar": visibleChrome(
      q('[data-testid="editor-box"] .monaco-scrollable-element > .scrollbar.vertical'),
    ),
    "monaco-h-scrollbar": visibleChrome(
      q('[data-testid="editor-box"] .monaco-scrollable-element > .scrollbar.horizontal'),
    ),
    xterm: safe(q('[data-testid="terminal-box"] .xterm-viewport')),
    // Layout honesty: a hidden scrollbar must not leave a reserved gutter.
    "native-borderless-no-gutter": (() => {
      const el = byId("native-borderless");
      return el ? el.clientWidth === el.getBoundingClientRect().width : false;
    })(),
  };
};

requestAnimationFrame(() => {
  window.__ready = true;
});
