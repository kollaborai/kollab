/**
 * terminal-viewer.tsx - xterm.js view for one terminal connection.
 *
 * The connection (from useTerminalWsConnection) owns the socket. This view draws the output it
 * receives, sends the keystrokes typed into it, and reports the grid size whenever its box changes.
 * The shell's state (connecting, ended, error) is drawn by the caller.
 */

import { useEffect, useRef } from "react";
import type { Terminal as XTerminal } from "@xterm/xterm";
import type { FitAddon as FitAddonType } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

import type { TerminalWsConnection } from "@/components/terminal/use-terminal-ws-connection";
import { cn } from "@/lib/utils";

export interface TerminalViewerProps {
  connection: TerminalWsConnection;
  className?: string;
}

// xterm needs concrete colors for colored output, so the ANSI palette is fixed here.
const ANSI_PALETTE = {
  black: "#1a1a1a",
  red: "#f87171",
  green: "#4ade80",
  yellow: "#fbbf24",
  blue: "#60a5fa",
  magenta: "#c084fc",
  cyan: "#22d3ee",
  white: "#e5e5e5",
  brightBlack: "#525252",
  brightRed: "#fca5a5",
  brightGreen: "#86efac",
  brightYellow: "#fcd34d",
  brightBlue: "#93c5fd",
  brightMagenta: "#d8b4fe",
  brightCyan: "#67e8f9",
  brightWhite: "#fafafa",
};

/**
 * A theme variable as a color xterm can parse. The canvas turns any CSS color (oklch included)
 * into #rrggbb or rgba(), so the value is read from the app's own tokens.
 */
function themeColor(scope: HTMLElement, name: string): string | undefined {
  const raw = getComputedStyle(scope).getPropertyValue(name).trim();
  if (!raw) return undefined;
  const context = document.createElement("canvas").getContext("2d");
  if (!context) return undefined;
  context.fillStyle = raw;
  return typeof context.fillStyle === "string" ? context.fillStyle : undefined;
}

function terminalTheme(scope: HTMLElement) {
  const background = themeColor(scope, "--background") ?? "#1a1a1a";
  const foreground = themeColor(scope, "--foreground") ?? "#e5e5e5";
  return {
    ...ANSI_PALETTE,
    background,
    foreground,
    cursor: foreground,
    cursorAccent: background,
    selectionBackground: "#404040",
    selectionForeground: foreground,
  };
}

export function TerminalViewer({ connection, className }: TerminalViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const { send, resize, onOutput, onExit } = connection;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    // Narrowed once here: the function declaration below does not keep the null check.
    const host: HTMLDivElement = container;

    let disposed = false;
    let term: XTerminal | null = null;
    let fit: FitAddonType | null = null;
    let frame = 0;
    let lastSize = "";
    // Output that arrives before xterm has loaded waits here, then is written in order.
    const early: string[] = [];
    const write = (text: string) => {
      if (term) term.write(text);
      else early.push(text);
    };

    const unsubscribeOutput = onOutput(write);
    const unsubscribeExit = onExit(() => write("\r\n\u001b[2m[shell ended]\u001b[0m\r\n"));

    // Refit to the box, and send the grid size only when it changed.
    const fitToBox = () => {
      if (disposed || !term || !fit) return;
      fit.fit();
      const dims = fit.proposeDimensions();
      if (!dims) return;
      const key = `${dims.cols}x${dims.rows}`;
      if (key === lastSize) return;
      lastSize = key;
      resize(dims.cols, dims.rows);
    };

    const observer = new ResizeObserver(fitToBox);
    observer.observe(container);

    async function mount(): Promise<void> {
      // dynamic imports keep xterm out of the main bundle
      const [{ Terminal }, { FitAddon }] = await Promise.all([
        import("@xterm/xterm"),
        import("@xterm/addon-fit"),
      ]);
      if (disposed) return;

      const created = new Terminal({
        cursorBlink: true,
        cursorStyle: "bar",
        fontSize: 11,
        fontFamily: "ui-monospace, Menlo, monospace",
        lineHeight: 1.0,
        scrollback: 10000,
        theme: terminalTheme(host),
      });
      const addon = new FitAddon();
      created.loadAddon(addon);
      created.open(host);
      term = created;
      fit = addon;
      created.onData((data) => send({ type: "input", data }));
      for (const text of early.splice(0)) created.write(text);
      frame = requestAnimationFrame(fitToBox);
    }

    void mount().catch((error: unknown) => {
      console.error("terminal failed to load", error);
    });

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      unsubscribeOutput();
      unsubscribeExit();
      fit?.dispose();
      term?.dispose();
      term = null;
      fit = null;
    };
  }, [send, resize, onOutput, onExit]);

  return (
    <div className={cn("absolute inset-0 overflow-hidden", className)}>
      <div ref={containerRef} className="absolute inset-0 overflow-hidden" />
    </div>
  );
}
