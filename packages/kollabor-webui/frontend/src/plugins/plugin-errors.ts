export const SAFE_PLUGIN_ACTION_ERROR = "The plugin action failed. Check the browser console for redacted details.";
export const SAFE_PLUGIN_SETTINGS_ERROR = "Could not complete this plugin settings action. Check the browser console for redacted details.";
export const SAFE_PLUGIN_RENDER_ERROR = "This plugin's view failed to render. Check the browser console for details. Sensitive values are redacted.";

const URL_CREDENTIALS = /\b(https?:\/\/)[^/@\s]+@/gi;
const AUTH_SCHEME = /\b(?:bearer|basic)\s+[^\s,;]+/gi;
const SENSITIVE_ASSIGNMENT = /((?:^|[^A-Za-z0-9])(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|credential|authorization|bearer)(?![A-Za-z0-9])\s*[:=]\s*)(?:(?:bearer|basic)\s+)?(?:"[^"]*"|'[^']*'|[^\s,;]+)/gi;
const SECRET_VALUE = /\b(?:secret[-_]token[-_][A-Za-z0-9_-]+|sk-|gh[pousr]_|github_pat_)[A-Za-z0-9_-]*/gi;

function errorDetail(error: unknown): string {
  try {
    if (error instanceof Error) return `${error.name}: ${error.message}${error.stack ? `\n${error.stack}` : ""}`;
    return String(error);
  } catch {
    return "Unprintable plugin error";
  }
}

/** Keep credential-shaped values out of the browser log while retaining useful failure context. */
export function redactPluginError(error: unknown): string {
  return errorDetail(error)
    .replace(URL_CREDENTIALS, "$1[redacted]@")
    .replace(SENSITIVE_ASSIGNMENT, "$1[redacted]")
    .replace(AUTH_SCHEME, "[redacted]")
    .replace(SECRET_VALUE, "[redacted]");
}

/** Log plugin failure details only after redacting them; plugin output never goes straight to the console. */
export function logPluginFailure(
  plugin: string,
  action: string,
  error: unknown,
  componentStack?: string | null,
): void {
  const message = `${plugin} ${action}`;
  if (componentStack) console.error(message, redactPluginError(error), redactPluginError(componentStack));
  else console.error(message, redactPluginError(error));
}
