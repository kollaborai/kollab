// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

const streamMocks = vi.hoisted(() => ({ getChatStreamContent: vi.fn() }));
vi.mock("@/api", () => ({
  CHAT_STREAM_TOOL_OUTPUT_CHUNK_SIZE: 32768,
  EngineApi: class {
    getChatStreamContent = streamMocks.getChatStreamContent;
  },
}));

import {
  PermissionPromptData,
  PermissionPromptDataUI,
  PermissionPrompt,
  PermissionResponseContext,
  type PermissionArgs,
  type PermissionResponseHandler,
} from "../src/components/PermissionTool";
import type { ChatRow } from "../src/chat-stream.ts";
import { pendingPermissions } from "../src/utils/permission-cancel.ts";
import { visibleRecoveredPermissionPrompts } from "../src/permission-preview-state.ts";

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

// Matches the data part written by ChatStreamService._on_permission_prompt:
// raw AttachPermissionBridge details, not a request_permission tool call.
const DAEMON_PROMPT_DATA: PermissionArgs & {
  request_id: string;
  source: string;
  parked: boolean;
  resolution?: { type: string; outcome?: string; scope?: string };
} = {
  tool_id: "daemon-tool-7",
  permission_id: "daemon-perm-9",
  request_id: "daemon-parent-1",
  source: "acp",
  parked: false,
  tool_name: "terminal",
  tool_type: "shell",
  risk_level: "high",
  command: "pytest -q",
  acp_options: [
    { optionId: "allow-once", name: "Allow once", kind: "allow_once" },
    { optionId: "reject-once", name: "Deny", kind: "reject_once" },
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

  it("loads full externalized request_permission args before showing actions and Stop ownership", async () => {
    streamMocks.getChatStreamContent.mockReset();
    const ownerSummary = {
      tool_id: "daemon-tool-args",
      request_id: "daemon-parent-args",
      permission_id: "daemon-perm-args",
      source: "acp",
      parked: false,
    };
    const fullArgs = {
      ...ownerSummary,
      tool_name: "terminal",
      tool_type: "shell",
      command: `pytest -q ${"critical/path/authorization/".repeat(400)}`,
      acp_options: [{ optionId: "allow", name: "Allow", kind: "allow_once" }],
    };
    const content = JSON.stringify(fullArgs);
    const marker = {
      kind: "chat-stream-content-preview" as const,
      session_id: "session-args",
      row_id: "row-args",
      part_index: 0,
      field: "args" as const,
      output_bytes: new TextEncoder().encode(content).length,
      output_chars: Array.from(content).length,
      output_type: "json" as const,
      output_revision: "a".repeat(64),
      preview: content.slice(0, 1024),
    };
    streamMocks.getChatStreamContent.mockResolvedValueOnce({
      row_id: marker.row_id,
      part_index: marker.part_index,
      field: marker.field,
      output_revision: marker.output_revision,
      offset: 0,
      next_offset: marker.output_chars,
      total_chars: marker.output_chars,
      output_type: marker.output_type,
      content,
      done: true,
    });
    const row: ChatRow = {
      id: marker.row_id,
      seq: 9,
      kind: "tool",
      role: "assistant",
      turn_id: "turn-args",
      status: "requires-action",
      parts: [{
        type: "tool-call",
        tool_call_id: "call-args",
        tool_name: "request_permission",
        args: ownerSummary,
        chat_stream_args_preview: marker,
      }],
      created_at: "2026-11-25T00:00:00Z",
    };
    const respond = vi.fn<PermissionResponseHandler>(async () => "answered");
    const props = partProps({
      toolCallId: "call-args",
      args: ownerSummary,
      providerMetadata: { kollab: { chat_stream_args_preview: marker } },
    });
    const container = renderPrompt(props, respond);

    expect(buttons(container).some((button) => button.textContent?.includes("Allow"))).toBe(false);
    expect(pendingPermissions([row], visibleRecoveredPermissionPrompts([row], marker.session_id))).toEqual([]);
    expect(streamMocks.getChatStreamContent).toHaveBeenCalledWith(
      marker.session_id,
      marker.row_id,
      marker.part_index,
      "args",
      marker.output_revision,
      { offset: 0, limit: 32768 },
    );

    await vi.waitFor(() => expect(container.textContent).toContain("critical/path/authorization/"));
    expect(container.querySelector("details[open]")?.textContent).toContain(fullArgs.command);
    expect(visibleRecoveredPermissionPrompts([row], marker.session_id)).toMatchObject([{
      sessionId: marker.session_id,
      rowId: marker.row_id,
      revision: marker.output_revision,
      partIndex: marker.part_index,
      promptId: "call-args",
      partType: "tool-call",
      data: fullArgs,
    }]);
    expect(pendingPermissions([row], visibleRecoveredPermissionPrompts([row], marker.session_id))).toEqual([{
      toolId: ownerSummary.tool_id,
      requestId: ownerSummary.request_id,
      source: ownerSummary.source,
      parked: false,
      previewKey: `session-args\u0000call-args\u0000${marker.output_revision}`,
    }]);
    await click(container, "Allow");
    expect(respond).toHaveBeenCalledWith(
      "call-args",
      expect.objectContaining(fullArgs),
      expect.objectContaining({ tool_id: ownerSummary.tool_id, permission_id: ownerSummary.permission_id }),
    );

    const details = container.querySelector("details");
    expect(details).not.toBeNull();
    await act(async () => {
      details!.open = false;
      details!.dispatchEvent(new Event("toggle"));
    });
    expect(visibleRecoveredPermissionPrompts([row], marker.session_id)).toEqual([]);
    expect(pendingPermissions([row], visibleRecoveredPermissionPrompts([row], marker.session_id))).toEqual([]);
  });

  it("fails closed when args preview metadata is present but malformed", () => {
    const respond = vi.fn<PermissionResponseHandler>(async () => "answered");
    const container = renderPrompt(partProps({
      providerMetadata: { kollab: { chat_stream_args_preview: { kind: "wrong-marker" } } },
    }), respond);

    expect(container.textContent).toContain("could not be verified");
    expect(buttons(container)).toEqual([]);
  });
});

