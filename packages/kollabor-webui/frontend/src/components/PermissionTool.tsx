import { createContext, useContext, useEffect, useState } from "react";
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
import {
  ChatStreamContentPreview,
  chatStreamArgsPreviewFromMetadata,
  chatStreamResultPreviewFromMetadata,
  chatStreamContentIdentity,
  type ChatStreamContentPreviewMarker,
} from "@/chat-stream-content";
import { ToolFallbackResult } from "@/components/assistant-ui/tool-fallback";
import {
  CHAT_STREAM_PERMISSION_PROMPT_PREVIEW_KIND,
  isChatStreamContentPreviewMarker,
  isChatStreamPartPreviewMarker,
  type ChatStreamToolOutputPreviewMarker,
  type ChatStreamPartPreviewMarker,
} from "@/chat-stream";
import {
  markPermissionPromptPreviewVisible,
  registerRecoveredPermissionPrompt,
} from "@/permission-preview-state";

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
  result?: PermissionResult | null;
  resultPreview?: ChatStreamToolOutputPreviewMarker;
  resolved?: boolean;
};

export type RecoveredPermissionRowContext = {
  sessionId: string;
  rowId: string;
  partIndex: number;
  rowRevision?: string;
  partRevision?: string;
};

function PermissionPromptCard({ partId, args, result, resultPreview, resolved = false }: PermissionPromptCardProps) {
  const payload = args || {};
  // The tool-call row decides the outcome. The daemon writes `result` once it
  // records the answer, so every client shows the same state. Until then the
  // card is pending.
  const decided: PermissionResult | undefined = isRecord(result) &&
      (result.decision === "approve" || result.decision === "deny")
    ? result as PermissionResult
    : undefined;
  const isResolved = resolved || result !== undefined || resultPreview !== undefined;
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
        {resultPreview ? <ToolFallbackResult resultPreview={resultPreview} /> : null}
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
  providerMetadata,
}: ToolCallMessagePartProps<PermissionArgs, PermissionResult>) {
  const marker = chatStreamArgsPreviewFromMetadata(providerMetadata);
  const candidateResultPreview = chatStreamResultPreviewFromMetadata(providerMetadata);
  const resultPreview = candidateResultPreview?.tool_call_id === toolCallId
    ? candidateResultPreview
    : undefined;
  if (hasChatStreamArgsPreviewMetadata(providerMetadata)) {
    return marker
      ? <PermissionArgsPreview id={toolCallId} marker={marker} result={result} resultPreview={resultPreview} />
      : <p role="alert" className="text-destructive text-xs">The complete permission request could not be verified. Refresh before deciding.</p>;
  }
  return <PermissionPromptCard partId={toolCallId} args={args || {}} result={result} resultPreview={resultPreview} />;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasChatStreamArgsPreviewMetadata(value: unknown): boolean {
  return isRecord(value) && isRecord(value.kollab) &&
    Object.hasOwn(value.kollab, "chat_stream_args_preview");
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
  if (isRecord(data) && data.kind === CHAT_STREAM_PERMISSION_PROMPT_PREVIEW_KIND &&
      isChatStreamPartPreviewMarker(data.marker)) {
    return <PermissionPromptPreview id={id} marker={data.marker} />;
  }
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

function permissionPartContentMarker(marker: ChatStreamPartPreviewMarker): ChatStreamContentPreviewMarker {
  return {
    kind: "chat-stream-content-preview",
    session_id: marker.session_id,
    row_id: marker.row_id,
    part_index: marker.part_index,
    field: "part",
    output_bytes: marker.output_bytes,
    output_chars: marker.output_chars,
    output_type: "json",
    output_revision: marker.output_revision,
    preview: marker.preview,
  };
}

function parsePermissionPromptPart(content: string, promptId: string): {
  data: unknown;
  result?: PermissionResult;
  resolved: boolean;
} | null {
  try {
    const part: unknown = JSON.parse(content);
    if (!isRecord(part) || part.type !== "prompt" || part.prompt_id !== promptId || !Object.hasOwn(part, "data")) {
      return null;
    }
    const hasResolution = isRecord(part.data) && Object.hasOwn(part.data, "resolution");
    return {
      data: part.data,
      result: permissionResultFromData(part.data),
      resolved: hasResolution,
    };
  } catch {
    return null;
  }
}

function parsePermissionArgs(content: string): PermissionArgs | null {
  try {
    const value: unknown = JSON.parse(content);
    if (!isRecord(value) || typeof value.tool_id !== "string" || !value.tool_id) return null;
    const details = isRecord(value.details) ? value.details : undefined;
    const permissionId = typeof value.permission_id === "string" && value.permission_id
      ? value.permission_id
      : typeof details?.permission_id === "string" && details.permission_id
        ? details.permission_id
        : undefined;
    if (!permissionId) return null;
    return { ...permissionArgsFromData(value), permission_id: permissionId };
  } catch {
    return null;
  }
}

/** Full request_permission args must be fetched before the card can act. */
function PermissionArgsPreview({
  id,
  marker,
  result,
  resultPreview,
  rowContext,
}: {
  id: string;
  marker: ChatStreamContentPreviewMarker;
  result?: PermissionResult | null;
  resultPreview?: ChatStreamToolOutputPreviewMarker;
  rowContext?: RecoveredPermissionRowContext;
}) {
  if (!isChatStreamContentPreviewMarker(marker) || marker.field !== "args" ||
      (rowContext && (marker.session_id !== rowContext.sessionId || marker.row_id !== rowContext.rowId ||
        marker.part_index !== rowContext.partIndex))) {
    return <p role="alert" className="text-destructive text-xs">The complete permission request could not be verified. Refresh before deciding.</p>;
  }
  return (
    <ChatStreamContentPreview
      key={chatStreamContentIdentity(marker)}
      marker={marker}
      autoLoad
    >
      {(content, loaded) => {
        const args = loaded ? parsePermissionArgs(content) : null;
        if (!loaded) {
          return <p role="status" className="text-muted-foreground text-xs">Loading complete permission details before showing decision options…</p>;
        }
        if (!args) {
          return <p role="alert" className="text-destructive text-xs">The complete permission request could not be verified. Refresh before deciding.</p>;
        }
        return <LoadedPermissionArgs id={id} marker={marker} args={args} content={content} result={result} resultPreview={resultPreview} rowContext={rowContext} />;
      }}
    </ChatStreamContentPreview>
  );
}

function LoadedPermissionArgs({
  id,
  marker,
  args,
  content,
  result,
  resultPreview,
  rowContext,
}: {
  id: string;
  marker: ChatStreamContentPreviewMarker;
  args: PermissionArgs;
  content: string;
  result?: PermissionResult | null;
  resultPreview?: ChatStreamToolOutputPreviewMarker;
  rowContext?: RecoveredPermissionRowContext;
}) {
  const [detailsOpen, setDetailsOpen] = useState(true);
  const revision = rowContext?.rowRevision ?? rowContext?.partRevision ?? marker.output_revision;
  const recoveredPrompt = {
    sessionId: marker.session_id,
    promptId: id,
    rowId: marker.row_id,
    partIndex: marker.part_index,
    revision,
    data: args,
    partType: "tool-call" as const,
    ...(rowContext?.rowRevision ? { rowRevision: rowContext.rowRevision } : {}),
    ...(rowContext?.partRevision ? { partRevision: rowContext.partRevision } : {}),
    contentRevision: marker.output_revision,
    argsPreview: marker,
  };
  const hasResult = result !== undefined || resultPreview !== undefined;
  const register = (isVisible: boolean) => {
    registerRecoveredPermissionPrompt(recoveredPrompt, !hasResult && isVisible && !!args.tool_id && !!args.permission_id);
  };
  useEffect(() => {
    register(detailsOpen);
    return () => register(false);
  }, [detailsOpen, id, marker.output_revision, marker.part_index, marker.row_id, marker.session_id,
      result, resultPreview, rowContext?.rowRevision, rowContext?.partRevision, rowContext?.sessionId, rowContext?.rowId]);
  return (
    <div className="space-y-2">
      <details open={detailsOpen} onToggle={(event) => {
        const open = event.currentTarget.open;
        setDetailsOpen(open);
        register(open);
      }}>
        <summary className="cursor-pointer text-xs font-medium">Full permission request details</summary>
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/50 p-2 text-xs">{content}</pre>
      </details>
      <div hidden={!detailsOpen} aria-hidden={!detailsOpen}>
        <PermissionPromptCard partId={id} args={args} result={result} resultPreview={resultPreview} />
      </div>
    </div>
  );
}

/** Renders a permission call recovered from a complete part or a paged row. */
export function RecoveredPermissionToolCall({
  id,
  args: rawArgs,
  argsPreview,
  result,
  rowContext,
}: {
  id: string;
  args: unknown;
  argsPreview?: unknown;
  result?: unknown;
  rowContext?: RecoveredPermissionRowContext;
}) {
  const args = permissionArgsFromData(rawArgs);
  const parsedResult = permissionResultFromData(result) ?? (isRecord(result) &&
    (result.decision === "approve" || result.decision === "deny") ? result as PermissionResult : undefined);
  if (!rowContext || rowContext.partIndex < 0) {
    return <p role="alert" className="text-destructive text-xs">The permission request could not be tied to its current row. Refresh before deciding.</p>;
  }
  if (argsPreview !== undefined) {
    if (!isChatStreamContentPreviewMarker(argsPreview) || argsPreview.field !== "args" ||
        argsPreview.session_id !== rowContext.sessionId || argsPreview.row_id !== rowContext.rowId ||
        argsPreview.part_index !== rowContext.partIndex) {
      return <p role="alert" className="text-destructive text-xs">The complete permission request could not be verified. Refresh before deciding.</p>;
    }
    return <PermissionArgsPreview id={id} marker={argsPreview} result={parsedResult} rowContext={rowContext} />;
  }
  if (!args.tool_id || !args.permission_id) {
    return <p role="alert" className="text-destructive text-xs">The complete permission request could not be verified. Refresh before deciding.</p>;
  }
  return <LoadedRecoveredPermissionToolCall id={id} args={args} result={parsedResult} rowContext={rowContext} />;
}

function LoadedRecoveredPermissionToolCall({
  id,
  args,
  result,
  rowContext,
}: {
  id: string;
  args: PermissionArgs;
  result?: PermissionResult;
  rowContext: RecoveredPermissionRowContext;
}) {
  const [detailsOpen, setDetailsOpen] = useState(true);
  const revision = rowContext.rowRevision ?? rowContext.partRevision;
  const recoveredPrompt = {
    sessionId: rowContext.sessionId,
    promptId: id,
    rowId: rowContext.rowId,
    partIndex: rowContext.partIndex,
    revision: revision ?? "",
    data: args,
    partType: "tool-call" as const,
    ...(rowContext.rowRevision ? { rowRevision: rowContext.rowRevision } : {}),
    ...(rowContext.partRevision ? { partRevision: rowContext.partRevision } : {}),
  };
  const register = (isVisible: boolean) => {
    registerRecoveredPermissionPrompt(recoveredPrompt, !result && !!revision && isVisible);
  };
  useEffect(() => {
    register(detailsOpen);
    return () => register(false);
  }, [detailsOpen, id, rowContext.sessionId, rowContext.rowId, rowContext.partIndex,
      rowContext.rowRevision, rowContext.partRevision, result]);
  return (
    <div className="space-y-2">
      <details open={detailsOpen} onToggle={(event) => {
        const open = event.currentTarget.open;
        setDetailsOpen(open);
        register(open);
      }}>
        <summary className="cursor-pointer text-xs font-medium">Full permission request details</summary>
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/50 p-2 text-xs">{JSON.stringify(args, null, 2)}</pre>
      </details>
      <div hidden={!detailsOpen} aria-hidden={!detailsOpen}>
        <PermissionPromptCard partId={id} args={args} result={result} />
      </div>
    </div>
  );
}

/** A clipped permission prompt has no decision actions until its exact source is loaded. */
export function PermissionPromptPreview({
  id,
  marker,
}: {
  id: string;
  marker: ChatStreamPartPreviewMarker;
}) {
  const contentMarker = permissionPartContentMarker(marker);

  return (
    <ChatStreamContentPreview
      key={chatStreamContentIdentity(contentMarker)}
      marker={contentMarker}
      autoLoad
    >
      {(content, loaded) => {
        const parsed = loaded ? parsePermissionPromptPart(content, id) : null;
        if (!loaded) {
          return <p role="status" className="text-muted-foreground text-xs">Loading complete permission details before showing decision options…</p>;
        }
        if (!parsed) {
          return <p role="alert" className="text-destructive text-xs">The complete permission request could not be verified. Refresh before deciding.</p>;
        }
        return <LoadedPermissionPrompt id={id} marker={marker} content={content} parsed={parsed} />;
      }}
    </ChatStreamContentPreview>
  );
}

function LoadedPermissionPrompt({
  id,
  marker,
  content,
  parsed,
}: {
  id: string;
  marker: ChatStreamPartPreviewMarker;
  content: string;
  parsed: { data: unknown; result?: PermissionResult; resolved: boolean };
}) {
  const [detailsOpen, setDetailsOpen] = useState(true);
  useEffect(() => {
    const key = {
      sessionId: marker.session_id,
      promptId: id,
      revision: marker.output_revision,
    };
    markPermissionPromptPreviewVisible(key, detailsOpen);
    return () => markPermissionPromptPreviewVisible(key, false);
  }, [detailsOpen, id, marker.output_revision, marker.session_id]);
  const payload = permissionArgsFromData(parsed.data);
  return (
    <div className="space-y-2">
      <details open={detailsOpen} onToggle={(event) => {
        const open = event.currentTarget.open;
        setDetailsOpen(open);
        markPermissionPromptPreviewVisible({
          sessionId: marker.session_id,
          promptId: id,
          revision: marker.output_revision,
        }, open);
      }}>
        <summary className="cursor-pointer text-xs font-medium">Full permission request details</summary>
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/50 p-2 text-xs">{content}</pre>
      </details>
      {detailsOpen ? (
        <PermissionPromptCard partId={id} args={payload} result={parsed.result} resolved={parsed.resolved} />
      ) : null}
    </div>
  );
}

/** The exact prompt part was already fetched by the part-recovery viewer. */
export function RecoveredPermissionPrompt({
  id,
  data,
  serializedPart,
  rowContext,
}: {
  id: string;
  data: unknown;
  serializedPart: string;
  rowContext?: {
    sessionId: string;
    rowId: string;
    partIndex: number;
    rowRevision?: string;
    partRevision?: string;
  };
}) {
  const [detailsOpen, setDetailsOpen] = useState(true);
  const payload = permissionArgsFromData(data);
  const result = permissionResultFromData(data);
  const resolved = isRecord(data) && Object.hasOwn(data, "resolution");
  const register = (isVisible: boolean) => {
    if (!rowContext) return;
    if (isVisible && (!isRecord(data) || data.permission_id !== id || resolved)) return;
    const revision = rowContext.rowRevision ?? rowContext.partRevision;
    if (!revision) return;
    registerRecoveredPermissionPrompt({
      sessionId: rowContext.sessionId,
      rowId: rowContext.rowId,
      revision,
      partIndex: rowContext.partIndex,
      promptId: id,
      data,
      ...(rowContext.rowRevision ? { rowRevision: rowContext.rowRevision } : {}),
      ...(rowContext.partRevision ? { partRevision: rowContext.partRevision } : {}),
    }, isVisible);
  };
  useEffect(() => {
    register(detailsOpen);
    return () => register(false);
  }, [detailsOpen, id, rowContext?.partIndex, rowContext?.rowId, rowContext?.rowRevision,
      rowContext?.partRevision, rowContext?.sessionId, data]);
  return (
    <div className="space-y-2">
      <details open={detailsOpen} onToggle={(event) => {
        const open = event.currentTarget.open;
        setDetailsOpen(open);
        register(open);
      }}>
        <summary className="cursor-pointer text-xs font-medium">Full permission request details</summary>
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/50 p-2 text-xs">{serializedPart}</pre>
      </details>
      {detailsOpen ? <PermissionPromptCard partId={id} args={payload} result={result} resolved={resolved} /> : null}
    </div>
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
