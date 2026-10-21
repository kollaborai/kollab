/// <reference types="vite/client" />

// Injected by vite.config.ts `define` from the frontend package.json version.
declare const __APP_VERSION__: string;

declare module "virtual:kollab-changelog" {
  const markdown: string;
  export default markdown;
}
