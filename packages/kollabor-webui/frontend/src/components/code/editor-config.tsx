import { useEditorStore } from "@/components/code/editor-store";

export function EditorConfigPanel() {
  const config = useEditorStore((s) => s.editorConfig);
  const update = useEditorStore((s) => s.updateEditorConfig);

  return (
    <div className="flex flex-col h-full bg-muted overflow-y-auto">
      <div className="px-3 py-2 shrink-0">
        <span className="text-[10px] font-mono text-foreground/40 uppercase tracking-wider">
          editor settings
        </span>
      </div>

      <div className="flex flex-col gap-3 px-3 py-2">
        {/* font size */}
        <ConfigRow label="Font Size">
          <div className="flex items-center gap-2">
            <button
              onClick={() => update({ fontSize: Math.max(10, config.fontSize - 1) })}
              className="w-5 h-5 flex items-center justify-center rounded-sm bg-accent hover:bg-accent/80 text-xs text-foreground/70"
            >
              -
            </button>
            <span className="text-xs font-mono text-foreground/70 w-5 text-center">
              {config.fontSize}
            </span>
            <button
              onClick={() => update({ fontSize: Math.min(24, config.fontSize + 1) })}
              className="w-5 h-5 flex items-center justify-center rounded-sm bg-accent hover:bg-accent/80 text-xs text-foreground/70"
            >
              +
            </button>
          </div>
        </ConfigRow>

        {/* tab size */}
        <ConfigRow label="Tab Size">
          <div className="flex items-center gap-1">
            {[2, 4, 8].map((size) => (
              <button
                key={size}
                onClick={() => update({ tabSize: size })}
                className={`px-2 py-0.5 text-xs rounded-sm transition-colors ${
                  config.tabSize === size
                    ? "bg-accent text-foreground"
                    : "text-foreground/40 hover:text-foreground/60"
                }`}
              >
                {size}
              </button>
            ))}
          </div>
        </ConfigRow>

        {/* word wrap */}
        <ConfigRow label="Word Wrap">
          <div className="flex items-center gap-1">
            {(["off", "on"] as const).map((val) => (
              <button
                key={val}
                onClick={() => update({ wordWrap: val })}
                className={`px-2 py-0.5 text-xs rounded-sm transition-colors ${
                  config.wordWrap === val
                    ? "bg-accent text-foreground"
                    : "text-foreground/40 hover:text-foreground/60"
                }`}
              >
                {val}
              </button>
            ))}
          </div>
        </ConfigRow>

        {/* minimap */}
        <ConfigRow label="Minimap">
          <Toggle
            checked={config.minimap}
            onChange={(v) => update({ minimap: v })}
          />
        </ConfigRow>

        {/* line numbers */}
        <ConfigRow label="Line Numbers">
          <div className="flex items-center gap-1">
            {(["on", "off", "relative"] as const).map((val) => (
              <button
                key={val}
                onClick={() => update({ lineNumbers: val })}
                className={`px-2 py-0.5 text-xs rounded-sm transition-colors ${
                  config.lineNumbers === val
                    ? "bg-accent text-foreground"
                    : "text-foreground/40 hover:text-foreground/60"
                }`}
              >
                {val}
              </button>
            ))}
          </div>
        </ConfigRow>

        {/* whitespace */}
        <ConfigRow label="Whitespace">
          <div className="flex items-center gap-1">
            {(["none", "boundary", "all"] as const).map((val) => (
              <button
                key={val}
                onClick={() => update({ renderWhitespace: val })}
                className={`px-2 py-0.5 text-xs rounded-sm transition-colors ${
                  config.renderWhitespace === val
                    ? "bg-accent text-foreground"
                    : "text-foreground/40 hover:text-foreground/60"
                }`}
              >
                {val}
              </button>
            ))}
          </div>
        </ConfigRow>
      </div>
    </div>
  );
}

function ConfigRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-xs text-foreground/50">{label}</span>
      {children}
    </div>
  );
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!checked)}
      className={`w-8 h-4 rounded-sm relative transition-colors ${
        checked ? "bg-accent" : "bg-card"
      }`}
    >
      <div
        className={`absolute top-0.5 w-3 h-3 rounded-sm bg-foreground/70 transition-all ${
          checked ? "left-4" : "left-0.5"
        }`}
      />
    </button>
  );
}
