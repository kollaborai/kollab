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
  const steps = panel.steps ?? [];
  const index = Math.min(step, Math.max(0, steps.length - 1));
  const current = steps[index];
  const last = index >= steps.length - 1;
  const actions = panel.actions ?? [];
  const submit = actions.find((action) => action.id === "finish") ?? actions[actions.length - 1];

  if (!current) {
    return (
      <p className="text-muted-foreground px-4 py-6 text-sm sm:px-6">
        This wizard has no steps.
      </p>
    );
  }

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
        <div className="divide-y">
          {current.fields.map((field) => (
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
      <footer className="flex shrink-0 flex-col-reverse gap-2 border-t px-4 py-3 sm:flex-row sm:justify-end sm:px-6">
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
          actions.map((action) => (
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
          <Button type="submit" disabled={busy}>
            Next
          </Button>
        )}
      </footer>
    </form>
  );
}
