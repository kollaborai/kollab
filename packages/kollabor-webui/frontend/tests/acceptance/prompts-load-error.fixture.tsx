import { createRoot } from "react-dom/client";
import type { PromptsApi } from "../../src/prompts-api";
import { PromptsPage } from "../../src/components/prompts/PromptsPage";
import "../../src/styles.css";
import {
  FIXTURE_COMPOSITION,
  FIXTURE_LIBRARIES,
  FIXTURE_ROLE_SECTIONS,
  FIXTURE_ROLES,
} from "../fixtures/prompts";

declare global {
  interface Window {
    promptsLoadLibrariesCalls: number;
  }
}

window.promptsLoadLibrariesCalls = 0;

const api = {
  listLibraries: async () => {
    window.promptsLoadLibrariesCalls += 1;
    if (window.promptsLoadLibrariesCalls === 1) {
      throw new Error("Prompt library could not be loaded");
    }
    await new Promise((resolve) => window.setTimeout(resolve, 150));
    return { libraries: FIXTURE_LIBRARIES, roles: FIXTURE_ROLES };
  },
  listSections: async () => ({ sections: FIXTURE_ROLE_SECTIONS }),
  getComposition: async () => FIXTURE_COMPOSITION,
} as unknown as PromptsApi;

createRoot(document.getElementById("root")!).render(<PromptsPage api={api} />);
