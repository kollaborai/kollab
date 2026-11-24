import { describe, expect, it } from "vitest";
import { normalizePluginEntry, normalizePluginUses, safePluginText } from "./loader";
import {
  describePluginAdds, installDismissalMessage, normalizeReviewJob, pluginReviewOmissionSummary,
} from "./install";

const CONTRIBUTION_KINDS = ["menu", "views", "pages", "modals", "slash", "renderers"] as const;

function contributionItems(kind: typeof CONTRIBUTION_KINDS[number], count: number): unknown[] {
  return Array.from({ length: count }, (_, index) => kind === "menu"
    ? { id: `row-${index}`, label: `Row ${index}`, open: { page: "main" } }
    : `declaration-${index}`);
}

const inventoryOmissionCases: Array<{
  name: string;
  input: () => Record<string, unknown>;
  omitted: (entry: ReturnType<typeof normalizePluginEntry>) => number;
}> = [
  {
    name: "services",
    input: () => ({ services: Array.from({ length: 129 }, (_, index) => ({ name: `worker-${index}` })) }),
    omitted: (entry) => entry?.omitted.services ?? 0,
  },
  {
    name: "plugin uses",
    input: () => ({ uses: Array.from({ length: 257 }, (_, index) => `custom:cap-${index}`) }),
    omitted: (entry) => entry?.omitted.uses ?? 0,
  },
  {
    name: "granted capabilities",
    input: () => ({ granted: Array.from({ length: 257 }, (_, index) => `custom:cap-${index}`) }),
    omitted: (entry) => entry?.omitted.granted ?? 0,
  },
  {
    name: "service secrets",
    input: () => ({ services: [{ name: "worker", secrets: Array.from({ length: 65 }, (_, index) => `ENV_${index}`) }] }),
    omitted: (entry) => entry?.services.reduce((total, service) => total + service.secrets_omitted, 0) ?? 0,
  },
  {
    name: "settings",
    input: () => ({ settings: Array.from({ length: 257 }, (_, index) => ({
      type: "checkbox", path: `plugins.example.field-${index}`, label: `Field ${index}`,
    })) }),
    omitted: (entry) => entry?.omitted.settings ?? 0,
  },
  {
    name: "settings section widgets",
    input: () => ({ settings_section: { widgets: Array.from({ length: 257 }, (_, index) => ({
      type: "checkbox", path: `plugins.example.field-${index}`, label: `Field ${index}`,
    })) } }),
    omitted: (entry) => entry?.omitted.settings ?? 0,
  },
  {
    name: "dropdown options",
    input: () => ({ settings: [{
      type: "dropdown", path: "plugins.example.mode", label: "Mode",
      options: Array.from({ length: 257 }, (_, index) => `choice-${index}`),
    }] }),
    omitted: (entry) => entry?.omitted.setting_options ?? 0,
  },
  {
    name: "option labels",
    input: () => ({ settings: [{
      type: "dropdown", path: "plugins.example.mode", label: "Mode",
      option_labels: Object.fromEntries(Array.from({ length: 257 }, (_, index) => [`choice-${index}`, `Choice ${index}`])),
    }] }),
    omitted: (entry) => entry?.omitted.option_labels ?? 0,
  },
  ...CONTRIBUTION_KINDS.map((kind) => ({
    name: `contributes.${kind}`,
    input: () => ({ contributes: { [kind]: contributionItems(kind, 257) } }),
    omitted: (entry: ReturnType<typeof normalizePluginEntry>) => entry?.omitted.contributions ?? 0,
  })),
];

