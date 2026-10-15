import { TerminalSquare } from "lucide-react";
import codex from "@/components/icons/brands/codex-color.svg";
import claude from "@/components/icons/brands/claude-color.svg";
import gemini from "@/components/icons/brands/gemini-color.svg";
import { cn } from "@/lib/utils";
import type { CliHarness } from "@/utils/cli-harness";

const marks = { codex, claude, gemini };

export function CliBadge({ harness, className }: { harness: CliHarness | null; className?: string }) {
  if (!harness) return null;
  return (
    <span
      className={cn("inline-flex min-w-0 items-center gap-1.5 whitespace-nowrap", className)}
      title={harness.label}
      aria-label={harness.label}
    >
      {harness.brand ? (
        <img src={marks[harness.brand]} alt="" className="size-3.5 shrink-0" />
      ) : (
        <TerminalSquare className="size-3.5 shrink-0" aria-hidden="true" />
      )}
      <span className="truncate">{harness.label}</span>
    </span>
  );
}
