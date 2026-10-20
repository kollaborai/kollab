import { createRoot } from "react-dom/client";
import { PromptsPage } from "../../src/components/prompts/PromptsPage";
import { PromptsApi } from "../../src/prompts-api";
import { fixtureTransport } from "../fixtures/prompts";
import "../../src/styles.css";

const fixture = fixtureTransport();
const api = new PromptsApi(fixture.transport);
const parameters = new URLSearchParams(window.location.search);
const originalGetSection = api.getSection.bind(api);
api.getSection = async (bundle, key) => {
  if (parameters.get("fail-section") === key) {
    throw new Error(`Simulated section load failure: ${key}`);
  }
  if (parameters.get("delay-section") === key) {
    const delayMs = Number(parameters.get("delay-ms") ?? 400);
    await new Promise((resolve) => setTimeout(resolve, delayMs));
  }
  return originalGetSection(bundle, key);
};
const originalPreview = api.preview.bind(api);

const originalSaveSection = api.saveSection.bind(api);
api.saveSection = async (bundle, key, content) => {
  if (parameters.get("delay-save") === "1") {
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  if (parameters.get("fail-save") === "1") {
    throw new Error("Simulated PUT failure: permission denied");
  }
  return originalSaveSection(bundle, key, content);
};
api.preview = async (bundle) => {
  if (parameters.get("fail-preview") === "1") {
    throw new Error("Simulated preview failure: engine unavailable");
  }
  return originalPreview(bundle);
};
const originalListSections = api.listSections.bind(api);
let listSectionsCalls = 0;
api.listSections = async (bundle) => {
  listSectionsCalls += 1;
  const failRefresh = parameters.get("fail-refresh");
  if ((failRefresh === "save" && listSectionsCalls === 2) ||
      (failRefresh === "remove" && listSectionsCalls === 3)) {
    throw new Error(`Simulated section refresh failure: ${failRefresh}`);
  }
  return originalListSections(bundle);
};
const originalDeleteOverride = api.deleteOverride.bind(api);
api.deleteOverride = async (bundle, key) => {
  if (parameters.get("delay-remove") === "1") {
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  if (parameters.get("fail-delete") === "1") {
    throw new Error("Simulated DELETE failure: permission denied");
  }
  return originalDeleteOverride(bundle, key);
};
document.documentElement.classList.add("dark");
createRoot(document.getElementById("root")!).render(
  <div className="flex h-screen min-h-0 flex-col">
    <PromptsPage api={api} />
  </div>,
);
Object.assign(window, { __systemPromptsFixture: fixture });
