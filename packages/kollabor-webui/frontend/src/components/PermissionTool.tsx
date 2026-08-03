import { useState } from "react";
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

type PermissionArgs = {
  tool_id?: string;
  tool_name?: string;
  tool_type?: string;
  risk_level?: string;
  risk_reason?: string;
  input?: Record<string, unknown>;
};

type PermissionResult = {
  decision: "approve" | "deny";
  scope: "once" | "session" | "project" | "always_edits" | "trust_tool";
};

const RISK_BADGE_VARIANT: Record<
  string,
  "destructive" | "default" | "secondary" | "outline"
> = {
  high: "destructive",
  medium: "default",
  low: "secondary",
};

function PermissionPrompt({
  args,
  result,
  addResult,
}: ToolCallMessagePartProps<PermissionArgs, PermissionResult>) {
  const payload = args || {};
  // Remember WHAT was submitted, not merely THAT something was. `result` only
  // arrives once the decision round-trips back through assistant-transport, so
  // a boolean flag would leave the card rendering its `false` branch — showing
  // "Denied" to a user who just clicked Allow, while the daemon happily runs
  // the approved tool. Also guards a double-click racing two addResult calls.
  const [submitted, setSubmitted] = useState<PermissionResult | null>(null);
  const decided = result ?? submitted;
  const answered = Boolean(decided);
  const riskLevel = (payload.risk_level || "unknown").toLowerCase();

  const submit = (
    decision: PermissionResult["decision"],
    scope: PermissionResult["scope"],
  ) => {
    if (answered) return;
    setSubmitted({ decision, scope });
    addResult({ decision, scope });
  };

  return (
    <Card className="w-full max-w-md">
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2 text-base">
          <span>Permission required: {payload.tool_name || "tool"}</span>
          <Badge variant={RISK_BADGE_VARIANT[riskLevel] ?? "outline"}>
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
          <p className="font-medium text-muted-foreground">
            {decided.decision === "approve" ? "Allowed" : "Denied"} (
            {decided.scope})
          </p>
        ) : null}
      </CardContent>
      <CardFooter className="flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          disabled={answered}
          onClick={() => submit("approve", "once")}
        >
          Allow once
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={answered}
          onClick={() => submit("approve", "session")}
        >
          Allow session
        </Button>
        <Button
          type="button"
          size="sm"
          variant="destructive"
          disabled={answered}
          onClick={() => submit("deny", "once")}
        >
          Deny
        </Button>
      </CardFooter>
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
