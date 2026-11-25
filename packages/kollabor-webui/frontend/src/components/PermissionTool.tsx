import { createContext, useContext, useState } from "react";
import { makeAssistantToolUI } from "@assistant-ui/react";
import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export type PermissionArgs = {
  permission_id?: string;
  acp_options?: Array<{ optionId: string; name: string; kind: string }>;
  details?: {
    acp_options?: Array<{ optionId: string; name: string; kind: string }>;
    operation?: { kind?: string };
  };
  tool_id?: string;
  tool_name?: string;
  tool_type?: string;
  risk_level?: string;
  risk_reason?: string;
  input?: Record<string, unknown>;
};

export type PermissionResult = {
  decision: "approve" | "deny";
  tool_id?: string;
  permission_id?: string;
  scope?: "once" | "session" | "project" | "always_edits" | "trust_tool";
  option_id?: string;
  vendor_saved?: boolean;
  warning?: string;
};

export type PermissionResponseHandler = (
  toolCallId: string,
  args: PermissionArgs,
  result: PermissionResult,
) => Promise<"answered" | "stale">;

export const PermissionResponseContext = createContext<PermissionResponseHandler | null>(null);

const RISK_BADGE_VARIANT: Record<
  string,
  "destructive" | "default" | "secondary" | "outline"
> = {
  high: "destructive",
  medium: "default",
  low: "secondary",
};

export function PermissionPrompt({
  args,
  result,
  toolCallId,
}: ToolCallMessagePartProps<PermissionArgs, PermissionResult>) {
  const payload = args || {};
  // The tool-call row decides the outcome. The daemon writes `result` once it
  // records the answer, so every client shows the same state. Until then the
  // card is pending.
  const decided: PermissionResult | undefined = result ?? undefined;
  // The one in-flight flag. It stops a second send while the decision is out
  // (a double click), and it stays set after an answer, so the card stays
  // locked until the row carries the result.
  const [submitting, setSubmitting] = useState(false);
  const [stale, setStale] = useState(false);
  const [submissionError, setSubmissionError] = useState(false);
  const respond = useContext(PermissionResponseContext);
  const riskLevel = (payload.risk_level || "unknown").toLowerCase();
  const options = payload.acp_options ?? payload.details?.acp_options ?? [];

  const submit = async (selected: PermissionResult) => {
    if (decided || submitting || stale || submissionError) return;
    setSubmitting(true);
    // The full decision body: the prompt's owner ids plus the chosen answer.
    const body: PermissionResult = {
      ...selected,
      ...(payload.tool_id ? { tool_id: payload.tool_id } : {}),
      ...(payload.permission_id ? { permission_id: payload.permission_id } : {}),
    };
    try {
      if (!respond) throw new Error("No permission responder is mounted.");
      const outcome = await respond(toolCallId, payload, body);
      if (outcome === "stale") {
        setStale(true);
        setSubmitting(false);
      } else if (outcome !== "answered") {
        // Any other outcome means the decision was not sent. Show the failure
        // rather than leave the card locked.
        throw new Error(`Unexpected permission outcome: ${String(outcome)}`);
      }
    } catch {
      // A failed response may have reached the engine. Lock this card until a
      // refresh establishes whether the prompt is still pending.
      setSubmitting(false);
      setSubmissionError(true);
    }
  };
  const disabled = submitting || stale || submissionError;
  const choose = (decision: PermissionResult["decision"], scope: PermissionResult["scope"]) => {
    void submit({ decision, scope });
  };

  return (
    <Card className="min-w-0 w-full max-w-md">
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2 text-base">
          <span className="min-w-0 wrap-anywhere">Permission required: {payload.tool_name || "tool"}</span>
          <Badge className="shrink-0" variant={RISK_BADGE_VARIANT[riskLevel] ?? "outline"}>
            {riskLevel} risk
          </Badge>
        </CardTitle>
        {payload.tool_type ? (
          <CardDescription>{payload.tool_type}</CardDescription>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-sm">
        {payload.risk_reason ? (
          <p className="text-muted-foreground">{payload.risk_reason}</p>
        ) : null}
        {payload.input ? (
          <pre className="overflow-x-auto rounded-md bg-muted/50 p-2.5 text-xs whitespace-pre-wrap">
            {JSON.stringify(payload.input, null, 2)}
          </pre>
        ) : null}
        {decided ? (
          <div className="flex flex-col gap-1">
            <p className="font-medium text-muted-foreground">
              {decided.decision === "approve" ? "Allowed" : "Denied"}
              {decided.scope ? ` (${decided.scope})` : ""}
            </p>
            {decided.vendor_saved || decided.warning ? (
              <p className="text-amber-700 dark:text-amber-300">
                {decided.warning || "Saved by the ACP provider; Kollab cannot undo it."}
              </p>
            ) : null}
          </div>
        ) : null}
        {stale ? <p role="status" className="text-destructive text-xs">This permission request is no longer pending.</p> : null}
        {submissionError ? <p role="status" className="text-destructive text-xs">Couldn't send the decision. Refresh to check whether it was applied.</p> : null}
        {submitting && !decided ? <p role="status" className="text-muted-foreground text-xs">Sending decision…</p> : null}
      </CardContent>
      {decided ? null : <CardFooter className="flex flex-wrap gap-2">
        {options.length > 0 ? options.map((option) => (
          <Button key={option.optionId} type="button" size="sm" disabled={disabled}
            className="h-auto min-h-8 max-w-full py-2 text-left whitespace-normal wrap-anywhere"
            variant={option.kind.startsWith("reject") ? "destructive" : "default"}
            title={option.kind.endsWith("always") ? "Saved by the harness; Kollab cannot undo it" : option.name}
            onClick={() => void submit({
                decision: option.kind.startsWith("allow") ? "approve" : "deny",
                scope: "once", option_id: option.optionId,
                vendor_saved: option.kind.endsWith("always"),
                warning: option.kind.endsWith("always") ? "Saved by the harness; Kollab cannot undo it" : undefined,
              })}>{option.name}</Button>
        )) : <>
        <Button
          type="button"
          size="sm"
          disabled={disabled}
          onClick={() => choose("approve", "once")}
        >
          Allow once
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={disabled}
          onClick={() => choose("approve", "session")}
        >
          Allow session
        </Button>
        <Button
          type="button"
          size="sm"
          variant="destructive"
          disabled={disabled}
          onClick={() => choose("deny", "once")}
        >
          Deny
        </Button>
        </>}
        {options.filter((option) => option.kind === "allow_once").length === 1 && payload.details?.operation?.kind && <>
          <Button type="button" size="sm" variant="outline" disabled={disabled} onClick={() => choose("approve", "session")}>Allow Session</Button>
          <Button type="button" size="sm" variant="outline" disabled={disabled} onClick={() => choose("approve", "project")}>Allow Project</Button>
          {payload.details.operation.kind === "edit" && <Button type="button" size="sm" variant="outline" disabled={disabled} onClick={() => choose("approve", "always_edits")}>Always Allow Edits</Button>}
        </>}
      </CardFooter>}
    </Card>
  );
}

export const PermissionToolUI = makeAssistantToolUI<
  PermissionArgs,
  PermissionResult
>({
  toolName: "request_permission",
  display: "standalone",
  render: PermissionPrompt,
});
