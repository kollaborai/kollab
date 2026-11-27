import { QueuePanel } from "./QueuePanel";

/**
 * The queue panel as the chat mounts it, in the composer footer. The panel reads the queued inputs from the chat
 * store (#1451), so the mount has no state of its own: the batch queue's polling and identity lifecycle is gone.
 */
export function WebQueuePanelMount({ canSendNow = true }: { canSendNow?: boolean }) {
  return <QueuePanel canSendNow={canSendNow} />;
}
