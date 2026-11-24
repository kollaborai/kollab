import { describe, expect, it } from "vitest";
import { normalizePluginEntry, normalizePluginUses, safePluginText } from "./loader";
import { describePluginAdds, installDismissalMessage, normalizeReviewJob } from "./install";

function trackedList(values: unknown[], limit: number) {
  let reads = 0;
  const read = () => {
    reads += 1;
    if (reads > limit) throw new Error(`read more than ${limit} array items`);
  };
  const list = new Proxy(values, {
    get(target, key, receiver) {
      if (key === "map" || key === "filter") {
        if (key === "map") return (callback: (value: unknown, index: number) => unknown) =>
          target.map((value, index) => { read(); return callback(value, index); });
        return (callback: (value: unknown, index: number) => unknown) =>
          target.filter((value, index) => { read(); return callback(value, index); });
      }
      if (key === "slice") {
        return (start = 0, end = target.length) => {
          const sliced = Array.prototype.slice.call(target, start, end);
          for (let index = 0; index < sliced.length; index += 1) read();
          return sliced;
        };
      }
      if (key === Symbol.iterator) {
        return function* () {
          for (const value of target) { read(); yield value; }
        };
      }
      if (typeof key === "string" && /^\d+$/.test(key)) read();
      return Reflect.get(target, key, receiver);
    },
  });
  return { list, reads: () => reads };
}

const boundedCases: Array<{
  name: string;
  limit: number;
  make: (index: number) => string;
  normalize: (items: unknown[]) => unknown[];
}> = [
  {
    name: "plugin uses",
    limit: 256,
    make: (index: number) => `net:host-${index}.example`,
    normalize: (items: unknown[]) => normalizePluginUses(items),
  },
  {
    name: "granted capabilities",
    limit: 256,
    make: (index: number) => `custom:cap-${index}`,
    normalize: (items: unknown[]) => normalizePluginEntry({ id: "example-plugin", granted: items })?.granted ?? [],
  },
  {
    name: "dropdown options",
    limit: 256,
    make: (index: number) => `choice-${index}`,
    normalize: (items: unknown[]) => normalizePluginEntry({
      id: "example-plugin",
      settings: [{ type: "dropdown", path: "plugins.example.mode", label: "Mode", options: items }],
    })?.settings[0]?.options ?? [],
  },
  {
    name: "service secrets",
    limit: 64,
    make: (index: number) => `ENV_${index}`,
    normalize: (items: unknown[]) => normalizePluginEntry({
      id: "example-plugin",
      services: [{ name: "worker", secrets: items }],
    })?.services[0]?.secrets ?? [],
  },
  {
    name: "staged service secrets",
    limit: 64,
    make: (index: number) => `ENV_${index}`,
    normalize: (items: unknown[]) => {
      const review = normalizeReviewJob({
        job_id: "review-1",
        status: "ready",
        manifest: { services: { worker: { secrets: items } } },
      });
      const services = review?.manifest?.services as Record<string, { secrets: string[] }> | undefined;
      return services?.worker?.secrets ?? [];
    },
  },
  {
    name: "changed folders",
    limit: 256,
    make: (index: number) => `folder_${index}`,
    normalize: (items: unknown[]) => normalizeReviewJob({
      job_id: "review-1", status: "ready", changed_folders: items,
    })?.changed_folders ?? [],
  },
] as const;

describe("plugin inventory collection bounds", () => {
  it.each(boundedCases)("bounds work for $name", ({ limit, make, normalize }) => {
    const input = trackedList(Array.from({ length: limit + 1000 }, (_, index) => make(index)), limit);

    const result = normalize(input.list);

    expect(result).toHaveLength(limit);
    expect(input.reads()).toBe(limit);
  });

  it("bounds staged service-property reads before copying values", () => {
    let reads = 0;
    const source = Object.fromEntries(Array.from({ length: 1000 }, (_, index) => [`worker-${index}`, {}]));
    const services = new Proxy(source, {
      get(target, key, receiver) {
        if (typeof key === "string" && Object.prototype.hasOwnProperty.call(target, key)) reads += 1;
        return Reflect.get(target, key, receiver);
      },
    });

    const job = normalizeReviewJob({ job_id: "review-1", status: "ready", manifest: { services } });
    const normalized = job?.manifest?.services as Record<string, unknown> | undefined;

    expect(Object.keys(normalized ?? {})).toHaveLength(128);
    expect(reads).toBe(128);
  });

  it("bounds setting option labels", () => {
    const labels = Object.fromEntries(Array.from({ length: 1000 }, (_, index) => [`choice-${index}`, `Label ${index}`]));
    const entry = normalizePluginEntry({
      id: "example-plugin",
      settings: [{ type: "dropdown", path: "plugins.example.mode", label: "Mode", option_labels: labels }],
    });

    expect(Object.keys(entry?.settings[0]?.option_labels ?? {})).toHaveLength(256);
  });

  it("counts only valid contributions and only declares a real web entry", () => {
    const job = normalizeReviewJob({
      job_id: "review-1",
      status: "ready",
      manifest: { contributes: { pages: [null, {}, "valid-page"] }, web: {} },
    });

    expect(job?.manifest).toBeDefined();
    expect(describePluginAdds(job!.manifest!)).toContain("1 page");
    expect(describePluginAdds(job!.manifest!)).not.toContain("3 pages");
    expect(describePluginAdds(job!.manifest!)).not.toContain("browser code");
  });

  it("accepts the same normalized web path from inventory and review", () => {
    const entry = normalizePluginEntry({
      id: "example-plugin",
      web: { entry: "./web/dist/index.js", hash: "a".repeat(64) },
    });
    const review = normalizeReviewJob({
      job_id: "review-1", status: "ready", manifest: { web: { entry: "./web/dist/index.js" } },
    });

    expect(entry?.web?.entry).toBe("web/dist/index.js");
    expect(describePluginAdds(review!.manifest!)).toContain("browser code");
  });

  it.each(["run:codex\u200b", "net:github.com\u202eevil.test"])(
    "rejects invisible controls before review text is rendered: %s",
    (value) => expect(safePluginText(value)).toBeNull(),
  );

  it.each([
    ["installing", "Plugin installation is in progress. Wait for it to finish."],
    ["ready", null],
    ["complete", null],
  ] as const)("explains whether dismissing a %s job is blocked", (status, expected) => {
    expect(installDismissalMessage(status)).toBe(expected);
  });
});
