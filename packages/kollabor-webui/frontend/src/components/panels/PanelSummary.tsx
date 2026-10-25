import { Fragment, useEffect, useRef, useState } from "react";
import { Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { summaryRows, type PanelSummaryRow } from "./panel-model";

/** Read-only panel facts; copying is opt-in metadata, never inferred from text. */
export function PanelSummary({
  summary,
  className,
}: {
  summary: unknown;
  className?: string;
}) {
  const rows = summaryRows(summary);
  const attempt = useRef(0);
  const [notice, setNotice] = useState<{
    label: string;
    value: string;
    text: string;
    ok: boolean;
  } | null>(null);
  useEffect(() => () => { attempt.current += 1; }, []);

  const copy = async (row: PanelSummaryRow) => {
    if (row.copyable !== true || !row.value.trim()) return;
    const mine = ++attempt.current;
    setNotice(null);
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(row.value);
      if (mine !== attempt.current) return;
      setNotice({ ...row, text: `${row.label} copied.`, ok: true });
    } catch {
      if (mine !== attempt.current) return;
      setNotice({
        ...row,
        text: `Could not copy ${row.label}. Select the value to copy it.`,
        ok: false,
      });
    }
  };

  if (!rows.length) return null;
  const visibleNotice = notice && rows.some(
    (row) => row.label === notice.label && row.value === notice.value,
  ) ? notice : null;
  return (
    <div className={cn("min-w-0 rounded-lg bg-muted/30 p-3", className)}>
      <dl className="grid grid-cols-[minmax(0,8.5rem)_minmax(0,1fr)] items-start gap-x-3 gap-y-2 text-sm">
        {rows.map((row, index) => (
          <Fragment key={`${row.label}-${index}`}>
            <dt className="text-muted-foreground break-words">{row.label}</dt>
            <dd className="flex min-w-0 items-start gap-1.5">
              <span className="min-w-0 break-words [overflow-wrap:anywhere]">
                {row.value || "—"}
              </span>
              {row.copyable === true && row.value.trim() ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  className="-mt-0.5 shrink-0 text-muted-foreground"
                  aria-label={`Copy ${row.label}`}
                  title={`Copy ${row.label}`}
                  onClick={() => void copy(row)}
                >
                  <Copy aria-hidden="true" />
                </Button>
              ) : null}
            </dd>
          </Fragment>
        ))}
      </dl>
      {visibleNotice ? (
        <p
          role="status"
          className={cn("mt-2 text-xs break-words", visibleNotice.ok ? "text-muted-foreground" : "text-destructive")}
        >
          {visibleNotice.text}
        </p>
      ) : null}
    </div>
  );
}
