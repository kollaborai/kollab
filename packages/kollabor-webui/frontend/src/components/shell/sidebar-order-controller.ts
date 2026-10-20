export type SidebarOrderSnapshot = {
  order: string[];
  revision: number;
  warning?: string | null;
};

export type SidebarOrderView = {
  order: string[];
  revision: number;
  warning: string | null;
  error: string | null;
};

export type SidebarOrderClient = {
  saveSidebarOrder(order: string[], revision: number): Promise<SidebarOrderSnapshot>;
  getSidebarOrder(): Promise<SidebarOrderSnapshot>;
  isConflict(reason: unknown): boolean;
  isRecoveryRequired(reason: unknown): boolean;
  errorText(reason: unknown): string;
};

export type SidebarOrderController = {
  snapshot(): SidebarOrderView;
  stamp(): number;
  sync(from: number, snapshot: SidebarOrderSnapshot | undefined): void;
  save(next: string[]): Promise<void>;
};

type SaveOutcome =
  | { kind: "saved"; snapshot: SidebarOrderSnapshot }
  | { kind: "failed"; snapshot: SidebarOrderSnapshot; error: string }
  | { kind: "cancelled" };

const copySnapshot = (snapshot: SidebarOrderSnapshot): SidebarOrderSnapshot => ({
  order: [...snapshot.order],
  revision: snapshot.revision,
  warning: snapshot.warning ?? null,
});

export function initialSidebarOrder(): SidebarOrderView {
  return { order: [], revision: 0, warning: null, error: null };
}

/**
 * Coordinates optimistic sidebar edits with the engine's revisioned order.
 * This pure controller keeps failure and queue behavior independently testable.
 */
export function createSidebarOrderController(
  client: SidebarOrderClient,
  onChange: (snapshot: SidebarOrderView) => void,
): SidebarOrderController {
  let confirmed: SidebarOrderSnapshot = { order: [], revision: 0, warning: null };
  let view = initialSidebarOrder();
  let edits = 0;
  let pendingWrites = 0;
  let latestSave = 0;
  let queueEpoch = 0;
  let queue = Promise.resolve();

  const publish = () => onChange({ ...view, order: [...view.order] });

  return {
    snapshot: () => ({ ...view, order: [...view.order] }),
    stamp: () => edits,
    sync(from, incoming) {
      if (!incoming || pendingWrites > 0 || from !== edits) return;
      const next = copySnapshot(incoming);
      if (next.revision < confirmed.revision) return;
      confirmed = next;
      view = {
        ...view,
        order: [...next.order],
        revision: next.revision,
        warning: next.warning ?? null,
      };
      publish();
    },
    save(nextOrder) {
      const saveId = ++latestSave;
      const epoch = queueEpoch;
      const order = [...nextOrder];
      edits += 1;
      pendingWrites += 1;
      view = { ...view, order, error: null };
      publish();

      const operation = queue.then(async (): Promise<SaveOutcome> => {
        if (epoch !== queueEpoch) return { kind: "cancelled" };
        try {
          const saved = copySnapshot(await client.saveSidebarOrder(order, confirmed.revision));
          confirmed = saved;
          return { kind: "saved", snapshot: saved };
        } catch (reason) {
          const conflict = client.isConflict(reason);
          if (conflict || client.isRecoveryRequired(reason)) {
            // Later queued full-list writes were composed against stale state or
            // an unreadable store. Cancel them and refresh before accepting edits.
            queueEpoch += 1;
            try {
              confirmed = copySnapshot(await client.getSidebarOrder());
              return {
                kind: "failed",
                snapshot: confirmed,
                error: conflict
                  ? "Another client changed the sidebar order. The current order was refreshed; retry your move."
                  : client.errorText(reason),
              };
            } catch {
              return {
                kind: "failed",
                snapshot: confirmed,
                error: conflict
                  ? "Another client changed the sidebar order, but refresh failed. Reload before retrying."
                  : `${client.errorText(reason)} The current order could not be refreshed; reload before retrying.`,
              };
            }
          }
          return { kind: "failed", snapshot: confirmed, error: client.errorText(reason) };
        }
      });
      // Keep the queue usable after failures; each operation handles its own result.
      queue = operation.then(() => undefined, () => undefined);

      return operation
        .then((result) => {
          if (result.kind === "cancelled") return;
          if (result.kind === "saved") {
            view = {
              ...view,
              revision: result.snapshot.revision,
              warning: result.snapshot.warning ?? null,
              ...(saveId === latestSave ? { order: [...result.snapshot.order], error: null } : {}),
            };
          } else {
            // Roll back immediately to the last confirmed server state, even if
            // a newer local write is queued. That write can later succeed or fail.
            view = {
              ...view,
              order: [...confirmed.order],
              revision: confirmed.revision,
              warning: confirmed.warning ?? null,
              error: result.error,
            };
          }
          publish();
        })
        .finally(() => {
          pendingWrites -= 1;
        });
    },
  };
}
