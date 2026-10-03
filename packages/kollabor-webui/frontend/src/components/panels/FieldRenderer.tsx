import { useEffect, useId, useState, type ReactNode } from "react";
import type { PanelField } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";

/** Unsaved edits by field path. `null` on a secret field means "clear it". */
export type Draft = Record<string, unknown>;

const EMPTY_OPTION = "__empty__";

export function fieldValue(field: PanelField, draft: Draft): unknown {
  return field.path in draft ? draft[field.path] : field.value;
}

/** The draft after `field` takes `value`; a value equal to the daemon's drops out. */
export function nextDraft(
  draft: Draft,
  field: PanelField,
  value: unknown,
): Draft {
  const unchanged = field.secret
    ? value === ""
    : (value ?? "") === (field.value ?? "");
  const rest = { ...draft };
  delete rest[field.path];
  return unchanged ? rest : { ...rest, [field.path]: value };
}

/** A cleared secret travels as "" (the daemon validates strings), never null. */
export function serializeDraft(draft: Draft): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(draft).map(([path, value]) => [
      path,
      value === null ? "" : value,
    ]),
  );
}

function FieldShell({
  id,
  field,
  dirty,
  error,
  aside,
  children,
}: {
  id: string;
  field: PanelField;
  dirty: boolean;
  error?: string | null;
  aside?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div
      className="flex min-w-0 flex-col gap-1.5 py-2.5"
      data-field={field.path}
    >
      <div className="flex min-w-0 items-center justify-between gap-3">
        <label
          htmlFor={id}
          title={field.path}
          className="min-w-0 text-sm font-medium break-words"
        >
          {field.label}
          {dirty ? (
            <span
              role="img"
              aria-label="Unsaved change"
              title="Unsaved change"
              className="ml-2 inline-block size-1.5 rounded-full bg-amber-500 align-middle"
            />
          ) : null}
        </label>
        {aside}
      </div>
      {children}
      {field.help ? (
        <p className="text-muted-foreground text-xs break-words">
          {field.help}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="text-destructive text-xs break-words">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function readOnlyText(field: PanelField): string {
  if (field.secret) return field.is_set ? "Set" : "Not set";
  const { value } = field;
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "On" : "Off";
  return String(value);
}

function decimalsOf(step: number | null | undefined): number {
  return (String(step ?? 1).split(".")[1] || "").length;
}

/** Text-backed number box: keeps "1." while typing, reports only valid numbers. */
function NumberInput({
  id,
  value,
  min,
  max,
  step,
  disabled,
  onCommit,
}: {
  id: string;
  value: unknown;
  min?: number | null;
  max?: number | null;
  step?: number | null;
  disabled?: boolean;
  onCommit: (value: number) => void;
}) {
  const asText = (v: unknown) => (v === null || v === undefined ? "" : String(v));
  const [text, setText] = useState(() => asText(value));
  useEffect(() => {
    // Take outside changes (revert, save, fresh panel); keep text that still
    // parses to the same number.
    setText((current) =>
      current !== "" && Number(current) === Number(value)
        ? current
        : asText(value),
    );
  }, [value]);
  return (
    <Input
      id={id}
      type="number"
      inputMode="decimal"
      min={min ?? undefined}
      max={max ?? undefined}
      step={step ?? undefined}
      value={text}
      disabled={disabled}
      onChange={(event) => {
        setText(event.target.value);
        const parsed =
          event.target.value.trim() === "" ? Number.NaN : Number(event.target.value);
        if (Number.isFinite(parsed)) onCommit(parsed);
      }}
      onBlur={() => setText(asText(value))}
    />
  );
}

/**
 * One setting. `draft` holds the unsaved edits for the whole panel; changes go
 * up as the next draft, so the parent owns the state and it survives re-renders.
 */
export function FieldRenderer({
  field,
  draft,
  error,
  disabled,
  onChange,
}: {
  field: PanelField;
  draft: Draft;
  error?: string | null;
  disabled?: boolean;
  onChange: (draft: Draft) => void;
}) {
  const id = useId();
  const dirty = field.path in draft;
  const value = fieldValue(field, draft);
  const set = (next: unknown) => onChange(nextDraft(draft, field, next));
  const revert = () => {
    const rest = { ...draft };
    delete rest[field.path];
    onChange(rest);
  };

  if (field.type === "label") {
    return (
      <div className="flex min-w-0 flex-col gap-0.5 py-2.5">
        <p className="text-sm font-medium break-words">{field.label}</p>
        {readOnlyText(field) !== "—" ? (
          <p className="text-muted-foreground font-mono text-xs break-words">
            {readOnlyText(field)}
          </p>
        ) : null}
        {field.help ? (
          <p className="text-muted-foreground text-xs break-words">
            {field.help}
          </p>
        ) : null}
      </div>
    );
  }

  if (!field.editable) {
    return (
      <FieldShell
        id={id}
        field={field}
        dirty={false}
        aside={
          field.managed_by ? (
            <Badge variant="outline">Managed by {field.managed_by}</Badge>
          ) : null
        }
      >
        <p id={id} className="text-muted-foreground text-sm break-words">
          {readOnlyText(field)}
        </p>
      </FieldShell>
    );
  }

  if (field.type === "checkbox") {
    return (
      <FieldShell
        id={id}
        field={field}
        dirty={dirty}
        error={error}
        aside={
          <Switch
            id={id}
            checked={Boolean(value)}
            disabled={disabled}
            onCheckedChange={set}
          />
        }
      />
    );
  }

  if (field.type === "slider") {
    const min = field.min_value ?? 0;
    const max = field.max_value ?? 100;
    const step = field.step ?? 1;
    const current = typeof value === "number" ? value : Number(value ?? min);
    const decimals = decimalsOf(step);
    return (
      <FieldShell id={id} field={field} dirty={dirty} error={error}>
        <div className="flex min-w-0 items-center gap-3">
          <Slider
            id={id}
            aria-label={field.label}
            className="min-w-0 flex-1"
            min={min}
            max={max}
            step={step}
            value={[Number.isFinite(current) ? current : min]}
            disabled={disabled}
            onValueChange={([next]) => set(Number(next.toFixed(decimals)))}
          />
          <span className="w-14 shrink-0 text-right text-sm tabular-nums">
            {Number.isFinite(current) ? current.toFixed(decimals) : "—"}
          </span>
        </div>
      </FieldShell>
    );
  }

  if (field.type === "spinbox") {
    return (
      <FieldShell id={id} field={field} dirty={dirty} error={error}>
        <NumberInput
          id={id}
          value={value}
          min={field.min_value}
          max={field.max_value}
          step={field.step}
          disabled={disabled}
          onCommit={set}
        />
      </FieldShell>
    );
  }

  if (field.type === "dropdown" && field.options?.length) {
    const current = value === null || value === undefined ? "" : String(value);
    const options = field.options.includes(current) || current === ""
      ? field.options
      : [current, ...field.options];
    const toItem = (option: string) => (option === "" ? EMPTY_OPTION : option);
    return (
      <FieldShell id={id} field={field} dirty={dirty} error={error}>
        <Select
          value={current === "" && !options.includes("") ? "" : toItem(current)}
          disabled={disabled}
          onValueChange={(next) => set(next === EMPTY_OPTION ? "" : next)}
        >
          <SelectTrigger id={id} className="w-full min-w-0">
            <SelectValue placeholder="Choose…" />
          </SelectTrigger>
          <SelectContent>
            {options.map((option) => (
              <SelectItem key={option} value={toItem(option)}>
                {option === "" ? "(default)" : option}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </FieldShell>
    );
  }

  if (field.secret) {
    const cleared = dirty && value === null;
    const typed = typeof value === "string" ? value : "";
    return (
      <FieldShell
        id={id}
        field={field}
        dirty={dirty}
        error={error}
        aside={
          <Badge variant={field.is_set ? "secondary" : "outline"}>
            {field.is_set ? "Set" : "Not set"}
          </Badge>
        }
      >
        <div className="flex min-w-0 gap-2">
          <Input
            id={id}
            type="password"
            autoComplete="new-password"
            value={typed}
            disabled={disabled || cleared}
            placeholder={
              cleared
                ? "Will be cleared on save"
                : field.is_set
                  ? "Set. Type to replace"
                  : "Not set"
            }
            maxLength={4096}
            onChange={(event) => set(event.target.value)}
          />
          {cleared ? (
            <Button type="button" variant="outline" size="sm" onClick={revert}>
              Undo
            </Button>
          ) : field.is_set ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={disabled}
              onClick={() => set(null)}
            >
              Clear
            </Button>
          ) : null}
        </div>
      </FieldShell>
    );
  }

  return (
    <FieldShell id={id} field={field} dirty={dirty} error={error}>
      <Input
        id={id}
        type="text"
        value={value === null || value === undefined ? "" : String(value)}
        placeholder={field.placeholder ?? undefined}
        disabled={disabled}
        maxLength={4096}
        onChange={(event) => set(event.target.value)}
      />
    </FieldShell>
  );
}
