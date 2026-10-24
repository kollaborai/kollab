import { useState } from "react";
import { TriangleAlert, X } from "lucide-react";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { supervisorAlerts, type StreamEvent } from "@/supervisor-alerts";

/** Plain alert rows for the hub idle supervisor's `supervisor_alert` events. */
export function SupervisorAlertBanner({ events }: { events: ReadonlyArray<StreamEvent> }) {
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(new Set());
  const alerts = supervisorAlerts(events).filter((alert) => !dismissed.has(alert.key));
  if (alerts.length === 0) return null;
  return (
    <div role="alert" className="flex shrink-0 flex-col gap-1 bg-card px-4 py-2 text-sm sm:px-6">
      {alerts.slice(0, 3).map((alert) => (
        <div key={alert.key} className="flex items-start gap-2">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden="true" />
          <p className="min-w-0 flex-1 break-words text-foreground/90">{alert.message}</p>
          <TooltipIconButton
            tooltip="Dismiss alert"
            className="size-6"
            onClick={() => setDismissed((prev) => new Set(prev).add(alert.key))}
          >
            <X className="size-3.5" aria-hidden="true" />
          </TooltipIconButton>
        </div>
      ))}
    </div>
  );
}
