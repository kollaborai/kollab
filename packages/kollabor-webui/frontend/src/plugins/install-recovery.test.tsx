// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PluginInstallDialog } from "./install";

const jobId = "recovered-job";
const commit = "a".repeat(40);
const filesHash = "b".repeat(64);
const serviceWarning = "Plugin installed, but its services could not be started; check their status";

const job = (status: "ready" | "complete", warning: string | null = null) => ({
  job_id: jobId,
  action: "update",
  status,
  plugin_id: "hello",
  commit,
  files_sha256: filesHash,
  source: "https://example.invalid/plugin.git",
  changed_folders: ["content.txt"],
  omitted_changed_folders: 0,
  manifest: { id: "hello", name: "Hello", uses: [], services: {} },
  warning,
});

afterEach(() => vi.unstubAllGlobals());

describe("plugin install response recovery", () => {
  it.each([
    ["without a warning", null, true],
    ["with a warning", serviceWarning, false],
  ] as const)(
    "recovers a completed update after a lost confirm response %s",
    async (_label, warning, closes) => {
      vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
      const requests: string[] = [];
      const api = {
        json: async (path: string) => {
          requests.push(path);
          if (path === "/plugins/hello/update") return job("ready");
          if (path === `/plugins/jobs/${jobId}/confirm`) throw new Error("response lost");
          if (path === `/plugins/jobs/${jobId}`) return job("complete", warning);
          throw new Error(`unexpected request ${path}`);
        },
      };
      const onClose = vi.fn();
      const onApplied = vi.fn(async () => {});
      const host = document.createElement("div");
      document.body.append(host);
      const root = createRoot(host);

      try {
        await act(async () => {
          root.render(<PluginInstallDialog
            api={api as never}
            action="update"
            pluginId="hello"
            onClose={onClose}
            onApplied={onApplied}
            onRestoreFocus={() => {}}
          />);
          await new Promise((resolve) => setTimeout(resolve, 0));
        });

        const update = [...document.querySelectorAll("button")]
          .find((button) => button.textContent?.trim() === "Update");
        expect(update).toBeDefined();
        await act(async () => {
          update?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
          await new Promise((resolve) => setTimeout(resolve, 0));
        });

        expect(requests).toContain(`/plugins/jobs/${jobId}/confirm`);
        expect(requests).toContain(`/plugins/jobs/${jobId}`);
        expect(onApplied).toHaveBeenCalledTimes(1);
        expect(onClose).toHaveBeenCalledTimes(closes ? 1 : 0);
        expect(document.body.textContent).toContain("Plugin updated.");
        if (warning) expect(document.body.textContent).toContain(warning);
      } finally {
        await act(async () => root.unmount());
        host.remove();
      }
    },
  );

  it.each([
    ["error", "Cannot stage plugin source; check the URL, ref, and plugin manifest"],
    ["cancelled", null],
  ] as const)("cleans staged files before retrying a failed review (%s)", async (status, error) => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const requests: { path: string; method?: string }[] = [];
    let reviewCount = 0;
    const api = {
      json: async (path: string, init?: { method?: string }) => {
        requests.push({ path, method: init?.method });
        if (path === "/plugins/hello/update") {
          reviewCount += 1;
          return {
            job_id: reviewCount === 1 ? "old-review" : "new-review",
            action: "update",
            status: reviewCount === 1 ? status : "error",
            plugin_id: "hello",
            error: reviewCount === 1 ? error : "Cannot stage plugin source; check the URL, ref, and plugin manifest",
          };
        }
        if (path === "/plugins/jobs/old-review" && init?.method === "DELETE") return { status: "cancelled" };
        throw new Error(`unexpected request ${path}`);
      },
    };
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    try {
      await act(async () => {
        root.render(<PluginInstallDialog
          api={api as never}
          action="update"
          pluginId="hello"
          onClose={() => {}}
          onApplied={async () => {}}
          onRestoreFocus={() => {}}
        />);
        await new Promise((resolve) => setTimeout(resolve, 0));
      });

      const retry = [...document.querySelectorAll("button")]
        .find((button) => button.textContent?.trim() === "Review Again");
      expect(retry).toBeDefined();
      await act(async () => {
        retry?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await new Promise((resolve) => setTimeout(resolve, 0));
      });

      expect(requests).toEqual([
        { path: "/plugins/hello/update", method: "POST" },
        { path: "/plugins/jobs/old-review", method: "DELETE" },
        { path: "/plugins/hello/update", method: "POST" },
      ]);
    } finally {
      await act(async () => root.unmount());
      host.remove();
    }
  });
});
