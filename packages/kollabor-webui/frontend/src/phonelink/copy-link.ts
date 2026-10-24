/** Writes `text` to the clipboard; false when the browser refuses or has none. */
export async function copyText(
  clipboard: { writeText(text: string): Promise<void> } | undefined,
  text: string,
): Promise<boolean> {
  if (!clipboard) return false;
  try {
    await clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
