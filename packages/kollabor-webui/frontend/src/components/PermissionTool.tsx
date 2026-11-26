import { createContext, useContext, useState } from "react";
import { makeAssistantDataUI, makeAssistantToolUI } from "@assistant-ui/react";
import type { DataMessagePartProps, ToolCallMessagePartProps } from "@assistant-ui/react";
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
  request_id?: string;
  source?: string;
  parked?: boolean;
  permission_id?: string;
  acp_options?: Array<{ optionId: string; name: string; kind: string }>;
  operation?: { kind?: string };
  details?: {
    acp_options?: Array<{ optionId: string; name: string; kind: string }>;
    operation?: { kind?: string };
    source?: string;
    parked?: boolean;
    request_id?: string;
  };
  tool_id?: string;
  tool_name?: string;
  title?: string;
  tool_type?: string;
  risk_level?: string;
  risk_reason?: string;
  input?: Record<string, unknown>;
  command?: string;
  file_path?: string;
  server_name?: string;
  content_preview?: string;
  resolution?: {
    type?: string;
    outcome?: string;
    scope?: string;
    option_id?: string;
    vendor_saved?: boolean;
    warning?: string;
  };
};

export type PermissionResult = {
  decision: "approve" | "deny";
  tool_id?: string;
  permission_id?: string;
  scope?: "once" | "session" | "project" | "always_edits" | "trust_tool";
  option_id?: string;
  vendor_saved?: boolean;
  warning?: string;
  outcome?: string;
};

export type PermissionResponseHandler = (
  promptPartId: string,
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

type PermissionPromptCardProps = {
  partId: string;
  args: PermissionArgs;
  result?: PermissionResult;
  resolved?: boolean;
};

function PermissionPromptCard({ partId, args, result, resolved = false }: PermissionPromptCardProps) {
  const payload = args || {};
  // The tool-call row decides the outcome. The daemon writes `result` once it
  // records the answer, so every client shows the same state. Until then the
  // card is pending.
  const decided: PermissionResult | undefined = result ?? undefined;
  const isResolved = resolved || decided !== undefined;
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
    if (isResolved || submitting || stale || submissionError) return;
    setSubmitting(true);
    // The full decision body: the prompt's owner ids plus the chosen answer.
    const body: PermissionResult = {
      ...selected,
      ...(payload.tool_id ? { tool_id: payload.tool_id } : {}),
      ...(payload.permission_id ? { permission_id: payload.permission_id } : {}),
    };
    try {
      if (!respond) throw new Error("No permission responder is mounted.");
      const outcome = await respond(partId, payload, body);
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
              {decided.outcome === "cancelled" ? "Cancelled" : decided.decision === "approve" ? "Allowed" : "Denied"}
              {decided.scope ? ` (${decided.scope})` : ""}
            </p>
            {decided.vendor_saved || decided.warning ? (
              <p className="text-amber-700 dark:text-amber-300">
                {decided.warning || "Saved by the ACP provider; Kollab cannot undo it."}
              </p>
            ) : null}
          </div>
        ) : null}
        {isResolved && !decided ? (
          <p className="font-medium text-muted-foreground">Permission resolved</p>
        ) : null}
        {stale ? <p role="status" className="text-destructive text-xs">This permission request is no longer pending.</p> : null}
        {submissionError ? <p role="status" className="text-destructive text-xs">Couldn't send the decision. Refresh to check whether it was applied.</p> : null}
        {submitting && !decided ? <p role="status" className="text-muted-foreground text-xs">Sending decision…</p> : null}
      </CardContent>
      {isResolved ? null : <CardFooter className="flex flex-wrap gap-2">
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
        {(payload.operation?.kind ?? payload.details?.operation?.kind) && options.filter((option) => option.kind === "allow_once").length === 1 && <>
          <Button type="button" size="sm" variant="outline" disabled={disabled} onClick={() => choose("approve", "session")}>Allow Session</Button>
          <Button type="button" size="sm" variant="outline" disabled={disabled} onClick={() => choose("approve", "project")}>Allow Project</Button>
          {(payload.operation?.kind ?? payload.details?.operation?.kind) === "edit" && <Button type="button" size="sm" variant="outline" disabled={disabled} onClick={() => choose("approve", "always_edits")}>Always Allow Edits</Button>}
        </>}
      </CardFooter>}
    </Card>
  );
}

export function PermissionPrompt({
  args,
  result,
  toolCallId,
}: ToolCallMessagePartProps<PermissionArgs, PermissionResult>) {
  return <PermissionPromptCard partId={toolCallId} args={args || {}} result={result ?? undefined} />;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function permissionArgsFromData(value: unknown): PermissionArgs {
  if (!isRecord(value)) return {};
  const suppliedInput = isRecord(value.input) ? value.input : undefined;
  const input = suppliedInput ?? Object.fromEntries(
    ["command", "file_path", "server_name", "content_preview"]
      .flatMap((key) => typeof value[key] === "string" ? [[key, value[key]]] : []),
  );
  return {
    ...value,
    tool_name: typeof value.tool_name === "string" && value.tool_name
      ? value.tool_name
      : typeof value.title === "string" && value.title ? value.title : undefined,
    input: Object.keys(input).length > 0 ? input : undefined,
  } as PermissionArgs;
}

function permissionResultFromData(value: unknown): PermissionResult | undefined {
  if (!isRecord(value) || !isRecord(value.resolution)) return undefined;
  const resolution = value.resolution;
  if (resolution.type !== "permission_granted" && resolution.type !== "permission_denied" && resolution.type !== "permission_cancelled") {
    return undefined;
  }
  const scope = ["once", "session", "project", "always_edits", "trust_tool"].includes(String(resolution.scope))
    ? resolution.scope as PermissionResult["scope"]
    : undefined;
  return {
    decision: resolution.type === "permission_granted" ? "approve" : "deny",
    ...(scope ? { scope } : {}),
    ...(typeof resolution.option_id === "string" ? { option_id: resolution.option_id } : {}),
    ...(resolution.vendor_saved === true ? { vendor_saved: true } : {}),
    ...(typeof resolution.warning === "string" ? { warning: resolution.warning } : {}),
    ...(resolution.outcome === "cancelled" || resolution.type === "permission_cancelled" ? { outcome: "cancelled" } : {}),
  };
}

/** Renders the `data-prompt` part written by the daemon's chat-stream producer. */
export function PermissionPromptData({ id, data }: DataMessagePartProps<unknown>) {
  if (!id) return null;
  const payload = permissionArgsFromData(data);
  const hasResolution = isRecord(data) && Object.hasOwn(data, "resolution");
  return (
    <PermissionPromptCard
      partId={id}
      args={payload}
      result={permissionResultFromData(data)}
      resolved={hasResolution}
    />
  );
}

export const PermissionPromptDataUI = makeAssistantDataUI<unknown>({
  name: "prompt",
  render: PermissionPromptData,
});

export const PermissionToolUI = makeAssistantToolUI<
  PermissionArgs,
  PermissionResult
>({
  toolName: "request_permission",
  display: "standalone",
  render: PermissionPrompt,
});
