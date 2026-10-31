/** Match the daemon's authored-text goal detection without image placeholders. */
export function isGoalCommand(command: unknown): boolean {
  if (!command || typeof command !== "object") return false;
  const item = command as { type?: unknown; message?: { parts?: readonly unknown[] } };
  if (item.type !== "add-message" || !Array.isArray(item.message?.parts)) return false;
  const text = item.message.parts.flatMap((part) => {
    if (!part || typeof part !== "object") return [];
    const value = part as { type?: unknown; text?: unknown };
    return value.type === "text" && typeof value.text === "string" ? [value.text] : [];
  }).join("\n");
  return /^\s*\/(?:goal|goals)(?:\s+|$)/i.test(text);
}

function newGoalReplyId(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return `goal-${Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("")}`;
}

/** Tracks the actual in-flight run, not whichever daemon turn is active. */
export class GoalCancellationTracker {
  private active: string | null = null;

  prepare(commands: readonly unknown[]): string | undefined {
    const goal = commands.find(isGoalCommand);
    if (!goal || typeof goal !== "object") {
      this.active = null;
      return undefined;
    }
    // This identifies a transport attempt, not the durable submission. A
    // retry must not be able to stop the earlier attempt's goal command.
    const id = newGoalReplyId();
    this.active = id;
    return id;
  }

  cancel(commands: readonly unknown[]): { goal: boolean; id?: string } {
    if (this.active) return { goal: true, id: this.active };
    // A goal cancelled before prepare was sent nowhere; never fall back to
    // cancelling an unrelated active model turn.
    if (commands.length && isGoalCommand(commands[0])) return { goal: true };
    return { goal: false };
  }

  isActive(): boolean {
    return this.active !== null;
  }

  finish(): void {
    this.active = null;
  }
}
