/** Translate shared CLI panel errors only at the browser rendering boundary. */
export function webUiPanelCopy(message: string): string {
  if (message.includes("kollab --upgrade")) {
    return "This engine version is too old for this relay. Update the engine, then retry the join here.";
  }
  if (message.includes("kollab --hub stop all")) {
    return "A second device on this network must confirm the resume. Add one before trying again here.";
  }
  if (message.includes("kollab --hub halt")) {
    return "Confirm this action in the page before continuing.";
  }
  return message;
}
