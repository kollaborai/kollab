// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  PermissionPrompt,
  PermissionResponseContext,
  type PermissionArgs,
  type PermissionResponseHandler,
} from "../src/components/PermissionTool";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type PartProps = Parameters<typeof PermissionPrompt>[0];

const ARGS: PermissionArgs = {
  tool_id: "tool-7",
  permission_id: "perm-9",
  tool_name: "bash",
  tool_type: "shell",
  risk_level: "high",
  input: { command: "rm -rf build" },
};

const ACP_ARGS: PermissionArgs = {
  ...ARGS,
  acp_options: [
    { optionId: "opt-allow", name: "Allow", kind: "allow_once" },
    { optionId: "opt-deny", name: "Deny", kind: "reject_once" },
  ],
};

/** Tool props shaped like assistant-ui's, with an addResult spy. */
function partProps(overrides: Partial<PartProps> = {}): PartProps {
  return {
    type: "tool-call",
    toolCallId: "call-1",
    toolName: "request_permission",
    args: ARGS,
    argsText: JSON.stringify(ARGS),
    result: undefined,
    isError: false,
    status: { type: "requires-action", reason: "interrupt" },
    addResult: vi.fn(),
    resume: vi.fn(),
    ...overrides,
  } as PartProps;
}

let mounted: { root: Root; container: HTMLElement } | null = null;

function renderPrompt(props: PartProps, respond: PermissionResponseHandler): HTMLElement {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <PermissionResponseContext.Provider value={respond}>
        <PermissionPrompt {...props} />
      </PermissionResponseContext.Provider>,
    );
  });
  mounted = { root, container };
  return container;
}

afterEach(() => {
  if (!mounted) return;
  const { root, container } = mounted;
  act(() => root.unmount());
  container.remove();
  mounted = null;
});

function buttons(container: HTMLElement): HTMLButtonElement[] {
  return Array.from(container.querySelectorAll("button"));
}

async function click(container: HTMLElement, label: string): Promise<void> {
  const target = buttons(container).find((node) => node.textContent?.trim() === label);
  if (!target) throw new Error(`no button labelled "${label}"`);
  await act(async () => {
    target.click();
  });
}

describe("permission prompt outcome from the tool-call row", () => {
  it("allow click calls respond with the full decision body and never calls addResult", async () => {
    const respond = vi.fn<PermissionResponseHandler>(async () => "answered");
    const props = partProps();
    const container = renderPrompt(props, respond);

    await click(container, "Allow once");

    expect(respond).toHaveBeenCalledTimes(1);
    expect(respond).toHaveBeenCalledWith(
      "call-1",
      expect.objectContaining({ tool_id: "tool-7", permission_id: "perm-9" }),
      { tool_id: "tool-7", permission_id: "perm-9", decision: "approve", scope: "once" },
    );
    expect(props.addResult).not.toHaveBeenCalled();
  });

  it("an ACP option click sends its option_id in the same body", async () => {
    const respond = vi.fn<PermissionResponseHandler>(async () => "answered");
    const props = partProps({ args: ACP_ARGS, argsText: JSON.stringify(ACP_ARGS) });
    const container = renderPrompt(props, respond);

    await click(container, "Allow");

    expect(respond).toHaveBeenCalledTimes(1);
    expect(respond).toHaveBeenCalledWith(
      "call-1",
      expect.objectContaining({ tool_id: "tool-7" }),
      expect.objectContaining({
        tool_id: "tool-7",
        permission_id: "perm-9",
        decision: "approve",
        option_id: "opt-allow",
      }),
    );
    expect(props.addResult).not.toHaveBeenCalled();
  });

  it("a tool-call part with a result renders as decided and offers no option buttons", () => {
    const respond = vi.fn<PermissionResponseHandler>(async () => "answered");
    const container = renderPrompt(
      partProps({
        result: { decision: "approve", scope: "once", tool_id: "tool-7", permission_id: "perm-9" },
      }),
      respond,
    );

    expect(container.textContent).toContain("Allowed (once)");
    expect(buttons(container)).toEqual([]);
    expect(respond).not.toHaveBeenCalled();
  });

  it("a decided ACP prompt offers no option buttons", () => {
    const respond = vi.fn<PermissionResponseHandler>(async () => "answered");
    const container = renderPrompt(
      partProps({ args: ACP_ARGS, result: { decision: "deny", scope: "once" } }),
      respond,
    );

    expect(container.textContent).toContain("Denied");
    expect(buttons(container)).toEqual([]);
  });
});
