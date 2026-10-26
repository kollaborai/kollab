/// <reference types="vite/client" />

// Injected by vite.config.ts from the frontend package and exact source checkout.
declare const __APP_VERSION__: string;
declare const __KOLLAB_BUILD_BRANCH__: string;
declare const __KOLLAB_BUILD_SHA__: string;

declare module "virtual:kollab-changelog" {
  const markdown: string;
  export default markdown;
}
