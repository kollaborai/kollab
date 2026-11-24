import type { PanelAction, PanelWizard, PanelWizardStep } from "@/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { FieldRenderer, type Draft } from "./FieldRenderer";
import { titleCase } from "./panel-model";

/** Every editable value of a wizard, defaults included; untouched secrets stay out. */
export function wizardValues(
  panel: PanelWizard,
  draft: Draft,
): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const step of panel.steps) {
    for (const field of step.fields) {
      if (field.type === "label" || !field.editable) continue;
      const touched = field.path in draft;
      if (field.secret) {
        if (touched) values[field.path] = draft[field.path] ?? "";
      } else {
        values[field.path] = touched ? draft[field.path] : field.value;
      }
    }
  }
  return values;
}

/** Step header, the step's fields, then Back / Next or the panel's actions. */
export function WizardSteps({
  panel,
  draft,
  step,
  errors,
  busy,
  onDraftChange,
  onStepChange,
  onAction,
}: {
  panel: PanelWizard;
  draft: Draft;
  step: number;
  errors: Record<string, string>;
  busy: boolean;
  onDraftChange: (draft: Draft) => void;
  onStepChange: (step: number) => void;
  onAction: (action: PanelAction, step: PanelWizardStep) => void;
}) {
  const providerField = panel.steps.flatMap((item) => item.fields).find((field) => field.path === "provider");
  const selectedProvider = String(draft.provider ?? providerField?.value ?? "");
  const chatgpt = selectedProvider.toLowerCase().includes("sign in with chatgpt");
  const steps = (panel.steps ?? [])
    .filter((item) => {
      if (["api_key", "endpoint"].includes(item.id)) return !chatgpt;
      if (["oauth_sign_in", "oauth_review"].includes(item.id)) return chatgpt;
      return true;
    })
    .map((item) => item.id === "model"
      ? {
          ...item,
          fields: item.fields.filter((field) =>
            chatgpt ? field.path === "oauth_model" : field.path !== "oauth_model",
          ),
        }
      : item);
  const index = Math.min(step, Math.max(0, steps.length - 1));
  const current = steps[index];
  if (!current) {
    return (
      <p className="text-muted-foreground px-4 py-6 text-sm sm:px-6">
        This wizard has no steps.
      </p>
    );
  }

  const last = index >= steps.length - 1;
  const canAdvance = current.id !== "oauth_sign_in" || panel.context?.oauth_status === "signed_in";
  const actions = (current.actions ?? (last ? panel.actions : [])) ?? [];
  const visibleActions = chatgpt && last
    ? actions.filter((action) => action.id === "finish")
    : actions.filter((action) => !action.id.startsWith("oauth_"));
  const submit = visibleActions.find((action) => action.id === "finish") ?? visibleActions[visibleActions.length - 1];

  // Enter in a text box moves on, like Next / the final action would.
  const advance = () => {
    if (busy) return;
    if (!last) onStepChange(index + 1);
    else if (submit) onAction(submit, current);
  };

  return (
    <form
      className="flex min-h-0 flex-1 flex-col"
      onSubmit={(event) => {
        event.preventDefault();
        advance();
      }}
    >
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 sm:px-6">
        <div className="flex flex-col gap-2 pb-2">
          <div className="flex gap-1" aria-hidden="true">
            {steps.map((item, position) => (
              <span
                key={item.id}
                className={cn(
                  "h-1 flex-1 rounded-full",
                  position <= index ? "bg-primary" : "bg-muted",
                )}
              />
            ))}
          </div>
          <h3 className="text-sm font-semibold">
            Step {index + 1} of {steps.length}: {current.title}
          </h3>
        </div>
        <div>
          {current.id === "oauth_sign_in" ? (
            <div className="flex flex-col gap-4 py-2">
              <p className="text-sm">Open this page and enter the code:</p>
              {panel.context?.user_code ? (
                <div className="flex flex-wrap items-center gap-2">
                  <a
                    className="text-sm underline underline-offset-4"
                    href={String(panel.context.verification_url ?? "https://auth.openai.com/codex/device")}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Open Page
                  </a>
                  <code className="text-sm font-medium">{String(panel.context.user_code)}</code>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => void navigator.clipboard?.writeText(String(panel.context?.user_code ?? ""))}
                  >
                    Copy
                  </Button>
                </div>
              ) : null}
              <p role="status" className="text-muted-foreground text-sm">
                {panel.context?.oauth_status === "signed_in"
                  ? `Signed in as ${String(panel.context.account || "ChatGPT account")}`
                  : panel.context?.oauth_status === "expired"
                    ? "Code expired. Start sign-in again."
                    : panel.context?.oauth_status === "denied"
                      ? "Sign-in denied. Start again if you want to continue."
                      : panel.context?.oauth_status === "network_error"
                        ? String(panel.context.oauth_message || "Network error while signing in.")
                        : panel.context?.oauth_status === "failed"
                          ? String(panel.context.oauth_message || "Sign-in failed.")
                          : "Waiting for sign-in…"}
              </p>
            </div>
          ) : current.id === "oauth_review" ? (
            <p className="text-sm">
              Signed in as {String(panel.context?.account || "ChatGPT account")}. The selected model will be saved to the openai-oauth profile.
            </p>
          ) : current.fields.map((field) => (
            <FieldRenderer
              key={field.path}
              field={field}
              draft={draft}
              error={errors[field.path]}
              disabled={busy}
              onChange={onDraftChange}
            />
          ))}
        </div>
      </div>
      <footer className="bg-muted/30 flex shrink-0 flex-col-reverse gap-2 px-4 py-3 sm:flex-row sm:justify-end sm:px-6">
        {index > 0 ? (
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => onStepChange(index - 1)}
          >
            Back
          </Button>
        ) : null}
        {last ? (
          visibleActions.map((action) => (
            <Button
              key={action.id}
              type={action === submit ? "submit" : "button"}
              variant={action === submit ? "default" : "outline"}
              disabled={busy}
              onClick={
                action === submit ? undefined : () => onAction(action, current)
              }
            >
              {busy ? "Working…" : action.label || titleCase(action.id)}
            </Button>
          ))
        ) : (
          <Button type="submit" disabled={busy || !canAdvance}>
            Next
          </Button>
        )}
        {!last
          ? (current.actions ?? (current.id === "oauth_sign_in"
                ? [{ id: "oauth_cancel", label: "Cancel" }]
                : []))
              .filter((action) =>
                action.id !== "oauth_start" ||
                ["expired", "denied", "network_error", "failed"].includes(String(panel.context?.oauth_status)),
              )
              .map((action) => (
              <Button
                key={action.id}
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => onAction(action, current)}
              >
                {action.label || titleCase(action.id)}
              </Button>
              ))
          : null}
      </footer>
    </form>
  );
}
