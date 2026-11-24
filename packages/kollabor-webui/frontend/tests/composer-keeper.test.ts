import { afterEach, describe, expect, it, vi } from "vitest";

const composer = vi.hoisted(() => ({
  text: "",
  setText: vi.fn(),
}));

vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  return {
    ...actual,
    useRef: <T,>(initialValue: T) => ({ current: initialValue }),
    useLayoutEffect: (effect: () => void) => effect(),
    useEffect: (effect: () => void) => effect(),
  };
});

vi.mock("@assistant-ui/react", () => ({
  useAui: () => ({
    composer: {
      getState: () => ({ text: composer.text }),
      setText: composer.setText,
    },
  }),
  useAuiState: (selector: (state: { composer: { text: string } }) => unknown) =>
    selector({ composer: { text: composer.text } }),
}));

import { ComposerKeeper, forgetComposerText } from "../src/components/assistant-ui/composer-keeper";

describe("composer keeper", () => {
  const deletedSessionId = "deleted-session-test";
  const reopenableSessionId = "reopenable-session-test";

  afterEach(() => {
    composer.text = "";
    composer.setText.mockClear();
    forgetComposerText(deletedSessionId);
    forgetComposerText(reopenableSessionId);
  });

  it("forgets a deleted session without dropping a reopenable session's draft", () => {
    composer.text = "draft from deleted session";
    ComposerKeeper({ sessionId: deletedSessionId });
    composer.text = "draft from reopenable session";
    ComposerKeeper({ sessionId: reopenableSessionId });

    forgetComposerText(deletedSessionId);
    composer.text = "";
    composer.setText.mockClear();
    ComposerKeeper({ sessionId: deletedSessionId });
    expect(composer.setText).not.toHaveBeenCalled();

    ComposerKeeper({ sessionId: reopenableSessionId });
    expect(composer.setText).toHaveBeenCalledWith("draft from reopenable session");
  });
});