describe("permission prompt data from the daemon chat-stream row", () => {
  it("renders the producer-shaped prompt and answers through the shared responder", async () => {
    const respond = vi.fn<PermissionResponseHandler>(async () => "answered");
    const container = renderDataPrompt("daemon-perm-9", DAEMON_PROMPT_DATA, respond);

    expect(container.textContent).toContain("Permission required: terminal");
    expect(container.textContent).toContain("high risk");
    expect(container.textContent).toContain("pytest -q");
    expect(PermissionPromptDataUI.unstable_data.name).toBe("prompt");
    await click(container, "Allow once");

    expect(respond).toHaveBeenCalledTimes(1);
    expect(respond).toHaveBeenCalledWith(
      "daemon-perm-9",
      expect.objectContaining(DAEMON_PROMPT_DATA),
      expect.objectContaining({
        tool_id: "daemon-tool-7",
        permission_id: "daemon-perm-9",
        decision: "approve",
        option_id: "allow-once",
      }),
    );
  });

  it("renders resolved cancellation without live decision buttons", () => {
    const respond = vi.fn<PermissionResponseHandler>(async () => "answered");
    const container = renderDataPrompt("daemon-perm-9", {
      ...DAEMON_PROMPT_DATA,
      resolution: { type: "permission_denied", outcome: "cancelled" },
    }, respond);

    expect(container.textContent).toContain("Cancelled");
    expect(buttons(container)).toHaveLength(0);
  });

  it("renders a daemon grant as resolved and hides decision buttons", () => {
    const respond = vi.fn<PermissionResponseHandler>(async () => "answered");
    const container = renderDataPrompt("daemon-perm-9", {
      ...DAEMON_PROMPT_DATA,
      resolution: { type: "permission_granted", scope: "session" },
    }, respond);

    expect(container.textContent).toContain("Allowed (session)");
    expect(buttons(container)).toHaveLength(0);
  });

  it("requires the complete recovered permission part to be visible before answering", async () => {
    streamMocks.getChatStreamContent.mockReset();
    let finish!: (chunk: unknown) => void;
    streamMocks.getChatStreamContent.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    const completeData = {
      ...DAEMON_PROMPT_DATA,
      command: `pytest -q ${"tests/authorization-path/".repeat(300)}`,
    };
    const completePart = JSON.stringify({ type: "prompt", prompt_id: "daemon-perm-9", data: completeData });
    const marker = {
      kind: "chat-stream-part-preview",
      session_id: "session-preview",
      row_id: "row-preview",
      part_index: 2,
      output_bytes: new TextEncoder().encode(completePart).length,
      output_chars: Array.from(completePart).length,
      output_type: "json",
      output_revision: "f".repeat(64),
      preview: completePart.slice(0, 1024),
    };
    const respond = vi.fn<PermissionResponseHandler>(async () => "answered");
    const container = renderDataPrompt("daemon-perm-9", {
      kind: "kollab-chat-stream-permission-prompt-preview",
      marker,
    }, respond);

    expect(buttons(container).map((button) => button.textContent?.trim())).not.toContain("Allow once");
    expect(buttons(container).map((button) => button.textContent?.trim())).not.toContain("Deny");
    expect(streamMocks.getChatStreamContent).toHaveBeenCalledWith(
      "session-preview", "row-preview", 2, "part", marker.output_revision,
      { offset: 0, limit: 32768 },
    );

    await act(async () => {
      finish({
        row_id: marker.row_id,
        part_index: marker.part_index,
        field: "part",
        output_revision: marker.output_revision,
        offset: 0,
        next_offset: marker.output_chars,
        total_chars: marker.output_chars,
        output_type: "json",
        content: completePart,
        done: true,
      });
      await Promise.resolve();
    });
    await vi.waitFor(() => expect(container.textContent).toContain("authorization-path/"));
    const fullPrompt = container.querySelector("details[open]");
    expect(fullPrompt?.textContent).toContain(completeData.command);
    await click(container, "Allow once");
    expect(respond).toHaveBeenCalledWith(
      "daemon-perm-9",
      expect.objectContaining({ command: completeData.command, request_id: completeData.request_id }),
      expect.objectContaining({ tool_id: completeData.tool_id, permission_id: completeData.permission_id, decision: "approve" }),
    );
  });

  it("does not show decision actions when recovered prompt identity differs", async () => {
    streamMocks.getChatStreamContent.mockReset();
    const wrongPart = JSON.stringify({ type: "prompt", prompt_id: "different-prompt", data: DAEMON_PROMPT_DATA });
    const marker = {
      kind: "chat-stream-part-preview",
      session_id: "session-preview",
      row_id: "row-preview",
      part_index: 2,
      output_bytes: new TextEncoder().encode(wrongPart).length,
      output_chars: Array.from(wrongPart).length,
      output_type: "json",
      output_revision: "e".repeat(64),
      preview: wrongPart.slice(0, 64),
    };
    streamMocks.getChatStreamContent.mockResolvedValueOnce({
      row_id: marker.row_id,
      part_index: marker.part_index,
      field: "part",
      output_revision: marker.output_revision,
      offset: 0,
      next_offset: marker.output_chars,
      total_chars: marker.output_chars,
      output_type: "json",
      content: wrongPart,
      done: true,
    });
    const respond = vi.fn<PermissionResponseHandler>(async () => "answered");
    const container = renderDataPrompt("daemon-perm-9", {
      kind: "kollab-chat-stream-permission-prompt-preview",
      marker,
    }, respond);

    await vi.waitFor(() => expect(container.textContent).toContain("could not be verified"));
    expect(buttons(container).some((button) => /Allow|Deny/.test(button.textContent ?? ""))).toBe(false);
    expect(respond).not.toHaveBeenCalled();
  });
});

function renderDataPrompt(
  id: string,
  data: unknown,
  respond: PermissionResponseHandler,
): HTMLElement {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <PermissionResponseContext.Provider value={respond}>
        <PermissionPromptData id={id} data={data} />
      </PermissionResponseContext.Provider>,
    );
  });
  mounted = { root, container };
  return container;
}
