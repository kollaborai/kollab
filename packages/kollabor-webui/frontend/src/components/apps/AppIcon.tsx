import type { AppRow } from "@/api";
import { cn } from "@/lib/utils";

/** A brand color too light for the white tile (white, yellow) draws dark instead. */
function glyphFill(hex: string): string {
  const n = Number.parseInt(hex, 16);
  if (!/^[0-9a-f]{6}$/i.test(hex) || Number.isNaN(n)) return "#111827";
  const [r, g, b] = [n >> 16, (n >> 8) & 255, n & 255];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 200 ? "#111827" : `#${hex}`;
}

/**
 * The brand glyph on a white tile, or the first letter on a muted tile when the
 * row has no icon. `sm` is the header's stacked strip.
 */
export function AppIcon({
  row,
  size = "lg",
}: {
  row: Pick<AppRow, "label" | "icon">;
  size?: "lg" | "sm";
}) {
  const tile = cn(
    "flex shrink-0 items-center justify-center",
    size === "lg" ? "size-12 rounded-xl" : "size-6 rounded-md ring-2 ring-background",
  );
  if (!row.icon) {
    return (
      <span
        aria-hidden="true"
        className={cn(
          tile,
          "bg-muted text-muted-foreground font-semibold",
          size === "lg" ? "text-lg" : "text-xs",
        )}
      >
        {row.label.trim().charAt(0).toUpperCase()}
      </span>
    );
  }
  return (
    <span aria-hidden="true" className={cn(tile, "bg-white")}>
      <svg viewBox="0 0 24 24" className={size === "lg" ? "size-6" : "size-3.5"}>
        <path d={row.icon.path} fill={glyphFill(row.icon.hex)} />
      </svg>
    </span>
  );
}
