import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { WhatsNewDialog } from "../src/whats-new/WhatsNewDialog";
import {
  decideWhatsNew,
  STORAGE_KEY,
  type DialogRelease,
} from "../src/whats-new/whats-new-model";

vi.mock("@/components/ui/dialog", async () => {
  const React = await import("react");
  return {
    Dialog: ({ children }: { children: React.ReactNode }) =>
      React.createElement("div", null, children),
    DialogContent: ({ className, children, showCloseButton = true }: { className?: string; children: React.ReactNode; showCloseButton?: boolean }) =>
      React.createElement("div", { className, "data-testid": "dialog-content", "data-show-close-button": showCloseButton }, children),
    DialogTitle: ({ children }: { children: React.ReactNode }) =>
      React.createElement("h3", null, children),
    DialogDescription: ({ children }: { children: React.ReactNode }) =>
      React.createElement("p", null, children),
  };
});

const release: DialogRelease = {
  version: "0.22.1",
  previous: "0.22.0",
  added: ["First feature.", "Second feature."],
  changed: ["A changed flow."],
  fixedCount: 3,
};

const noop = () => {};

function findElement(
  node: unknown,
  matches: (props: Record<string, unknown>) => boolean,
): { props: Record<string, unknown> } | null {
  if (Array.isArray(node)) {
    for (const child of node) {
      const found = findElement(child, matches);
      if (found) return found;
    }
    return null;
  }
  if (typeof node !== "object" || node === null || !("props" in node)) return null;
  const element = node as { props: Record<string, unknown> };
  if (matches(element.props)) return element;
  return findElement(element.props.children, matches);
}

const storedValues = new Map<string, string>();
const browserStorage = {
  getItem: (key: string) => storedValues.get(key) ?? null,
  setItem: (key: string, value: string) => storedValues.set(key, value),
};

function dialogTree(onOpenChange = vi.fn(), onSeeAllChanges = vi.fn()) {
  vi.stubGlobal("window", { localStorage: browserStorage });
  storedValues.clear();
  return {
    tree: WhatsNewDialog({ open: true, release, onOpenChange, onSeeAllChanges }),
    onOpenChange,
    onSeeAllChanges,
  };
}

function expectAnnouncementSeen() {
  const decision = decideWhatsNew({
    result: {
      status: "ready",
      sections: [{ version: release.version, groups: [{ category: "Added", items: ["Feature"] }] }],
    },
    seenRaw: browserStorage.getItem(STORAGE_KEY),
    firstRun: false,
  });
  expect(decision.show).toBe(false);
}

describe("WhatsNewDialog", () => {
  it("renders nothing when closed or without a release", () => {
    expect(renderToStaticMarkup(<WhatsNewDialog open={false} release={release} onOpenChange={noop} onSeeAllChanges={noop} />)).toBe("");
    expect(renderToStaticMarkup(<WhatsNewDialog open release={null} onOpenChange={noop} onSeeAllChanges={noop} />)).toBe("");
  });

  it("titles the release, sublines the previous one, and lists entries", () => {
    const html = renderToStaticMarkup(
      <WhatsNewDialog open release={release} onOpenChange={noop} onSeeAllChanges={noop} />,
    );
    expect(html).toContain("What’s New in 0.22.1");
    expect(html).toContain("Updated from 0.22.0.");
    expect(html).toContain("First feature.");
    expect(html).toContain("A changed flow.");
  });

  it("shows Added and Changed section labels", () => {
    const html = renderToStaticMarkup(
      <WhatsNewDialog open release={release} onOpenChange={noop} onSeeAllChanges={noop} />,
    );
    expect(html).toContain("Added");
    expect(html).toContain("Changed");
  });

  it("renders at most six Added and Changed entries combined", () => {
    const html = renderToStaticMarkup(
      <WhatsNewDialog
        open
        release={{
          ...release,
          added: ["Added 1", "Added 2", "Added 3", "Added 4"],
          changed: ["Changed 1", "Changed 2", "Changed 3", "Changed 4"],
        }}
        onOpenChange={noop}
        onSeeAllChanges={noop}
      />,
    );
    expect((html.match(/<li\b/g) ?? [])).toHaveLength(6);
    expect(html).toContain("And 2 more.");
  });

  it("keeps fixes to a count, never a list", () => {
    const html = renderToStaticMarkup(
      <WhatsNewDialog open release={release} onOpenChange={noop} onSeeAllChanges={noop} />,
    );
    expect(html).toContain("Plus 3 fixes.");
  });

  it("omits the fixes line when the release fixes nothing", () => {
    const html = renderToStaticMarkup(
      <WhatsNewDialog open release={{ ...release, fixedCount: 0 }} onOpenChange={noop} onSeeAllChanges={noop} />,
    );
    expect(html).not.toContain("Plus");
  });

  it("hides the subline when no previous version is known", () => {
    const html = renderToStaticMarkup(
      <WhatsNewDialog open release={{ ...release, previous: null }} onOpenChange={noop} onSeeAllChanges={noop} />,
    );
    expect(html).not.toContain("Updated from");
  });

  it("exposes a labelled close button and the two footer actions", () => {
    const html = renderToStaticMarkup(
      <WhatsNewDialog open release={release} onOpenChange={noop} onSeeAllChanges={noop} />,
    );
    expect(html).toContain('aria-label="Close What’s New"');
    expect(html).toContain('data-show-close-button="false"');
    expect(html).toContain("Got It");
    expect(html).toContain("See All Changes");
  });

  it.each([
    ["Got It", "whats-new-got-it"],
    ["the header X", "Close What’s New"],
  ])("records the version when %s is activated and suppresses it after reload", (_name, action) => {
    const { tree, onOpenChange } = dialogTree();
    const button = findElement(tree, (props) =>
      action === "whats-new-got-it"
        ? props["data-testid"] === action
        : props["aria-label"] === action,
    );

    expect(button).not.toBeNull();
    (button?.props.onClick as (() => void) | undefined)?.();

    expect(browserStorage.getItem(STORAGE_KEY)).toBe('{"seenVersion":"0.22.1"}');
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expectAnnouncementSeen();
    vi.unstubAllGlobals();
  });

  it("records the version when the dialog reports Escape or outside dismissal", () => {
    const { tree, onOpenChange } = dialogTree();
    const dialog = tree as { props: { onOpenChange: (open: boolean) => void } };

    dialog.props.onOpenChange(false);

    expect(browserStorage.getItem(STORAGE_KEY)).toBe('{"seenVersion":"0.22.1"}');
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expectAnnouncementSeen();
    vi.unstubAllGlobals();
  });

  it("records the version and opens the changelog through See All Changes", () => {
    const { tree, onOpenChange, onSeeAllChanges } = dialogTree();
    const button = findElement(tree, (props) => props.children === "See All Changes");

    expect(button).not.toBeNull();
    (button?.props.onClick as (() => void) | undefined)?.();

    expect(browserStorage.getItem(STORAGE_KEY)).toBe('{"seenVersion":"0.22.1"}');
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(onSeeAllChanges).toHaveBeenCalledOnce();
    expectAnnouncementSeen();
    vi.unstubAllGlobals();
  });
});