const reviewOmissionCases: Array<{
  name: string;
  job: () => Record<string, unknown>;
  expected: string;
}> = [
  {
    name: "services",
    job: () => ({ manifest: { services: Object.fromEntries(Array.from({ length: 129 }, (_, index) => [`worker-${index}`, {}])) } }),
    expected: "1 additional service omitted from this review",
  },
  {
    name: "plugin uses",
    job: () => ({ manifest: { uses: Array.from({ length: 257 }, (_, index) => `custom:cap-${index}`) } }),
    expected: "1 additional use declaration omitted from this review",
  },
  {
    name: "service secrets",
    job: () => ({ manifest: { services: { worker: { secrets: Array.from({ length: 65 }, (_, index) => `ENV_${index}`) } } } }),
    expected: "1 additional service secret omitted from this review",
  },
  ...CONTRIBUTION_KINDS.map((kind) => ({
    name: `contributes.${kind}`,
    job: () => ({ manifest: { contributes: { [kind]: contributionItems(kind, 257) } } }),
    expected: "1 additional contribution omitted from this review",
  })),
  {
    name: "changed folders",
    job: () => ({ action: "update", changed_folders: Array.from({ length: 257 }, (_, index) => `component-${index}`) }),
    expected: "1 additional changed folder omitted from this review",
  },
];

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
    })?.settings?.[0]?.options ?? [],
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

  it.each(inventoryOmissionCases)("reports omitted items for $name", ({ input, omitted }) => {
    const entry = normalizePluginEntry({ id: "example-plugin", ...input() });

    expect(omitted(entry)).toBe(1);
    expect(entry && omitted(normalizePluginEntry(entry))).toBe(1);
  });

  it.each(reviewOmissionCases)("discloses an incomplete $name review and blocks approval", ({ job: makeJob, expected }) => {
    const job = normalizeReviewJob({ job_id: "review-1", status: "ready", ...makeJob() });

    expect(job?.review_complete).toBe(false);
    expect(pluginReviewOmissionSummary(job!)).toContain(expected);
  });

  it.each(["token-counter", "password-manager"])("keeps valid plugin id %s", (id) => {
    expect(normalizePluginEntry({ id })).not.toBeNull();
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

  it.each([
    ["services", { services: Object.fromEntries(Array.from({ length: 129 }, (_, index) => [`worker-${index}`, {}])) }, "1 additional service omitted from this review"],
    ["uses", { uses: Array.from({ length: 257 }, (_, index) => `custom:cap-${index}`) }, "1 additional use declaration omitted from this review"],
    ["service secrets", { services: { worker: { secrets: Array.from({ length: 65 }, (_, index) => `ENV_${index}`) } } }, "1 additional service secret omitted from this review"],
  ] as const)("discloses omitted %s in the review summary", (_name, manifest, expected) => {
    const job = normalizeReviewJob({ job_id: "review-1", status: "ready", manifest });

    expect(job?.manifest).toBeDefined();
    expect(describePluginAdds(job!.manifest!)).toContain(expected);
  });

  it("reports omissions in the installed inventory row", () => {
    const entry = normalizePluginEntry({
      id: "example-plugin",
      services: [
        { name: "worker", secrets: Array.from({ length: 65 }, (_, index) => `ENV_${index}`) },
        ...Array.from({ length: 128 }, (_, index) => ({ name: `worker-${index}`, secrets: [] })),
      ],
      uses: Array.from({ length: 257 }, (_, index) => `custom:cap-${index}`),
      granted: Array.from({ length: 257 }, (_, index) => `custom:cap-${index}`),
    });

    expect(entry?.services).toHaveLength(128);
    expect(entry?.services[0].secrets_omitted).toBe(1);
    expect(entry?.omitted).toEqual({
      services: 1, uses: 1, granted: 1, settings: 0, setting_options: 0, option_labels: 0, contributions: 0,
    });
  });

  it("bounds setting option labels", () => {
    const labels = Object.fromEntries(Array.from({ length: 1000 }, (_, index) => [`choice-${index}`, `Label ${index}`]));
    const entry = normalizePluginEntry({
      id: "example-plugin",
      settings: [{ type: "dropdown", path: "plugins.example.mode", label: "Mode", option_labels: labels }],
    });

    expect(Object.keys(entry?.settings?.[0]?.option_labels ?? {})).toHaveLength(256);
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

  it.each([
    "run:codex\u200b",
    "net:github.com\u202eevil.test",
    "password\u200c=hunter2",
    "password\u200d=hunter2",
    "pass\u00adword=hunter2",
    "password\u2060=hunter2",
    "password\ufeff=hunter2",
  ])(
    "rejects invisible controls before review text is rendered: %s",
    (value) => expect(safePluginText(value)).toBeNull(),
  );

  it.each([
    ["installing", null, "Plugin installation is in progress. Wait for it to finish."],
    ["installing", "Plugin review no longer exists.", null],
    ["ready", null, null],
    ["complete", null, null],
  ] as const)("explains whether dismissing a %s job is blocked", (status, error, expected) => {
    expect(installDismissalMessage(status, error)).toBe(expected);
  });
});
