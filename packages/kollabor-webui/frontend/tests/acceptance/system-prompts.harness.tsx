import { createRoot } from "react-dom/client";
import { PromptsPage } from "../../src/components/prompts/PromptsPage";
import { PromptsApi } from "../../src/prompts-api";
import { fixtureTransport } from "../fixtures/prompts";
import "../../src/styles.css";

const fixture = fixtureTransport();
const api = new PromptsApi(fixture.transport);
if (new URLSearchParams(window.location.search).get("fail-delete") === "1") {
  api.deleteOverride = async () => {
    throw new Error("Simulated DELETE failure: permission denied");
  };
}
document.documentElement.classList.add("dark");
createRoot(document.getElementById("root")!).render(
  <div className="flex h-screen min-h-0 flex-col">
    <PromptsPage api={api} />
  </div>,
);
Object.assign(window, { __systemPromptsFixture: fixture });
