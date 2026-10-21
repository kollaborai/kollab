import { useId } from "react";
import type { PanelAction, PanelField, PanelPicker, PanelRow } from "@/api";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { FieldRenderer, fieldValue, nextDraft, type Draft } from "./FieldRenderer";
import { PanelSummary } from "./PanelSummary";
import { PickerList } from "./PickerList";
import { networkSummaryRows, optionLabel, titleCase } from "./panel-model";

export type NetworkSection = "overview" | "access" | "knocks";

/** A native single-choice group, using the app's compact button treatment. */
function TrustControl({
  field,
  busy,
  error,
  onChange,
}: {
  field: PanelField;
  busy: boolean;
  error?: string;
  onChange: (value: string) => void;
}) {
  const id = useId();
  const current = field.value === null || field.value === undefined ? "" : String(field.value);
  const options = field.options ?? [];
  const shown = current && !options.includes(current) ? [current, ...options] : options;
  return (
    <fieldset
      disabled={busy || !field.editable}
      aria-describedby={field.help ? `${id}-help` : undefined}
      className="min-w-0"
      data-field={field.path}
    >
      <legend className="mb-2 text-sm font-medium">{field.label}</legend>
      <div className="flex w-fit max-w-full flex-wrap gap-1 rounded-lg bg-muted/30 p-1">
        {shown.map((option, index) => (
          <div key={option} className="relative">
            <input
              id={`${id}-${index}`}
              className="peer sr-only"
              type="radio"
              name={id}
              value={option}
              checked={current === option}
              onChange={() => onChange(option)}
            />
            <label
              htmlFor={`${id}-${index}`}
              className={cn(
                buttonVariants({ variant: "ghost", size: "sm" }),
                "cursor-pointer font-normal peer-focus-visible:ring-[3px] peer-focus-visible:ring-ring/50 peer-disabled:pointer-events-none peer-disabled:opacity-50",
                current === option ? "bg-accent text-accent-foreground" : "text-muted-foreground",
              )}
            >
              {field.option_labels?.[option] ?? titleCase(optionLabel(field, option))}
            </label>
          </div>
        ))}
      </div>
      {field.help ? <p id={`${id}-help`} className="mt-2 text-xs text-muted-foreground break-words">{field.help}</p> : null}
      {error ? <p role="alert" className="mt-1 text-xs text-destructive break-words">{error}</p> : null}
    </fieldset>
  );
}

function DeviceNameControl({
  field,
  draft,
  error,
  busy,
  onDraftChange,
  onSave,
}: {
  field: PanelField;
  draft: Draft;
  error?: string;
  busy: boolean;
  onDraftChange: (draft: Draft) => void;
  onSave: (value: unknown) => void;
}) {
  const id = useId();
  const value = fieldValue(field, draft);
  const dirty = field.path in draft;
  if (!field.editable) {
    return <FieldRenderer field={field} draft={draft} disabled error={error} onChange={onDraftChange} />;
  }
  return (
    <div className="min-w-0" data-field={field.path}>
      <label htmlFor={id} className="mb-2 block text-sm font-medium">{field.label}</label>
      <div className="flex min-w-0 items-center gap-2">
        <Input
          id={id}
          type="text"
          className="w-full min-w-0 max-w-xs"
          value={value === null || value === undefined ? "" : String(value)}
          disabled={busy}
          placeholder={field.placeholder ?? undefined}
          aria-describedby={field.help ? `${id}-help` : undefined}
          aria-invalid={Boolean(error)}
          maxLength={4096}
          onChange={(event) => onDraftChange(nextDraft(draft, field, event.target.value))}
          onKeyDown={(event) => {
            if (event.key === "Enter" && dirty && !busy) {
              event.preventDefault();
              onSave(value);
            }
          }}
        />
        <Button type="button" variant="outline" size="sm" disabled={busy || !dirty} onClick={() => onSave(value)}>
          Save
        </Button>
      </div>
      {field.help ? <p id={`${id}-help`} className="mt-2 text-xs text-muted-foreground break-words">{field.help}</p> : null}
      {error ? <p role="alert" className="mt-1 text-xs text-destructive break-words">{error}</p> : null}
    </div>
  );
}

