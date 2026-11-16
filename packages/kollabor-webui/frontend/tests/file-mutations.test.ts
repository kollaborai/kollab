import { beforeEach, expect, test, vi } from "vitest";
const fetchMock = vi.fn();
vi.mock("../src/lib/code-fetch", () => ({ codeFetch: (...args: unknown[]) => fetchMock(...args) }));
import { mutateFileTree } from "../src/components/code/file-mutations";
beforeEach(() => fetchMock.mockReset());
test.each(["create", "rename", "delete"])("%s reports a daemon refusal", async (action) => {
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ error: { message: "Read only is on." } }), { status: 403 }));
  await expect(mutateFileTree(action, {})).rejects.toThrow("Read only is on.");
});
test("a successful mutation resolves", async () => {
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ success: true }), { status: 200 }));
  await expect(mutateFileTree("create", {})).resolves.toBeUndefined();
});
