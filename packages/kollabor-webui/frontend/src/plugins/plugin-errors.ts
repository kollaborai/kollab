export const SAFE_PLUGIN_ACTION_ERROR = "The plugin action failed. Check the browser console for redacted details.";
export const SAFE_PLUGIN_SETTINGS_ERROR = "Could not complete this plugin settings action. Check the browser console for redacted details.";

const URL_CREDENTIALS = /\b(https?:\/\/)[^/@\s]+@/gi;
const AUTH_SCHEME = /\b(?:bearer|basic)\s+[^\s,;]+/gi;
const SENSITIVE_ASSIGNMENT = /((?:^|[^A-Za-z0-9])(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|credential|authorization|bearer)(?![A-Za-z0-9])\s*[:=]\s*)(?:(?:bearer|basic)\s+)?(?:"[^"]*"|'[^']*'|[^\s,;]+)/gi;
const SECRET_VALUE = /\b(?:secret[-_]token[-_][A-Za-z0-9_-]+|sk-|gh[pousr]_|github_pat_)[A-Za-z0-9_-]*/gi;

const UNPRINTABLE_PLUGIN_ERROR = "Unprintable plugin error";

/** Describe thrown values without invoking their coercion or trusting property access. */
export function describePluginError(error: unknown, includeStack = false): string {
  try {
    if (typeof error === "string") return error;
    if (error === null) return "null";
    if (typeof error === "undefined") return "undefined";
    if (typeof error === "number" || typeof error === "boolean" || typeof error === "bigint") return `${error}`;
    if (typeof error === "symbol") return "A symbol was thrown";

    const value = error as { name?: unknown; message?: unknown; stack?: unknown };
    const message = value.message;
    if (typeof message !== "string") return UNPRINTABLE_PLUGIN_ERROR;
    if (!includeStack) return message;
    const name = value.name;
    const detail = typeof name === "string" && name ? `${name}: ${message}` : message;
    const stack = includeStack ? value.stack : undefined;
    return typeof stack === "string" && stack ? `${detail}\n${stack}` : detail;
  } catch {
    return UNPRINTABLE_PLUGIN_ERROR;
  }
}

/** Explain the surface that failed without exposing renderer-provided text. */
export function pluginRenderFailureMessage(renderer: string): string {
  const surface = renderer.startsWith("tool:")
    ? "tool card renderer"
    : renderer.startsWith("tag:")
      ? "inline tag renderer"
      : renderer.startsWith("view:")
        ? "chat view renderer"
        : renderer.startsWith("page-tab:")
          ? "page tab renderer"
          : renderer.startsWith("modal-tab:")
            ? "modal tab renderer"
            : renderer === "settings"
              ? "settings renderer"
              : "renderer";
  return `This plugin's ${surface} failed to render. Check the browser console for details. Sensitive values are redacted.`;
}

/** Keep credential-shaped values out of the browser log while retaining useful failure context. */
export function redactPluginError(error: unknown): string {
  return describePluginError(error, true)
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