/** Network presentation only. PanelHost still owns every panel, draft and action. */
export function NetworkPanel({
  panel,
  rootPanel,
  section,
  draft,
  errors,
  busy,
  onSectionChange,
  onDraftChange,
  onRowAction,
  onToolbarAction,
  onControlChange,
}: {
  panel: PanelPicker;
  rootPanel: PanelPicker;
  section: NetworkSection;
  draft: Draft;
  errors: Record<string, string>;
  busy: boolean;
  onSectionChange: (section: NetworkSection) => void;
  onDraftChange: (draft: Draft) => void;
  onRowAction: (action: PanelAction, row: PanelRow) => void;
  onToolbarAction: (action: PanelAction) => void;
  onControlChange: (field: PanelField, value: unknown, resetDraft?: boolean) => void;
}) {
  const controls = rootPanel.controls ?? [];
  const toolbar = rootPanel.toolbar_actions ?? [];
  const trust = controls.find((field) => field.path === "level" && field.type === "dropdown");
  const deviceName = controls.find((field) => field.path === "name" && field.type === "text_input");
  const canAccess = Boolean(controls.length || rootPanel.rows.length || toolbar.some((action) => action.id === "new_code"));
  const canKnock = panel.panel === "connect-knocks" || toolbar.some((action) => action.id === "knocks");
  const activeSection = section === "access" && !canAccess ? "overview" : section;
  const accessPanel: PanelPicker = {
    ...rootPanel,
    summary: undefined,
    notice: undefined,
    controls: controls.filter((field) => field !== trust && field !== deviceName),
    toolbar_actions: toolbar.filter((action) => action.id === "new_code"),
  };
  return (
    <Tabs value={activeSection} onValueChange={(next) => onSectionChange(next as NetworkSection)} className="min-w-0 gap-4">
      <TabsList aria-label="Network sections" className="w-fit max-w-full justify-start">
        <TabsTrigger value="overview" disabled={busy}>Overview</TabsTrigger>
        <TabsTrigger value="access" disabled={busy || !canAccess}>Access</TabsTrigger>
        <TabsTrigger value="knocks" disabled={busy || !canKnock}>Knocks</TabsTrigger>
      </TabsList>
      <TabsContent value="overview" className="min-w-0 space-y-4">
        {rootPanel.notice ? <p className="rounded-md bg-muted/40 px-3 py-2 text-sm break-words">{rootPanel.notice}</p> : null}
        <PanelSummary summary={networkSummaryRows(rootPanel.summary)} />
        {toolbar.some((action) => action.id !== "new_code" && action.id !== "knocks") ? (
          <div className="flex flex-wrap gap-2">
            {toolbar.filter((action) => action.id !== "new_code" && action.id !== "knocks").map((action) => (
              <Button key={action.id} type="button" variant="outline" size="sm" disabled={busy} onClick={() => onToolbarAction(action)}>
                {action.label || titleCase(action.id)}
              </Button>
            ))}
          </div>
        ) : null}
      </TabsContent>
      <TabsContent value="access" className="min-w-0 space-y-5">
        {rootPanel.notice ? <p className="rounded-md bg-muted/40 px-3 py-2 text-sm break-words">{rootPanel.notice}</p> : null}
        {trust ? <TrustControl field={trust} busy={busy} error={errors[trust.path]} onChange={(value) => onControlChange(trust, value)} /> : null}
        {deviceName ? <DeviceNameControl field={deviceName} draft={draft} busy={busy} error={errors[deviceName.path]} onDraftChange={onDraftChange} onSave={(value) => onControlChange(deviceName, value, true)} /> : null}
        <PickerList
          panel={accessPanel}
          draft={draft}
          onDraftChange={onDraftChange}
          busy={busy}
          errors={errors}
          compactControls
          emptyText="No pending join requests."
          onRowAction={onRowAction}
          onToolbarAction={onToolbarAction}
          onControlChange={onControlChange}
        />
      </TabsContent>
      <TabsContent value="knocks" className="min-w-0">
        {panel.panel === "connect-knocks" ? (
          <PickerList panel={panel} draft={draft} onDraftChange={onDraftChange} busy={busy} errors={errors} compactControls onRowAction={onRowAction} onToolbarAction={onToolbarAction} onControlChange={onControlChange} />
        ) : null}
      </TabsContent>
    </Tabs>
  );
}
